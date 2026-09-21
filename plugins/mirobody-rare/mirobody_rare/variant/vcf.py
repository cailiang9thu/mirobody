"""VCF reading (plan §4.3 / §4.4 ①–③): stdlib gzip text, no pysam. A call passes when
FILTER is PASS or '.', the genotype carries a non-reference allele and, for the
candidate list, the five-tuple is in the local ClinVar P/LP table. Zygosity is decided
from the genotype plus the sample's sex on X/Y (hemizygous), never guessed."""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_NONREF = {"0/0", "0|0", "./.", ".", "0", "./0", "0/."}


@dataclass
class VariantCall:
    chrom: str
    pos: int
    ref: str
    alt: str
    gt: str
    zygosity: str                 # het | hom | hemi
    filter: str = "PASS"
    depth: int | None = None
    gq: int | None = None
    clinvar: dict | None = None   # ClinVarIndex.lookup() record
    inheritance: str = "unknown"  # de_novo | maternal | paternal | biparental | unknown
    parent_gt: dict = field(default_factory=dict)

    @property
    def gene(self) -> str:
        return (self.clinvar or {}).get("gene", "")

    @property
    def key(self) -> str:
        return f"{self.chrom}:{self.pos}:{self.ref}:{self.alt}"

    def to_dict(self) -> dict:
        cv = self.clinvar or {}
        return {"chrom": self.chrom, "pos": self.pos, "ref": self.ref, "alt": self.alt, "gt": self.gt,
                "zygosity": self.zygosity, "filter": self.filter, "gene": self.gene,
                "clnsig": cv.get("clnsig"), "review_status": cv.get("rev"), "stars": cv.get("stars"),
                "clinvar_vid": cv.get("vid"), "conditions": cv.get("dn", []), "consequence": cv.get("mc"),
                "inheritance": self.inheritance, "parent_gt": self.parent_gt}


def _open(path: str | Path):
    p = str(path)
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p, "rt", encoding="utf-8")


def _zygosity(gt: str, chrom: str, sex: str | None) -> str:
    alleles = gt.replace("|", "/").split("/")
    c = chrom.replace("chr", "")
    if sex == "M" and c in ("X", "Y") and len({a for a in alleles if a != "."}) == 1:
        return "hemi"
    if len(alleles) == 1:
        return "hemi"
    nonref = [a for a in alleles if a not in ("0", ".")]
    return "hom" if len(nonref) == len(alleles) and len(set(nonref)) == 1 else "het"


def _fmt_int(fmt: list[str], vals: list[str], key: str) -> int | None:
    if key in fmt:
        v = vals[fmt.index(key)]
        return int(v) if v.isdigit() else None
    return None


def read_header(path: str | Path) -> dict:
    """Reference build (from ##reference / ##contig assembly) and sample names."""
    ref, samples = "", []
    with _open(path) as fh:
        for line in fh:
            if line.startswith("##"):
                if "GRCh38" in line or "hg38" in line:
                    ref = ref or "GRCh38"
                elif "GRCh37" in line or "hg19" in line or "b37" in line:
                    ref = ref or "GRCh37"
                continue
            if line.startswith("#CHROM"):
                samples = line.rstrip("\n").split("\t")[9:]
            break
    return {"reference": ref, "samples": samples}


def read_candidates(path: str | Path, clinvar, sex: str | None = None, sample_ix: int = 0,
                    min_dp: int = 10, min_gq: int = 20) -> tuple[list[VariantCall], dict]:
    """Every PASS non-reference call whose five-tuple is P/LP in ClinVar, plus counts."""
    n = n_pass = n_nonref = 0
    out: list[VariantCall] = []
    pend: list[tuple[tuple, dict]] = []          # (five-tuple, call fields) — resolved in one lookup_many
    with _open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            n += 1
            c = line.rstrip("\n").split("\t")
            if len(c) < 10 + sample_ix:
                continue
            if c[6] not in ("PASS", "."):
                continue
            n_pass += 1
            fmt = c[8].split(":")
            vals = c[9 + sample_ix].split(":")
            gt = vals[0] if fmt and fmt[0] == "GT" else "."
            if gt in _NONREF:
                continue
            n_nonref += 1
            dp, gq = _fmt_int(fmt, vals, "DP"), _fmt_int(fmt, vals, "GQ")
            if (dp is not None and dp < min_dp) or (gq is not None and gq < min_gq):
                continue
            for alt in c[4].split(","):
                pend.append(((c[0].replace("chr", ""), int(c[1]), c[3], alt),
                             dict(gt=gt, zygosity=_zygosity(gt, c[0], sex), filter=c[6], depth=dp, gq=gq)))
    hits = clinvar.lookup_many([k for k, _ in pend])
    for k, f in pend:
        cv = hits.get(k)
        if cv:
            out.append(VariantCall(chrom=k[0], pos=k[1], ref=k[2], alt=k[3], clinvar=cv, **f))
    return out, {"n_records": n, "n_pass": n_pass, "n_nonref": n_nonref, "n_clinvar_plp": len(out)}


def read_genotypes_at(path: str | Path, keys: set[str], sample_ix: int = 0) -> dict[str, str]:
    """Genotype of the given five-tuples in another sample's VCF (a parent); missing = '0/0'
    is NOT assumed — absent keys stay absent so the caller can say 'not covered'."""
    out: dict[str, str] = {}
    if not keys:
        return out
    with _open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            c = line.rstrip("\n").split("\t")
            k = f"{c[0].replace('chr', '')}:{c[1]}:{c[3]}:{c[4]}"
            if k in keys and len(c) >= 10 + sample_ix:
                out[k] = c[9 + sample_ix].split(":")[0]
    return out
