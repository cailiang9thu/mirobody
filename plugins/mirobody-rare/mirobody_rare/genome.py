"""Layer 2 in the pipeline: attachment pointers → candidate variants → gene by evidence.

Input is the solver-visible pointer block (`prediction_context.attachments` in haenv, or
the `th_files` rows of an upload): file paths + sha256 per pedigree role and a PED file.
Output is `variants[]` shaped like `th_variant` + its ClinVar `th_variant_annotation`, and a
gene decision that names which evidence produced it. Nothing here reads the phenotype
truth; the phenotype side hands in HPO ids it coded itself.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ._config import load as load_cfg
from .pedigree import parse_ped, trio_inheritance
from .variant import VariantCall, get_clinvar, read_candidates, read_genotypes_at
from .variant.vcf import read_header

log = logging.getLogger(__name__)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class GenomeResult:
    available: bool = False
    reference: str = ""
    sex: str | None = None
    trio: bool = False
    variants: list[VariantCall] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    integrity: dict = field(default_factory=dict)   # role -> ok | sha_mismatch | missing
    pedigree: dict | None = None
    common: list[VariantCall] = field(default_factory=list)   # dropped by gnomAD popmax
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"available": self.available, "reference": self.reference, "sex": self.sex, "trio": self.trio,
                "counts": self.counts, "integrity": self.integrity, "pedigree": self.pedigree,
                "variants": [v.to_dict() for v in self.variants],
                "common_variants": [v.to_dict() for v in self.common], "notes": self.notes}


def filter_common(variants: list[VariantCall], max_af_popmax: float) -> tuple[list[VariantCall], list[VariantCall]]:
    """Split by gnomAD popmax: a P/LP call common in some population is not a rare-disease
    candidate. Unqueried / not-found variants are KEPT (absence of evidence)."""
    kept, common = [], []
    for v in variants:
        af = (v.gnomad or {}).get("af_popmax")
        (common if isinstance(af, (int, float)) and af > max_af_popmax else kept).append(v)
    return kept, common


def annotate_gnomad(variants: list[VariantCall]) -> None:
    """Fill `v.gnomad` for every candidate in one batched call; failures leave None."""
    from .variant.gnomad import get_client
    from .reference.sync import run
    client = get_client()
    if client is None or not variants:
        return
    keys = [(v.chrom, v.pos, v.ref, v.alt) for v in variants]
    try:
        got = run(client.lookup_many(keys))
    except Exception as e:                              # noqa: BLE001
        log.warning("[gnomad] unavailable: %s", e)
        return
    for v in variants:
        v.gnomad = got.get((v.chrom, v.pos, v.ref, v.alt))


def analyze(att: dict | None, sex_hint: str | None = None) -> GenomeResult:
    """`att` = {"genome": {"reference", "files": {role: {path, sha256}}, "ped": {path, sha256}}, ...}."""
    res = GenomeResult()
    gen = (att or {}).get("genome") or {}
    files = gen.get("files") or {}
    if not files.get("proband"):
        res.notes.append("no proband VCF attached; genome layer not applicable")
        return res
    cfg = load_cfg().get("variant", {})
    cv = get_clinvar()
    # integrity: the pointer's sha256 must match the bytes we open
    for role, f in files.items():
        p = Path(f["path"])
        if not p.exists():
            res.integrity[role] = "missing"
        elif f.get("sha256") and _sha256(p) != f["sha256"]:
            res.integrity[role] = "sha_mismatch"
        else:
            res.integrity[role] = "ok"
    if res.integrity.get("proband") != "ok":
        res.notes.append(f"proband VCF {res.integrity.get('proband')}; refusing to read")
        return res
    ped = gen.get("ped") or {}
    sex = sex_hint
    if ped.get("path") and Path(ped["path"]).exists():
        pg = parse_ped(ped["path"])
        pb = pg.proband()
        fam, rows = pg.to_rows()
        res.pedigree = {"family_id": fam["family_id"], "members": rows}
        if pb and pb.sex_code:
            if sex_hint and pb.sex_code != sex_hint:
                res.notes.append(f"PED sex {pb.sex_code} disagrees with profile {sex_hint}; using PED")
            sex = pb.sex_code
    res.sex = sex
    hdr = read_header(files["proband"]["path"])
    res.reference = gen.get("reference") or hdr["reference"]
    if res.reference and res.reference != "GRCh38":
        res.notes.append(f"reference {res.reference}: ClinVar table is GRCh38, lookups skipped")
        res.available = True
        return res
    cands, counts = read_candidates(files["proband"]["path"], cv, sex=sex,
                                    min_dp=int(cfg.get("min_dp", 10)), min_gq=int(cfg.get("min_gq", 20)))
    min_stars = int(cfg.get("min_stars", 1))
    cands = [c for c in cands if (c.clinvar or {}).get("stars", 0) >= min_stars]
    counts["n_after_stars"] = len(cands)
    parents = {r: files[r] for r in ("father", "mother") if files.get(r) and res.integrity.get(r) == "ok"}
    res.trio = len(parents) == 2
    if parents:
        keys = {c.key for c in cands}
        pgt = {r: read_genotypes_at(f["path"], keys) for r, f in parents.items()}
        for c in cands:
            c.parent_gt = {r: pgt[r].get(c.key) for r in parents}
            if res.trio:
                c.inheritance = trio_inheritance(c.parent_gt.get("father"), c.parent_gt.get("mother"))
    annotate_gnomad(cands)
    cands, common = filter_common(cands, float(cfg.get("max_af_popmax", 0.01)))
    counts["n_common_dropped"] = len(common)
    res.variants = cands
    res.common = common
    res.counts = counts
    res.available = True
    return res


def variant_diseases(v: VariantCall, disease) -> set[str]:
    """ORPHA codes the variant's ClinVar record links to (directly, or through its OMIM ids)."""
    out: set[str] = set()
    for tok in ((v.clinvar or {}).get("disdb") or "").replace("|", ",").split(","):
        if tok.startswith("Orphanet:"):
            out.add(tok.split(":", 1)[1])
        elif tok.startswith("OMIM:"):
            out |= disease.omim_to_orpha.get(tok.split(":", 1)[1], set())
    return out


def variant_phenotype_score(v: VariantCall, present_hpo: list[str], disease) -> float:
    """How well the disorders this variant is reported for explain the proband's terms
    (best-match IC over each linked ORPHA's annotation closure; 0 when none is annotated)."""
    pres = [c for c in (disease.hpo.canon(h) for h in present_hpo) if c]
    denom = sum(disease.ic_of(q) for q in pres)
    if denom <= 0:
        return 0.0
    best = 0.0
    for code in variant_diseases(v, disease):
        cl = disease.closure.get(code)
        if not cl:
            continue
        s = sum(disease._best_ic(q, cl) for q in pres) / denom
        best = max(best, s)
    return round(best, 4)


def choose_gene(genome: GenomeResult, present_hpo: list[str], dx_genes: list[str],
                gene_pheno, disease, hgnc) -> dict:
    """Gene from variant evidence. Precedence, each step explicit in `method`:
    1. a candidate variant's gene is among the phenotype-ranked diagnosis's causal genes;
    2. one candidate gene leads the others by `gene_margin` on the proband's HPO terms;
    3. otherwise null with the candidates listed (no guess)."""
    cfg = load_cfg().get("variant", {})
    margin = float(cfg.get("gene_margin", 0.1))
    genes = []
    for v in genome.variants:
        g = hgnc.canonical(v.gene) if v.gene else None
        if g and g not in genes:
            genes.append(g)
    if not genes:
        return {"symbol": None, "candidates": [], "method": "variant", "reason": "no_clinvar_plp_candidate"}
    dxg = {hgnc.canonical(g) or g for g in dx_genes}
    hit = [g for g in genes if g in dxg]
    if len(hit) == 1:
        return {"symbol": hit[0], "hgnc_id": hgnc.get(hit[0])["hgnc_id"], "candidates": genes,
                "method": "variant+diagnosis", "evidence": [v.key for v in genome.variants if hgnc.canonical(v.gene) == hit[0]]}
    pool = hit or genes
    # 2. the variant's own reported disorders vs the proband's phenotype: a carrier-state
    #    decoy in an unrelated recessive gene scores ~0 here; the causal variant does not
    by_gene: dict[str, float] = {}
    for v in genome.variants:
        g = hgnc.canonical(v.gene) if v.gene else None
        if g in pool:
            by_gene[g] = max(by_gene.get(g, 0.0), variant_phenotype_score(v, present_hpo, disease))
    scored = sorted(by_gene.items(), key=lambda x: (-x[1], x[0]))
    if scored and scored[0][1] > 0 and (len(scored) == 1 or scored[0][1] >= scored[1][1] * (1 + margin)):
        chosen = scored[0][0]
        return {"symbol": chosen, "hgnc_id": hgnc.get(chosen)["hgnc_id"], "candidates": genes, "ranked": scored,
                "method": "variant+phenotype", "evidence": [v.key for v in genome.variants if hgnc.canonical(v.gene) == chosen]}
    # 3. gene-level HPO annotations as the last evidence
    chosen, scored2 = gene_pheno.rank(pool, present_hpo, disease, margin=margin)
    if chosen:
        return {"symbol": chosen, "hgnc_id": hgnc.get(chosen)["hgnc_id"], "candidates": genes, "ranked": scored2,
                "method": "variant+gene_phenotype", "evidence": [v.key for v in genome.variants if hgnc.canonical(v.gene) == chosen]}
    return {"symbol": None, "candidates": genes, "ranked": scored or scored2, "method": "variant",
            "reason": "several_candidate_genes_no_clear_leader"}
