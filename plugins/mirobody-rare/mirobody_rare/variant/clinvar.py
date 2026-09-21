"""Local ClinVar table (plan §4.4 ③): the P/LP subset of the GRCh38 ClinVar VCF, keyed by
the normalized five-tuple, built once into `cache_dir`. Zero-cost filter that runs before
any external API; carries the review status so a single-submitter call is never read as
expert consensus."""

from __future__ import annotations

import gzip
import logging
import pickle
import time
from functools import lru_cache
from pathlib import Path

from .._config import cache_dir, load as load_cfg, ontology_dir

log = logging.getLogger(__name__)

INDEX_NAME = "clinvar_plp_grch38.pkl"
_KEEP = ("Pathogenic", "Likely_pathogenic", "Pathogenic/Likely_pathogenic")
_STARS = {"practice_guideline": 4, "reviewed_by_expert_panel": 3,
          "criteria_provided,_multiple_submitters,_no_conflicts": 2,
          "criteria_provided,_conflicting_classifications": 1,
          "criteria_provided,_single_submitter": 1}


def _info(s: str) -> dict[str, str]:
    out = {}
    for kv in s.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k] = v
    return out


def _source_vcf() -> Path:
    cfg = load_cfg().get("variant", {})
    p = Path(cfg.get("clinvar_vcf", ontology_dir() / "clinvar_GRCh38" / "clinvar.vcf.gz")).expanduser()
    return p


def build_index(src: Path | None = None, out: Path | None = None) -> Path:
    src = src or _source_vcf()
    out = out or (cache_dir() / INDEX_NAME)
    t0 = time.time()
    idx: dict[str, dict] = {}
    n = 0
    with gzip.open(src, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            n += 1
            c = line.rstrip("\n").split("\t", 8)
            info = _info(c[7])
            sig = info.get("CLNSIG", "")
            if not any(sig.startswith(k) for k in _KEEP):
                continue
            gene = info.get("GENEINFO", "").split("|")[0].split(":")[0]
            rev = info.get("CLNREVSTAT", "")
            idx[f"{c[0].replace('chr', '')}:{c[1]}:{c[3]}:{c[4]}"] = {
                "vid": c[2], "clnsig": sig, "rev": rev, "stars": _STARS.get(rev, 0), "gene": gene,
                "dn": info.get("CLNDN", "").replace("_", " ").split("|")[:4],
                "disdb": info.get("CLNDISDB", ""), "mc": info.get("MC", "").split("|")[-1]}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        pickle.dump({"records": idx, "meta": {"source": str(src), "n_source": n, "n_plp": len(idx),
                                               "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}}, fh, protocol=5)
    log.info("[clinvar] %d of %d records are P/LP -> %s in %.0fs", len(idx), n, out, time.time() - t0)
    return out


class ClinVarIndex:
    def __init__(self, path: Path | None = None):
        path = path or (cache_dir() / INDEX_NAME)
        if not path.exists():
            build_index(out=path)
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        self.records: dict[str, dict] = d["records"]
        self.meta: dict = d["meta"]
        self.version = f"clinvar_grch38_plp:{self.meta.get('built_at', '')[:10]}"
        log.info("[clinvar] index: %d P/LP records", len(self.records))

    def lookup(self, chrom: str, pos: int, ref: str, alt: str) -> dict | None:
        return self.records.get(f"{str(chrom).replace('chr', '')}:{pos}:{ref}:{alt}")

    def lookup_many(self, keys: list[tuple[str, int, str, str]]) -> dict[tuple, dict]:
        out = {}
        for k in keys:
            r = self.records.get(f"{str(k[0]).replace('chr', '')}:{k[1]}:{k[2]}:{k[3]}")
            if r:
                out[k] = r
        return out


class ClinVarPg:
    """Same surface over `ref_clinvar` (plan §16), shaped by the measured network:

    The test database is remote (≈500 ms round trip). Sending every VCF five-tuple to the
    server, even 150k per `unnest` batch, costs 8 s per batch and a trio case 30–70 s. So the
    key set stays local and the payload stays in the database: at start the process fetches
    the 349k `vkey` md5 digests (16 bytes each, ≈35 MB, cached on disk per `source_version`),
    `lookup_many` hashes the query tuples locally and asks the server only for the hits —
    one `WHERE vkey = ANY($1)` per call, a few hundred rows at most. Residency drops from
    351 MB (full records) to ≈35 MB; a lookup costs one round trip instead of dozens.
    """

    _COLS = ("r.chrom, r.pos, r.ref, r.alt, r.variation_id, r.clnsig, r.review_status, r.stars, r.gene_symbol, r.consequence,"
             " r.condition_names, r.disease_db")

    def __init__(self) -> None:
        from ..reference.sync import run
        from ..reference.db import get_pool
        self._run, self._pool = run, get_pool
        self.version = self._run(self._version())
        self.keys: set[bytes] = self._load_keys()
        log.info("[clinvar] pg backend, %s, %d local keys", self.version, len(self.keys))

    async def _version(self) -> str:
        pool = await self._pool()
        v = await pool.fetchval("SELECT source_version FROM ref_clinvar LIMIT 1")
        return v or "clinvar_grch38_plp:unloaded"

    async def _fetch_keys(self) -> bytes:
        pool = await self._pool()
        rows = await pool.fetch("SELECT vkey FROM ref_clinvar")
        return b"".join(bytes.fromhex(r["vkey"]) for r in rows)

    def _load_keys(self) -> set[bytes]:
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.version)
        cache = cache_dir() / f"clinvar_keys_{safe}.bin"
        if cache.exists():
            blob = cache.read_bytes()
        else:
            t0 = time.time()
            blob = self._run(self._fetch_keys())
            cache.write_bytes(blob)
            log.info("[clinvar] fetched %d keys from pg in %.1fs -> %s", len(blob) // 16, time.time() - t0, cache)
        return {blob[i:i + 16] for i in range(0, len(blob), 16)}

    @staticmethod
    def _digest(chrom, pos, ref, alt) -> bytes:
        import hashlib
        return hashlib.md5(f"{str(chrom).replace('chr', '')}:{int(pos)}:{ref}:{alt}".encode()).digest()

    @staticmethod
    def _rec(r) -> dict:
        return {"vid": r["variation_id"], "clnsig": r["clnsig"], "rev": r["review_status"] or "", "stars": r["stars"],
                "gene": r["gene_symbol"] or "", "dn": list(r["condition_names"] or []), "disdb": r["disease_db"] or "",
                "mc": r["consequence"] or ""}

    async def _fetch(self, digests: list[bytes]):
        pool = await self._pool()
        rows = await pool.fetch(f"SELECT {self._COLS} FROM ref_clinvar r WHERE r.vkey = ANY($1::text[])", [d.hex() for d in digests])
        return {(r["chrom"], r["pos"], r["ref"], r["alt"]): self._rec(r) for r in rows}

    def lookup_many(self, keys: list[tuple[str, int, str, str]]) -> dict[tuple, dict]:
        want = {}
        for k in keys:
            d = self._digest(*k)
            if d in self.keys:
                want.setdefault(d, k)
        if not want:
            return {}
        got: dict = {}
        ds = list(want)
        for i in range(0, len(ds), 2000):
            got.update(self._run(self._fetch(ds[i:i + 2000])))
        out = {}
        for d, k in want.items():
            norm = (str(k[0]).replace("chr", ""), int(k[1]), k[2], k[3])
            if norm in got:
                out[k] = got[norm]
        return out

    def lookup(self, chrom, pos, ref, alt) -> dict | None:
        return self.lookup_many([(chrom, int(pos), ref, alt)]).get((chrom, int(pos), ref, alt))


@lru_cache(maxsize=1)
def get_clinvar():
    if str(load_cfg().get("reference", {}).get("backend", "memory")) == "pg":
        return ClinVarPg()
    return ClinVarIndex()
