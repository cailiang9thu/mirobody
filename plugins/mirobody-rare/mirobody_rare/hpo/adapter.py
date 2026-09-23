"""`HpoAdapter`: free-text phenotype phrase → HPO term (plan §3.3).

Offline, lexical, two steps in fixed precedence — exact key, then longest
contained label — and an explicit refusal when nothing matches or when the best
label belongs to more than one term. Folding reuses mirobody's own
`lexical.normalize` and `zh_fold.fold_to_hans` (library layer, numpy-only), so a
Traditional query and a full-width query find the same Simplified label.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from mirobody.lexical import normalize, word_tokens
from mirobody.zh_fold import fold_to_hans

from .._config import load as load_cfg
from .bundle import HpoBundle, load_bundle

log = logging.getLogger(__name__)

_KIND_W = {"label": 1.0, "EXACT": 0.95, "NARROW": 0.85, "BROAD": 0.8, "RELATED": 0.75}


def fold(text: str) -> str:
    return normalize(fold_to_hans(text or ""))


def _is_cjk(s: str) -> bool:
    return any(ord(ch) >= 0x2E80 for ch in s)


@dataclass(frozen=True)
class HpoCandidate:
    hpo_id: str
    label: str            # the label that matched, as shipped
    kind: str             # label | EXACT | NARROW | BROAD | RELATED
    lang: str             # zh | en
    score: float


@dataclass(frozen=True)
class HpoResolution:
    term: str
    hpo_id: str = ""
    label: str = ""                       # the term's display label (zh when available)
    resolved: bool = False
    ambiguous: bool = False               # best label maps to >1 term (or two labels tie)
    method: str = ""                      # exact | contains | ""
    score: float = 0.0
    candidates: tuple[HpoCandidate, ...] = ()

    @property
    def code(self) -> str:
        return self.hpo_id


class HpoAdapter:
    """Domain adapter for HPO, isolated from the FHIR `SYSTEMS` id space (plan §3.1)."""

    domain = "hpo"

    def __init__(self, bundle: HpoBundle | None = None):
        cfg = load_cfg().get("hpo", {})
        self.min_chars = int(cfg.get("min_label_chars", 2))
        self.b = bundle or load_bundle()
        self._exact: dict[str, list[HpoCandidate]] = {}
        for hp, name in self.b.name.items():
            self._add(fold(name), HpoCandidate(hp, name, "label", "en", 1.0))
            for s, scope in self.b.syn_en.get(hp, ()):
                self._add(fold(s), HpoCandidate(hp, s, scope, "en", _KIND_W[scope]))
            zh = self.b.zh.get(hp)
            if zh:
                self._add(fold(zh), HpoCandidate(hp, zh, "label", "zh", 1.0))
            for s in self.b.syn_zh.get(hp, ()):
                self._add(fold(s), HpoCandidate(hp, s, "EXACT", "zh", 0.95))
        # containment scan order: longest key first, so the most specific label wins
        self._keys_zh = sorted((k for k in self._exact if _is_cjk(k) and len(k) >= self.min_chars), key=len, reverse=True)
        self._keys_en = sorted((k for k in self._exact if not _is_cjk(k) and len(k) >= 3), key=len, reverse=True)
        self.prior: dict[str, int] = {}
        log.info("[hpo] adapter: %d terms, %d keys (%d zh)", len(self.b.name), len(self._exact), len(self._keys_zh))

    def set_prior(self, usage: dict[str, int]) -> None:
        """Usage counts (how many disorders annotate the term) — the tie-break between two
        terms that share a label. Set by the disease adapter once its tables are up."""
        self.prior = dict(usage)

    def _add(self, key: str, c: HpoCandidate) -> None:
        if not key:
            return
        lst = self._exact.setdefault(key, [])
        if all(x.hpo_id != c.hpo_id for x in lst):
            lst.append(c)

    # ------------------------------------------------------------------ API
    def canon(self, hp: str) -> str | None:
        return self.b.canon(hp)

    def ancestors(self, hp: str) -> frozenset[str]:
        return self.b.ancestors(hp)

    def label(self, hp: str) -> str:
        return self.b.label(hp)

    def resolve(self, term: str) -> HpoResolution:
        key = fold(term)
        if not key:
            return HpoResolution(term=term)
        hits = self._exact.get(key)
        if hits:
            return self._answer(term, hits, "exact", 1.0)
        # longest contained label; zh by substring, en by token boundary
        best: list[HpoCandidate] = []
        best_len = 0
        if _is_cjk(key):
            for k in self._keys_zh:
                if len(k) < best_len:
                    break
                if k in key:
                    best_len = len(k)
                    best.extend(self._exact[k])
        toks = word_tokens(key)
        if toks:
            joined = " " + " ".join(toks) + " "
            for k in self._keys_en:
                if len(k) < best_len:
                    break
                if " " + k + " " in joined:
                    best_len = len(k)
                    best.extend(self._exact[k])
        if not best:
            return HpoResolution(term=term)
        cover = best_len / max(len(key), 1)
        return self._answer(term, best, "contains", round(0.6 + 0.4 * cover, 4))

    def _answer(self, term: str, hits: list[HpoCandidate], method: str, base: float) -> HpoResolution:
        ranked = sorted(hits, key=lambda c: (-c.score, -self.prior.get(c.hpo_id, 0), c.hpo_id))
        top = ranked[0]
        ids = {c.hpo_id for c in ranked if c.score == top.score}
        amb = len(ids) > 1
        return HpoResolution(term=term, hpo_id=top.hpo_id, label=self.b.label(top.hpo_id), resolved=True,
                             ambiguous=amb, method=method, score=round(base * top.score, 4),
                             candidates=tuple(ranked[:5]))

    def _phenotype_index(self) -> dict[str, list[tuple[str, ...]]]:
        """First token -> the token tuples of every English key that starts with it, longest first.
        Only keys naming a Phenotypic abnormality term (HP:0000118) are targets: a modifier label
        (`Right`, `Onset`) must not consume the words of a sentence."""
        if getattr(self, "_pidx", None) is None:
            idx: dict[str, set[tuple[str, ...]]] = {}
            for k in self._keys_en:
                if any("HP:0000118" in self.b.ancestors(c.hpo_id) for c in self._exact[k]):
                    toks = tuple(word_tokens(k))
                    if toks:
                        idx.setdefault(toks[0], set()).add(toks)
            self._pidx = {t: sorted(v, key=len, reverse=True) for t, v in idx.items()}
        return self._pidx

    def find_all(self, text: str) -> list[tuple[int, int]]:
        """Char spans of the HPO labels / synonyms in English `text`: whole tokens, left to right,
        the longest key at each position, non-overlapping (dictionary concept recognition). This
        is what gives a sentence with three findings three targets."""
        import re
        idx = self._phenotype_index()
        toks = [(m.start(), m.end(), m.group().lower()) for m in re.finditer(r"[A-Za-z0-9]+", text)]
        words = [t for _, _, t in toks]
        out, i = [], 0
        while i < len(toks):
            n = next((len(k) for k in idx.get(words[i], ()) if tuple(words[i:i + len(k)]) == k), 0)
            if n:
                out.append((toks[i][0], toks[i + n - 1][1]))
                i += n
            else:
                i += 1
        return out

    def resolve_many(self, terms: list[str]) -> list[HpoResolution]:
        memo: dict[str, HpoResolution] = {}
        out = []
        for t in terms:
            if t not in memo:
                memo[t] = self.resolve(t)
            out.append(memo[t])
        return out


@lru_cache(maxsize=1)
def get_adapter() -> HpoAdapter:
    return HpoAdapter()
