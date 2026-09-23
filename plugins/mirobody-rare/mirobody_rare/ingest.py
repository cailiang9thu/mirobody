"""Ingest jobs (plan §4.3, §5, §6.3): file on disk → rows through a `RareRepo`.

`ingest_vcf` is chromosome-sharded, ≤8 concurrent, and idempotent: a rerun first asks the
repo which (sample, chrom) shards already exist and only parses the missing ones
(`uq_th_variant_call` is the second line of defence). The whole raw call set is NOT
stored for a WES-scale file; what lands in `th_variant` is the local filter output
(PASS · DP · GQ · ClinVar P/LP), which is what the MVP promises (plan §4.4 ①–③)."""

from __future__ import annotations

import asyncio
import gzip
import logging
from collections.abc import Iterable
from pathlib import Path

from ._config import load as load_cfg
from .pedigree import parse_ped
from .signal import index_dicom_zip
from .variant import get_clinvar
from .variant.vcf import _NONREF, _fmt_int, _open, _zygosity, iter_records, read_header

log = logging.getLogger(__name__)

MAX_VCF_BYTES = 500 * 1024 * 1024        # plan §0.2: WES/panel only; WGS refused, not truncated


def _chroms_in(path: str | Path) -> list[str]:
    seen: list[str] = []
    with _open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            c = line.split("\t", 1)[0].replace("chr", "")
            if not seen or seen[-1] != c:
                if c not in seen:
                    seen.append(c)
    return seen


def _rows_for_chrom(path: str | Path, chrom: str, sample_id: int, user_id: str, sex: str | None,
                    clinvar, min_dp: int, min_gq: int, min_stars: int) -> tuple[list[dict], list[dict], int]:
    vrows, arows, n_raw = [], [], 0
    pend: list[tuple[tuple, dict]] = []
    if True:
        for c in iter_records(path, chrom):
            n_raw += 1
            if c[6] not in ("PASS", ".") or len(c) < 10:
                continue
            fmt, vals = c[8].split(":"), c[9].split(":")
            gt = vals[0] if fmt and fmt[0] == "GT" else "."
            if gt in _NONREF:
                continue
            dp, gq = _fmt_int(fmt, vals, "DP"), _fmt_int(fmt, vals, "GQ")
            if (dp is not None and dp < min_dp) or (gq is not None and gq < min_gq):
                continue
            for alt in c[4].split(","):
                pend.append(((chrom, int(c[1]), c[3], alt), dict(genotype=gt, zygosity=_zygosity(gt, c[0], sex), depth=dp, gq=gq, filter=c[6])))
    hits = clinvar.lookup_many([k for k, _ in pend])          # one round-trip per shard, never per call
    from .genome import annotate_gnomad
    from .variant.vcf import VariantCall
    calls = {k: VariantCall(chrom=k[0], pos=k[1], ref=k[2], alt=k[3], gt=f["genotype"], zygosity=f["zygosity"], clinvar=hits[k])
             for k, f in pend if hits.get(k) and hits[k].get("stars", 0) >= min_stars}
    annotate_gnomad(list(calls.values()))
    for k, f in pend:
        cv = hits.get(k)
        if not cv or cv.get("stars", 0) < min_stars:
            continue
        vrows.append({"sample_id": sample_id, "user_id": user_id, "chrom": k[0], "pos": k[1], "ref": k[2], "alt": k[3], **f,
                      "gene_symbol": cv.get("gene") or None, "consequence": cv.get("mc") or None,
                      "is_de_novo": None, "inheritance": "unknown"})
        arows.append({"_key": k, "source": "clinvar", "source_version": clinvar.version,
                      "clinical_significance": cv.get("clnsig"), "review_status": cv.get("rev"),
                      "condition_names": list(cv.get("dn") or [])})
        g = calls[k].gnomad
        if g and not g.get("not_found"):
            arows.append({"_key": k, "source": "gnomad", "source_version": g.get("source_version", "gnomad_r4"),
                          "af_global": g.get("af_global"), "af_popmax": g.get("af_popmax"), "popmax_pop": g.get("popmax_pop"),
                          "allele_count": g.get("allele_count")})
    return vrows, arows, n_raw


async def ingest_vcf(repo, user_id: str, path: str | Path, file_id: int | None = None, sex: str | None = None,
                     assay: str = "WES", sample_id: int | None = None, concurrency: int = 8, file_key: str | None = None) -> dict:
    p = Path(path)
    if p.stat().st_size > MAX_VCF_BYTES:
        raise ValueError(f"{p.name}: {p.stat().st_size >> 20} MB exceeds the MVP limit (WES/panel only; WGS is phase 2)")
    hdr = read_header(p)
    if hdr["reference"] != "GRCh38":
        raise ValueError(f"{p.name}: reference build {hdr['reference'] or 'unknown'}; only GRCh38 is accepted (no guessing)")
    cfg = load_cfg().get("variant", {})
    clinvar = get_clinvar()
    if sample_id is None:
        import hashlib
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        prior = await repo.sample_by_hash(user_id, sha)
        if prior:
            log.info("[ingest] %s already ingested for %s as sample %s; skip", p.name, user_id, prior["id"])
            return {"sample_id": prior["id"], "shards": 0, "skipped_shards": 0, "raw_calls": 0, "kept": 0, "status": "ready", "duplicate": True}
        # A FAILED sample with the same bytes is resumed, not duplicated: `written_chroms` makes
        # the shards already written skip. Only `failed` — a `parsing` one may be in flight in
        # another task, and resuming it would parse the same shards twice.
        failed = await repo.failed_sample_by_hash(user_id, sha)
        if failed:
            log.info("[ingest] %s resumes failed sample %s for %s", p.name, failed["id"], user_id)
            sample_id = int(failed["id"])
    if sample_id is None:
        sample_id = await repo.add_sample({"user_id": user_id, "file_id": file_id, "assay": assay, "reference": "GRCh38",
                                          "sample_label": (hdr["samples"] or [None])[0], "caller": None, "content_sha256": sha,
                                          "file_key": file_key})
    await repo.set_sample_status(sample_id, "parsing")
    done = await repo.written_chroms(sample_id)
    todo = [c for c in _chroms_in(p) if c not in done]
    log.info("[ingest] sample %s: %d shards, %d already written, %d to parse", sample_id, len(todo) + len(done), len(done), len(todo))
    sem = asyncio.Semaphore(concurrency)
    n_var = n_raw = 0

    async def one(chrom: str) -> tuple[str, int, int]:
        async with sem:
            vrows, arows, raw = await asyncio.to_thread(_rows_for_chrom, p, chrom, sample_id, user_id, sex, clinvar,
                                                        int(cfg.get("min_dp", 10)), int(cfg.get("min_gq", 20)), int(cfg.get("min_stars", 1)))
            n = await repo.add_variants(vrows)
            ann = []
            for a in arows:
                vid = await repo.variant_id(sample_id, *a["_key"])
                if vid:
                    ann.append({**{k: v for k, v in a.items() if k != "_key"}, "variant_id": vid})
            await repo.add_annotations(ann)
            return chrom, n, raw

    try:
        for fut in asyncio.as_completed([one(c) for c in todo]):
            chrom, n, raw = await fut
            n_var += n
            n_raw += raw
            log.debug("[ingest] sample %s chr%s: %d raw -> %d kept", sample_id, chrom, raw, n)
        total = len(await repo.variants(user_id, limit=10 ** 9))
        await repo.set_sample_status(sample_id, "ready", variant_count=total)
        return {"sample_id": sample_id, "shards": len(todo), "skipped_shards": len(done), "raw_calls": n_raw, "kept": n_var, "status": "ready"}
    except Exception:
        await repo.set_sample_status(sample_id, "failed")
        raise


#: What a retry may help with: the database or the network timing out or dropping. A wrong
#: reference build, an oversized file or a malformed header raise ValueError and are final.
def _transient() -> tuple[type[BaseException], ...]:
    out: list[type[BaseException]] = [TimeoutError, ConnectionError]
    try:
        import asyncpg
        out += [asyncpg.PostgresConnectionError, asyncpg.InterfaceError, asyncpg.TooManyConnectionsError]
    except (ImportError, AttributeError):
        pass
    return tuple(out)


async def ingest_vcf_retrying(repo, user_id: str, path: str | Path, attempts: int = 3, base_delay: float = 2.0, **kw) -> dict:
    """`ingest_vcf` with bounded retries on transient errors (exponential backoff). Each retry
    resumes the sample the failed attempt left `failed`, so no sample row is duplicated.
    2026-09-23: one of 20 concurrent roundtrip cases lost its mother's sample to a single
    asyncpg pool TimeoutError; with no retry the trio inheritance for that case went `unknown`."""
    from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
    async for attempt in AsyncRetrying(retry=retry_if_exception_type(_transient()), stop=stop_after_attempt(max(1, attempts)),
                                       wait=wait_exponential(multiplier=base_delay, max=60) if base_delay else (lambda _s: 0),
                                       reraise=True):
        with attempt:
            n = attempt.retry_state.attempt_number
            if n > 1:
                log.warning("[ingest] %s: retry %d/%d after a transient error", Path(path).name, n, attempts)
            return await ingest_vcf(repo, user_id, path, **kw)
    raise RuntimeError("unreachable")                     # pragma: no cover


async def ingest_ped(repo, path: str | Path, user_ids: dict[str, str] | None = None, owner_id: str | None = None) -> int:
    """PED → th_pedigree / th_pedigree_member, owned by `owner_id` (the uploader): family ids are
    lab-local, so two owners may import the same id. `user_ids` maps individual_id → account."""
    pg = parse_ped(path)
    fam, rows = pg.to_rows()
    for r in rows:
        r["user_id"] = (user_ids or {}).get(r["individual_id"])
    return await repo.upsert_pedigree(fam, rows, owner_id=owner_id)


async def ingest_dicom(repo, user_id: str, path: str | Path, file_id: int, residency: str = "CN") -> dict:
    idx = await asyncio.to_thread(index_dicom_zip, path)
    row = idx.to_row(user_id, file_id=file_id, residency=residency)
    row["id"] = await repo.add_signal(row)
    if idx.deid_status != "done":
        log.warning("[ingest] signal %s de-identification failed on tags %s; object hidden", file_id, idx.phi_tags)
    return {**row, "phi_tags": idx.phi_tags}
