"""Diagnosis refresh after new evidence lands.

The narrative text hook ranks disorders by phenotype only. Once a P/LP variant is on file
(VCF ingested before or after the narrative), the differential is walked again and the first
disorder whose disease-causing gene carries a candidate variant is written as a
`th_disease_code` candidate with source ``nlp+variant`` — the same promotion rule
`coding.apply_genome` applies inside the shim, so the DB and the shim agree.

Variants annotated gnomAD-common (het > 1 %, hom/hemi > 5 % continental popmax) are not
evidence, exactly as `genome.filter_common` treats them. Earlier candidates stay in place
(readers see the history); the promoted row is added once (idempotent on code+source).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

HET_CAP, REC_CAP = 0.01, 0.05


def _evidence_genes(variants: list[dict], hgnc) -> set[str]:
    genes: set[str] = set()
    for v in variants:
        ann = {a.get("source"): a for a in v.get("annotations", [])}
        sig = str((ann.get("clinvar") or {}).get("clinical_significance") or "")
        if "athogenic" not in sig or "onflict" in sig:
            continue
        af = (ann.get("gnomad") or {}).get("af_popmax")
        cap = REC_CAP if v.get("zygosity") in ("hom", "hemi") else HET_CAP
        if isinstance(af, (int, float)) and af > cap:
            continue
        g = hgnc.canonical(v.get("gene_symbol") or "") or v.get("gene_symbol")
        if g:
            genes.add(g)
    return genes


async def refresh_diagnosis(repo, user_id: str, file_id: int | None = None) -> dict:
    from .coding import _causal_first
    from .disease import get_adapter as get_disease
    from .gene.hgnc import get_hgnc
    variants = await repo.variants(user_id, limit=10 ** 6)
    if not variants:
        return {"promoted": None, "reason": "no variants"}
    hgnc = get_hgnc()
    vgenes = _evidence_genes(variants, hgnc)
    if not vgenes:
        return {"promoted": None, "reason": "no P/LP rare variant"}
    ph = await repo.phenotypes(user_id)
    present = [r["hpo_id"] for r in ph if not r.get("negated") and (r.get("subject") or "proband") == "proband"]
    absent = [r["hpo_id"] for r in ph if r.get("negated") and (r.get("subject") or "proband") == "proband"]
    family = [r["hpo_id"] for r in ph if (r.get("subject") or "proband") != "proband" and not r.get("negated")]
    if not present:
        return {"promoted": None, "reason": "no phenotypes"}
    from .gene.phenotype import get_gene_phenotypes
    dis, g2p = get_disease(), get_gene_phenotypes()
    for h in dis.rank(present, absent, family):
        # group disorders (e.g. ORPHA:70 proximal SMA) carry no Orphanet gene of their own:
        # fall back to the genes behind their OMIM ids, as the shim does for `gene.symbol`
        symbols = _causal_first(dis.genes_of(h.orpha)) or g2p.genes_for_omim(dis.b.omim.get(h.orpha.split(":", 1)[-1], []))
        causal = {hgnc.canonical(x) or x for x in symbols}
        linked = causal & vgenes
        if not linked:
            continue
        have = await repo.disease_codes(user_id)
        if any(d["code"] == h.orpha and d.get("source") == "nlp+variant" for d in have):
            return {"promoted": h.orpha, "reason": "already written"}
        nlp = [d for d in have if d.get("source") == "nlp"]
        if nlp and nlp[-1]["code"] == h.orpha:
            return {"promoted": h.orpha, "reason": "already the phenotype candidate"}   # nothing to re-rank
        await repo.add_disease_code({"user_id": user_id, "system": "ORPHA", "code": h.orpha, "label": h.name,
                                     "status": "candidate", "source": "nlp+variant", "source_text": "promoted_by=" + ",".join(sorted(linked)),
                                     "confidence": float(h.score), "file_id": file_id})
        log.info("[dx_refresh] user=%s promoted %s (%s) by %s", user_id, h.orpha, h.name, sorted(linked))
        return {"promoted": h.orpha, "genes": sorted(linked)}
    return {"promoted": None, "reason": "no differential entry carries a variant"}
