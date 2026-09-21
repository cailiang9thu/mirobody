"""The coding pipeline: ledger / text → assertions → HPO → ORPHA → gene.

This is the system under test for haenv's `rare_coding` pack. Output follows the
haenv solver contract (`assertions[] / diagnosis / gene / abstained[]`) and keeps
the plan §14.2 fields per assertion so the same record can be written to
`th_phenotype` (`source='nlp'`, `confidence`, `source_text`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ._config import load as load_cfg
from .assertion import Assertion, extract, extract_ledger
from .disease import DiseaseHit, get_adapter as get_disease
from .gene import get_hgnc
from .hpo import HpoResolution, get_adapter as get_hpo

log = logging.getLogger(__name__)

NO_GUESS_NOTE = ("编码只在词表命中时给出;`abstained` 里的条目不是阴性,是没编上。"
                 "`review=true` 的条目命中了多个同分术语,取了排序第一个,需人工确认。")


@dataclass
class CodedAssertion:
    evidence_id: str
    assertion: Assertion
    hpo: HpoResolution | None = None

    def to_solver(self) -> dict:
        a = self.assertion
        d = {"evidence_id": self.evidence_id, "text": a.text, "subject": a.subject, "subject_role": a.subject_role,
             "polarity": a.polarity, "kind": a.kind, "onset_text": a.onset_text, "asserted_by": a.asserted_by,
             "codes": {}, "confidence": None, "method": "", "review": False, "source": "nlp"}
        if self.hpo and self.hpo.resolved:
            d["codes"] = {"hpo": self.hpo.hpo_id}
            d["hpo_label"] = self.hpo.label
            d["confidence"] = self.hpo.score
            d["method"] = self.hpo.method
            d["review"] = bool(self.hpo.ambiguous)
        return d


@dataclass
class CodingResult:
    coded: list[CodedAssertion] = field(default_factory=list)
    abstained: list[dict] = field(default_factory=list)
    differential: list[DiseaseHit] = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)
    gene: dict = field(default_factory=dict)
    genome: dict | None = None
    signals: list[dict] = field(default_factory=list)
    present_hpo: list[str] = field(default_factory=list)

    def to_solver(self) -> dict:
        return {"assertions": [c.to_solver() for c in self.coded],
                "abstained": self.abstained,
                "diagnosis": self.diagnosis, "gene": self.gene,
                "variants": (self.genome or {}).get("variants", []),
                "signals": self.signals,
                "genome": {k: v for k, v in (self.genome or {}).items() if k != "variants"},
                "differential_codes": [{"orpha": h.orpha, "name": h.name, "score": h.score,
                                        "matched": list(h.matched), "against": list(h.against),
                                        "genes": list(h.genes)} for h in self.differential],
                "coding_note": NO_GUESS_NOTE}


def _causal_first(assocs: list[dict]) -> list[str]:
    """Orphanet association types: keep the disease-causing genes when there are any, so a
    modifier / susceptibility gene never competes with the causal one for `gene.symbol`."""
    causal = [g["symbol"] for g in assocs if str(g.get("assoc", "")).startswith("Disease-causing")]
    return causal or [g["symbol"] for g in assocs]


def code_assertions(pairs: list[tuple[str, Assertion]]) -> CodingResult:
    cfg = load_cfg()
    policy = str(cfg.get("hpo", {}).get("ambiguous", "flag"))
    hpo, dis, hgnc = get_hpo(), get_disease(), get_hgnc()
    res = CodingResult()
    cores = [a.core for _, a in pairs]
    resolutions = hpo.resolve_many(cores)
    present, absent, family = [], [], []
    for (ev, a), r in zip(pairs, resolutions):
        if a.kind in ("lab_value", "treatment"):
            res.abstained.append({"evidence_id": ev, "text": a.text, "reason": f"kind={a.kind}:routed_to_indicator_pipeline"})
            continue
        if not r.resolved or (r.ambiguous and policy == "abstain"):
            res.abstained.append({"evidence_id": ev, "text": a.text, "reason": "no_hpo_match" if not r.resolved else "ambiguous",
                                  "candidates": [c.hpo_id for c in r.candidates[:3]]})
            continue
        res.coded.append(CodedAssertion(ev, a, r))
        if a.polarity == "uncertain":
            continue
        if a.subject == "relative":
            family.append(r.hpo_id)
        elif a.polarity == "absent":
            absent.append(r.hpo_id)
        else:
            present.append(r.hpo_id)
    # a disease named outright in the text wins over the phenotype ranking
    named = [dis.resolve_name(a.core) for _, a in pairs if a.kind == "diagnosis_hypothesis" and a.polarity == "present"]
    named = [n for n in named if n.resolved and not n.ambiguous]
    res.differential = dis.rank(present, absent, family)
    top = res.differential[0] if res.differential else None
    if named:
        n = named[0]
        res.diagnosis = {"codes": {"orpha": n.orpha}, "name": n.name, "method": "name", "confidence": 0.9}
        genes = _causal_first(dis.genes_of(n.orpha))
    elif top:
        res.diagnosis = {"codes": {"orpha": top.orpha}, "name": top.name, "method": "phenotype",
                         "confidence": top.score, "supporting_hpo": list(top.matched)}
        genes = _causal_first(dis.genes_of(top.orpha))
    else:
        res.diagnosis = {"codes": {}, "name": None, "method": "", "confidence": 0.0}
        genes = []
    gene_method = "orpha_gene"
    if not genes and res.diagnosis.get("codes", {}).get("orpha"):
        # Orphanet hangs genes on subtypes; a group-level disorder (Gaucher disease, ORPHA:355)
        # has none. Fall back to the genes HPO annotates to the disorder's OMIM entries.
        from .gene.phenotype import get_gene_phenotypes
        code = res.diagnosis["codes"]["orpha"].split(":", 1)[-1]
        genes = get_gene_phenotypes().genes_for_omim(dis.b.omim.get(code, []))
        gene_method = "omim_gene"
    canon = list(dict.fromkeys(c for c in (hgnc.canonical(g) for g in genes) if c))
    if len(canon) == 1:
        res.gene = {"symbol": canon[0], "hgnc_id": hgnc.get(canon[0])["hgnc_id"], "candidates": canon, "method": gene_method}
    elif canon:
        # several genes cause the top disorder: rank them by their own HPO annotations against
        # the proband's terms; pick only a clear leader, else leave `symbol` null (no variant call here)
        from .gene.phenotype import get_gene_phenotypes
        chosen, scored = get_gene_phenotypes().rank(canon, present, dis)
        res.gene = {"symbol": chosen, "hgnc_id": hgnc.get(chosen)["hgnc_id"] if chosen else None,
                    "candidates": canon, "ranked": scored, "method": gene_method + "+phenotype",
                    **({} if chosen else {"reason": "multiple_causal_genes_no_clear_leader"})}
    else:
        res.gene = {"symbol": None, "candidates": [], "method": "", "reason": "no_gene_association"}
    res.present_hpo = present
    return res


def apply_genome(res: CodingResult, attachments: dict | None, sex_hint: str | None = None) -> CodingResult:
    """Layer 2 on top of a coded case: candidate variants, then the gene by variant evidence,
    then — if the variant gene names a disorder in the differential — promote that disorder."""
    from .gene.phenotype import get_gene_phenotypes
    from .genome import analyze, choose_gene
    dis, hgnc = get_disease(), get_hgnc()
    g = analyze(attachments, sex_hint=sex_hint)
    res.genome = g.to_dict()
    if not g.available or not g.variants:
        return res
    # diagnosis promotion: a differential entry whose causal genes carry a P/LP variant beats
    # a higher phenotype score without one (the variant is evidence the score never saw)
    from .genome import variant_diseases
    vgenes = {hgnc.canonical(v.gene) for v in g.variants if v.gene}
    vgenes.discard(None)
    vdis: dict[str, set[str]] = {}          # ORPHA code -> genes whose variant ClinVar links there
    for v in g.variants:
        for code in variant_diseases(v, dis):
            vdis.setdefault(code, set()).add(hgnc.canonical(v.gene) or v.gene)
    if res.diagnosis.get("method") == "phenotype":
        # walk the differential in phenotype order; the first disorder that a candidate variant
        # is reported for (ClinVar disease link) or whose causal gene carries one wins
        for h in res.differential:
            code = h.orpha.split(":", 1)[-1]
            causal = {hgnc.canonical(x) for x in _causal_first(dis.genes_of(h.orpha))}
            linked = vdis.get(code, set()) | (causal & vgenes)
            if linked:
                if h.orpha != res.diagnosis["codes"].get("orpha"):
                    res.diagnosis = {"codes": {"orpha": h.orpha}, "name": h.name, "method": "phenotype+variant",
                                     "confidence": h.score, "supporting_hpo": list(h.matched),
                                     "promoted_by": sorted(linked)}
                break
    dx_code = (res.diagnosis.get("codes") or {}).get("orpha", "")
    dx_linked = sorted(vdis.get(dx_code.split(":", 1)[-1], set())) if dx_code else []
    dx_genes = _causal_first(dis.genes_of(res.diagnosis["codes"]["orpha"])) if res.diagnosis.get("codes", {}).get("orpha") else []
    if not dx_genes and res.diagnosis.get("codes", {}).get("orpha"):
        code = res.diagnosis["codes"]["orpha"].split(":", 1)[-1]
        dx_genes = get_gene_phenotypes().genes_for_omim(dis.b.omim.get(code, []))
    res.gene = choose_gene(g, res.present_hpo, list(dict.fromkeys(dx_linked + list(dx_genes))), get_gene_phenotypes(), dis, hgnc)
    return res


def apply_signals(res: CodingResult, attachments: dict | None) -> CodingResult:
    """Layer 4 on a coded case: each imaging pointer → de-identified index record (sha256
    verified against the pointer; a mismatch or failed de-identification is reported as such,
    never indexed)."""
    from pathlib import Path
    from .signal import index_dicom_zip
    out = []
    for s in ((attachments or {}).get("imaging") or {}).get("series") or []:
        p = Path(s.get("path", ""))
        if not p.exists():
            out.append({"path": str(p), "deid_status": "missing"})
            continue
        idx = index_dicom_zip(p)
        d = idx.to_dict()
        if s.get("sha256") and idx.sha256 != s["sha256"]:
            d = {"path": str(p), "deid_status": "sha_mismatch"}
        out.append(d)
    res.signals = out
    return res


def code_ledger(ledger: list[dict]) -> CodingResult:
    return code_assertions(extract_ledger(ledger))


def code_text(text: str, section: str = "") -> CodingResult:
    """Free clinical text (a case document section): one assertion set, ids are `T-<n>`."""
    pairs = []
    for i, line in enumerate(l for l in text.replace("\r", "").split("\n") if l.strip()):
        for a in extract(line, section=section):
            pairs.append((f"T-{i:03d}", a))
    return code_assertions(pairs)
