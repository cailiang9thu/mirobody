"""MCP tools for layer 1 (plan §8): registered through the `mirobody.tools` entry point.
Every answer carries the no-guess caveat (`genetic_service._ABSENCE_NOTE` lineage)."""

from __future__ import annotations

from .coding import NO_GUESS_NOTE, code_text
from .disease import get_adapter as get_disease
from .hpo import get_adapter as get_hpo

_ABSENCE_NOTE = "未编码 ≠ 阴性:词表没命中的症状仍可能存在。否定断言(polarity=absent)是一等信息,与缺行不同。"


class RareCodingService:
    __tools__ = ("resolve_hpo", "code_phenotypes", "rank_rare_diseases")

    async def resolve_hpo(self, term: str) -> dict:
        """Map one phenotype phrase (zh or en) to an HPO term, offline.

        Returns the term id, its label, how it was matched and up to five
        candidates; `resolved=false` means no vocabulary hit, not "no phenotype".
        """
        r = get_hpo().resolve(term)
        return {"term": term, "hpo_id": r.hpo_id or None, "label": r.label or None, "resolved": r.resolved,
                "ambiguous": r.ambiguous, "method": r.method, "score": r.score,
                "candidates": [{"hpo_id": c.hpo_id, "label": c.label, "kind": c.kind, "lang": c.lang} for c in r.candidates],
                "note": _ABSENCE_NOTE}

    async def code_phenotypes(self, text: str) -> dict:
        """Extract clinical assertions from free text and code each to HPO; rank ORPHA disorders.

        `text` is a case narrative (one or more lines). Each assertion keeps subject,
        polarity, onset and who asserted it; uncoded lines are listed under `abstained`.
        """
        res = code_text(text)
        d = res.to_solver()
        d["note"] = [NO_GUESS_NOTE, _ABSENCE_NOTE]
        return d

    async def rank_rare_diseases(self, present: list[str], absent: list[str] | None = None,
                                 family: list[str] | None = None, top_k: int = 5) -> dict:
        """Rank Orphanet disorders by HPO phenotype similarity (information content).

        `present`/`absent`/`family` are HPO ids. A rank is a retrieval, not a diagnosis:
        the score is a similarity, and the caveat says so.
        """
        hits = get_disease().rank(list(present), list(absent or []), list(family or []), top_k=top_k)
        return {"ranked": [{"orpha": h.orpha, "name": h.name, "score": h.score, "matched": list(h.matched),
                            "against": list(h.against), "genes": list(h.genes)} for h in hits],
                "note": "相似度排序不是诊断;基因列表来自 Orphanet 关联,未经变异证据支持。"}
