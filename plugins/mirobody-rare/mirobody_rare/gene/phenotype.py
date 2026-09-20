"""Gene ← HPO annotations (`genes_to_phenotype.txt`), to rank the causal-gene candidates
of a disorder by phenotype overlap when the disorder has several. Evidence-based ranking,
not a guess: a gene is chosen only when it leads the runner-up by a clear margin."""

from __future__ import annotations

import logging
from functools import lru_cache

from .._config import ontology_dir

log = logging.getLogger(__name__)


class GenePhenotypes:
    def __init__(self, path=None):
        path = path or (ontology_dir() / "genes_to_phenotype.txt")
        self.terms: dict[str, set[str]] = {}
        self.by_disease: dict[str, set[str]] = {}       # "OMIM:230800" -> {gene symbols}
        with path.open(encoding="utf-8") as fh:
            head = {h: i for i, h in enumerate(fh.readline().rstrip("\n").split("\t"))}
            for line in fh:
                c = line.rstrip("\n").split("\t")
                if len(c) < len(head):
                    continue
                self.terms.setdefault(c[head["gene_symbol"]], set()).add(c[head["hpo_id"]])
                self.by_disease.setdefault(c[head["disease_id"]], set()).add(c[head["gene_symbol"]])
        log.info("[gene] phenotype annotations for %d genes", len(self.terms))

    def genes_for_omim(self, omim_ids: list[str]) -> list[str]:
        out: list[str] = []
        for o in omim_ids:
            for g in sorted(self.by_disease.get(f"OMIM:{o}", ())):
                if g not in out:
                    out.append(g)
        return out

    def rank(self, candidates: list[str], present: list[str], disease, margin: float = 0.1) -> tuple[str | None, list[tuple[str, float]]]:
        """Return (chosen_or_None, [(gene, score)...]). `disease` is the DiseaseAdapter (IC + ancestors)."""
        hpo = disease.hpo
        pres = [c for c in (hpo.canon(h) for h in present) if c]
        denom = sum(disease.ic_of(q) for q in pres)
        scored: list[tuple[str, float]] = []
        for g in candidates:
            ann = self.terms.get(g)
            if not ann or denom <= 0:
                scored.append((g, 0.0))
                continue
            cl: set[str] = set()
            for a in ann:
                c = hpo.canon(a)
                if c:
                    cl.add(c)
                    cl |= hpo.ancestors(c)
            fs = frozenset(cl)
            s = sum(disease._best_ic(q, fs) for q in pres) / denom
            scored.append((g, round(s, 4)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        if not scored or scored[0][1] <= 0:
            return None, scored
        if len(scored) == 1 or scored[0][1] >= scored[1][1] * (1 + margin):
            return scored[0][0], scored
        return None, scored


@lru_cache(maxsize=1)
def get_gene_phenotypes() -> GenePhenotypes:
    return GenePhenotypes()
