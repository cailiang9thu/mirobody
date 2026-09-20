"""`DiseaseAdapter`: disease-name mention → ORPHA, and phenotype set → ranked ORPHA (plan D8).

Ranking is information-content phenotype similarity (Phenomizer-style, asymmetric
query→disease best-match average), computed over the ORPHA annotations of
`phenotype.hpoa` with ancestors from the HPO bundle. Scoring is code, not a
model, so a rank is reproducible and explainable: every hit carries which query
terms it matched and which negated terms argued against it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache

from mirobody.lexical import normalize, word_tokens

from .._config import load as load_cfg
from ..hpo import HpoAdapter, get_adapter as get_hpo
from .orphanet import OrphaBundle, load_orpha

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiseaseHit:
    orpha: str                 # "ORPHA:nnn"
    name: str
    score: float
    matched: tuple[str, ...] = ()        # query HPO ids that contributed
    against: tuple[str, ...] = ()        # negated HPO ids the disease lists
    genes: tuple[str, ...] = ()
    method: str = "phenotype"            # phenotype | name


@dataclass(frozen=True)
class NameResolution:
    term: str
    orpha: str = ""
    name: str = ""
    resolved: bool = False
    ambiguous: bool = False
    method: str = ""


def _ic_table(b: OrphaBundle, hpo: HpoAdapter) -> tuple[dict[str, float], dict[str, frozenset[str]]]:
    """IC(t) = -log(#diseases annotated to t or a descendant / N); closure(D) = annotations ∪ ancestors."""
    closure: dict[str, frozenset[str]] = {}
    count: dict[str, int] = {}
    for code, rows in b.annots.items():
        s: set[str] = set()
        for hp, _ in rows:
            c = hpo.canon(hp)
            if not c:
                continue
            s.add(c)
            s |= hpo.ancestors(c)
        s.discard("HP:0000001")
        fs = frozenset(s)
        closure[code] = fs
        for t in fs:
            count[t] = count.get(t, 0) + 1
    n = max(len(closure), 1)
    ic = {t: -math.log(c / n) for t, c in count.items()}
    return ic, closure


class DiseaseAdapter:
    domain = "orpha"

    def __init__(self, bundle: OrphaBundle | None = None, hpo: HpoAdapter | None = None):
        cfg = load_cfg().get("disease", {})
        self.top_k = int(cfg.get("top_k", 5))
        self.absent_penalty = float(cfg.get("absent_penalty", 0.5))
        self.family_weight = float(cfg.get("family_weight", 0.5))
        self.b = bundle or load_orpha()
        self.hpo = hpo or get_hpo()
        self.ic, self.closure = _ic_table(self.b, self.hpo)
        usage: dict[str, int] = {}
        for rows in self.b.annots.values():
            for h, _ in rows:
                c = self.hpo.canon(h)
                if c:
                    usage[c] = usage.get(c, 0) + 1
        self.hpo.set_prior(usage)
        self.direct: dict[str, dict[str, float | None]] = {
            code: {self.hpo.canon(h) or h: f for h, f in rows} for code, rows in self.b.annots.items()}
        self._names: dict[str, list[str]] = {}
        for code, nm in self.b.name.items():
            self._names.setdefault(normalize(nm), []).append(code)
            for s in self.b.synonyms.get(code, ()):
                self._names.setdefault(normalize(s), []).append(code)
        self._name_keys = sorted((k for k in self._names if len(k) >= 4), key=len, reverse=True)
        log.info("[orpha] adapter: %d disorders, %d annotated, %d IC terms",
                 len(self.b.name), len(self.closure), len(self.ic))

    # ------------------------------------------------------------ names
    def resolve_name(self, term: str) -> NameResolution:
        key = normalize(term)
        if not key:
            return NameResolution(term=term)
        hits = self._names.get(key)
        method = "exact"
        if not hits:
            toks = " " + " ".join(word_tokens(key)) + " "
            for k in self._name_keys:
                if " " + k + " " in toks:
                    hits, method = self._names[k], "contains"
                    break
        if not hits:
            return NameResolution(term=term)
        code = sorted(hits)[0]
        return NameResolution(term=term, orpha=f"ORPHA:{code}", name=self.b.name[code], resolved=True,
                              ambiguous=len(set(hits)) > 1, method=method)

    # ------------------------------------------------------------ phenotype ranking
    def ic_of(self, q: str) -> float:
        """IC of the term, or of its most specific annotated ancestor when the term itself
        is annotated to no disorder (keeps every similarity in [0, 1])."""
        if q in self.ic:
            return self.ic[q]
        return max((self.ic.get(a, 0.0) for a in self.hpo.ancestors(q)), default=0.0)

    def _best_ic(self, q: str, cl: frozenset[str]) -> float:
        if q in cl:
            return self.ic.get(q, 0.0)
        best = 0.0
        for a in self.hpo.ancestors(q):
            if a in cl:
                v = self.ic.get(a, 0.0)
                if v > best:
                    best = v
        return best

    def rank(self, present: list[str], absent: list[str] = (), family: list[str] = (),
             top_k: int | None = None) -> list[DiseaseHit]:
        """Symmetric IC similarity: 0.7 · (query→disease best-match) + 0.3 · (disease→query, frequency-weighted).

        The second half is what separates two disorders that both list every query
        term: the one whose own feature set is mostly explained by the query ranks
        above the one that lists 200 features of which the query touched three.
        """
        pres = [c for c in (self.hpo.canon(h) for h in present) if c]
        fam = [c for c in (self.hpo.canon(h) for h in family) if c]
        neg = [c for c in (self.hpo.canon(h) for h in absent) if c]
        if not pres and not fam:
            return []
        denom = sum(self.ic_of(q) for q in pres) + self.family_weight * sum(self.ic_of(q) for q in fam)
        if denom <= 0:
            return []
        q_closure: set[str] = set()
        for q in pres + fam:
            q_closure.add(q)
            q_closure |= self.hpo.ancestors(q)
        q_closure.discard("HP:0000001")
        qc = frozenset(q_closure)
        out: list[DiseaseHit] = []
        for code, cl in self.closure.items():
            s, matched = 0.0, []
            for q in pres:
                v = self._best_ic(q, cl)
                if v > 0:
                    s += v
                    matched.append(q)
            for q in fam:
                v = self._best_ic(q, cl)
                if v > 0:
                    s += self.family_weight * v
                    matched.append(q)
            if s <= 0:
                continue
            fwd = s / denom
            d = self.direct.get(code, {})
            num = den = 0.0
            for a, f in d.items():
                w = f if isinstance(f, float) else 0.5
                ica = self.ic.get(a, 0.0)
                den += w * ica
                num += w * self._best_ic(a, qc)
            back = num / den if den > 0 else 0.0
            score = 0.7 * fwd + 0.3 * back
            against = []
            for q in neg:
                if q in d or any(x in d for x in self.hpo.ancestors(q)):
                    f = d.get(q)
                    w = f if isinstance(f, float) else 0.5
                    score -= self.absent_penalty * w * self.ic_of(q) / denom
                    against.append(q)
            out.append(DiseaseHit(orpha=f"ORPHA:{code}", name=self.b.name.get(code, ""),
                                  score=round(max(score, 0.0), 4), matched=tuple(matched),
                                  against=tuple(against),
                                  genes=tuple(g["symbol"] for g in self.b.genes.get(code, ()))))
        out.sort(key=lambda h: (-h.score, h.orpha))
        return out[: (top_k or self.top_k)]

    def genes_of(self, orpha: str) -> list[dict]:
        return list(self.b.genes.get(orpha.split(":", 1)[-1], ()))


@lru_cache(maxsize=1)
def get_adapter() -> DiseaseAdapter:
    return DiseaseAdapter()
