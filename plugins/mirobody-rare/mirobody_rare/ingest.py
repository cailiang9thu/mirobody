"""Ingest jobs (plan §4.3, §5, §6.3): file on disk → rows through a `RareRepo`.

`ingest_vcf` reads the file once and writes it chromosome by chromosome, and is idempotent:
a rerun first asks the repo which (sample, chrom) shards already exist and skips them
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


def _candidates(c: list[str], chrom: str, sex: str | None, min_dp: int, min_gq: int) -> list[tuple[tuple, dict]]:
    """One VCF record → the calls that pass the local filter (PASS · non-ref GT · DP · GQ), one per ALT."""
    if c[6] not in ("PASS", ".") or len(c) < 10:
        return []
    fmt, vals = c[8].split(":"), c[9].split(":")
    gt = vals[0] if fmt and fmt[0] == "GT" else "."
    if gt in _NONREF:
        return []
    dp, gq = _fmt_int(fmt, vals, "DP"), _fmt_int(fmt, vals, "GQ")
    if (dp is not None and dp < min_dp) or (gq is not None and gq < min_gq):
        return []
    return [((chrom, int(c[1]), c[3], alt), dict(genotype=gt, zygosity=_zygosity(gt, c[0], sex), depth=dp, gq=gq, filter=c[6]))
            for alt in c[4].split(",")]


def _scan(path: str | Path, skip: set[str], sex: str | None, min_dp: int, min_gq: int):
    """Yield (chrom, candidates, raw_calls) per chromosome, reading the file ONCE.

    The old per-shard scan decompressed the whole file for every chromosome (22 passes; 11.8 s
    each on a 156 MB GIAB VCF, 407 s end to end). A VCF is sorted by contig, so one pass can hand
    a chromosome over the moment the next one starts. With an index and pysam each contig is
    fetched directly instead. Chromosomes in `skip` (already written: a resumed sample) are
    passed over without parsing. A contig that comes back after another one started (an
    unsorted file) is yielded again; the writes are replay-safe, but a crash between the two
    blocks resumes past the second one, so the case is logged."""
    from .variant.vcf import _pysam, has_index
    if has_index(path) and _pysam() is not None:
        ps = _pysam()
        with ps.VariantFile(str(path)) as vf:
            contigs = list(vf.header.contigs)
        for contig in contigs:
            chrom = contig.replace("chr", "")
            if chrom in skip:
                continue
            pend, n = [], 0
            for c in iter_records(path, chrom):
                n += 1
                pend += _candidates(c, chrom, sex, min_dp, min_gq)
            if n:
                yield chrom, pend, n
        return
    cur, pend, n, emitted = None, [], 0, set()
    with _open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            chrom = line.split("\t", 1)[0].replace("chr", "")
            if chrom != cur:
                if cur is not None and cur not in skip:
                    yield cur, pend, n
                    emitted.add(cur)
                if chrom in emitted:
                    log.warning("[ingest] %s: chr%s appears again after other contigs (unsorted VCF)", Path(path).name, chrom)
                cur, pend, n = chrom, [], 0
            if chrom in skip:
                continue
            n += 1
            pend += _candidates(line.rstrip("\n").split("\t"), chrom, sex, min_dp, min_gq)
    if cur is not None and cur not in skip:
        yield cur, pend, n


def _annotate(pend: list[tuple[tuple, dict]], sample_id: int, user_id: str, clinvar, min_stars: int) -> tuple[list[dict], list[dict]]:
    """Candidates of one chromosome → th_variant rows + their annotation rows (ClinVar P/LP ≥ min_stars, gnomAD)."""
    vrows, arows = [], []
    hits = clinvar.lookup_many([k for k, _ in pend])          # one round-trip per chromosome, never per call
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
    return vrows, arows


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_DONE = object()


async def ingest_vcf(repo, user_id: str, path: str | Path, file_id: int | None = None, sex: str | None = None,
                     assay: str = "WES", sample_id: int | None = None, concurrency: int = 8, file_key: str | None = None) -> dict:
    """`concurrency` is kept for callers; parsing is one pass now (see `_scan`), and the database
    writes of chromosome k overlap the parsing of chromosome k+1. Everything that touches the
    whole file runs in a worker thread: the server's event loop never waits on it."""
    p = Path(path)
    if p.stat().st_size > MAX_VCF_BYTES:
        raise ValueError(f"{p.name}: {p.stat().st_size >> 20} MB exceeds the MVP limit (WES/panel only; WGS is phase 2)")
    hdr = await asyncio.to_thread(read_header, p)
    if hdr["reference"] != "GRCh38":
        raise ValueError(f"{p.name}: reference build {hdr['reference'] or 'unknown'}; only GRCh38 is accepted (no guessing)")
    cfg = load_cfg().get("variant", {})
    clinvar = await asyncio.to_thread(get_clinvar)          # first call builds / loads the index
    sha = None
    if sample_id is None:
        sha = await asyncio.to_thread(_sha256, p)
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
    min_dp, min_gq, min_stars = int(cfg.get("min_dp", 10)), int(cfg.get("min_gq", 20)), int(cfg.get("min_stars", 1))
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=2)                # parsed-but-unwritten chromosomes held in memory
    import threading
    stop = threading.Event()                                   # the writer gave up: stop parsing

    def produce() -> None:
        try:
            for chrom, pend, raw in _scan(p, done, sex, min_dp, min_gq):
                if stop.is_set():
                    return
                vrows, arows = _annotate(pend, sample_id, user_id, clinvar, min_stars)
                asyncio.run_coroutine_threadsafe(q.put((chrom, vrows, arows, raw)), loop).result()
        except BaseException as e:                             # noqa: BLE001 — handed to the writer, which raises it
            asyncio.run_coroutine_threadsafe(q.put(e), loop).result()
            return
        asyncio.run_coroutine_threadsafe(q.put(_DONE), loop).result()

    n_var = n_raw = shards = 0
    worker = loop.run_in_executor(None, produce)
    try:
        while (item := await q.get()) is not _DONE:
            if isinstance(item, BaseException):
                raise item
            chrom, vrows, arows, raw = item
            n = await repo.add_variant_rows(sample_id, vrows, arows)
            n_var, n_raw, shards = n_var + n, n_raw + raw, shards + 1
            log.debug("[ingest] sample %s chr%s: %d raw -> %d kept", sample_id, chrom, raw, n)
        total = await repo.count_variants(sample_id)
        await repo.set_sample_status(sample_id, "ready", variant_count=total)
        log.info("[ingest] sample %s: %d shards parsed, %d already written; %d raw calls -> %d kept", sample_id, shards, len(done), n_raw, n_var)
        return {"sample_id": sample_id, "shards": shards, "skipped_shards": len(done), "raw_calls": n_raw, "kept": n_var, "status": "ready"}
    except BaseException:
        await repo.set_sample_status(sample_id, "failed")
        raise
    finally:
        stop.set()
        while not worker.done():                               # never leave the producer blocked on a full queue
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
        await asyncio.gather(worker, return_exceptions=True)


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


async def ingest_ped(repo, path: str | Path, user_ids: dict[str, str] | None = None, owner_id: str | None = None,
                     file_key: str | None = None) -> int:
    """PED → th_pedigree / th_pedigree_member, owned by `owner_id` (the uploader): family ids are
    lab-local, so two owners may import the same id. `user_ids` maps individual_id → account."""
    pg = parse_ped(path)
    fam, rows = pg.to_rows()
    for r in rows:
        r["user_id"] = (user_ids or {}).get(r["individual_id"])
    return await repo.upsert_pedigree(fam, rows, owner_id=owner_id, file_key=file_key)


async def ingest_dicom(repo, user_id: str, path: str | Path, file_id: int, residency: str = "CN",
                       file_key: str | None = None) -> dict:
    idx = await asyncio.to_thread(index_dicom_zip, path)
    row = idx.to_row(user_id, file_id=file_id, residency=residency)
    row["file_key"] = file_key                      # what a file delete erases by (erase.py)
    row["id"] = await repo.add_signal(row)
    if idx.deid_status != "done":
        log.warning("[ingest] signal %s de-identification failed on tags %s; object hidden", file_id, idx.phi_tags)
    return {**row, "phi_tags": idx.phi_tags}
