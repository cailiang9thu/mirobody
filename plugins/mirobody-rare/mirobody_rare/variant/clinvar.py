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


@lru_cache(maxsize=1)
def get_clinvar() -> ClinVarIndex:
    return ClinVarIndex()
