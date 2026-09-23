"""ConText over per-language cue files (`res/cues/<lang>.yaml`).

ConText (Harkema et al. 2009) decides, for each target term of a sentence, whether it is negated,
uncertain, or experienced by someone other than the patient: a trigger modifies the targets in its
scope, FORWARD up to the next terminator of its category, BACKWARD back to the previous one. The
rules are data — medspaCy's published English set is loaded verbatim — so this module holds the
mechanism and no cue words.

Two modes, chosen by the language file:
* `scope` — full ConText over tokens (English).
* `anchored` — a trigger counts only where its `anchor` puts it (clause start / clause end /
  anywhere). This is how the Chinese extractor has always worked; the primitives live here so
  its cue lists can too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CUES = Path(__file__).resolve().parent.parent / "res" / "cues"

NEGATED, POSSIBLE, HYPOTHETICAL, FAMILY = "NEGATED_EXISTENCE", "POSSIBLE_EXISTENCE", "HYPOTHETICAL", "FAMILY"
_UNCERTAIN = (POSSIBLE, HYPOTHETICAL)

# word tokens: "didn't" -> did + n't, "patient's" -> patient + 's (spaCy's split, which medspaCy's patterns assume)
_TOKEN = re.compile(r"n't|'s|[a-z0-9]+(?=n't)|[a-z0-9]+|[^\sa-z0-9]", re.I)


def tokens(text: str) -> list[tuple[int, int, str]]:
    text = text.replace("’", "'")
    return [(m.start(), m.end(), m.group().lower()) for m in _TOKEN.finditer(text)]


def language(text: str) -> str:
    """`zh` when the text has any CJK ideograph, `en` when it has Latin words, else `zh` (the default path)."""
    if re.search(r"[一-鿿]", text):
        return "zh"
    return "en" if re.search(r"[A-Za-z]{3,}", text) else "zh"


@dataclass(frozen=True)
class Rule:
    literal: str
    category: str
    direction: str                    # FORWARD | BACKWARD | BIDIRECTIONAL | TERMINATE | PSEUDO
    pattern: tuple = ()               # ((frozenset(values), optional), ...) — medspaCy LOWER/IN/OP:?
    anchor: str = "any"               # anchored mode: start | end | any
    source: str = ""


@dataclass(frozen=True)
class Modifier:
    start: int                        # token index (scope mode) or char offset (anchored mode)
    end: int
    rule: Rule
    text: str


@dataclass
class Assessment:
    polarity: str = "present"
    subject: str = "proband"
    role: str = "proband"
    hint: str = ""
    cues: list = field(default_factory=list)


def _pattern(rule: dict) -> tuple:
    pat = rule.get("pattern")
    if not pat:
        return tuple((frozenset([t]), False) for _, _, t in tokens(rule["literal"]))
    out = []
    for tok in pat:
        v = tok.get("LOWER")
        vals = v.get("IN") if isinstance(v, dict) else [v]
        # a value with a space ("risks of") is two tokens in the text and never matches one token
        # in spaCy either; kept so the vendored file is consumed as published
        out.append((frozenset(str(x).lower() for x in vals), tok.get("OP") == "?"))
    return tuple(out)


def _match(pat: tuple, toks: list[str], i: int) -> int:
    """Longest end index of `pat` matched at token `i`, or -1."""
    best = -1

    def go(k: int, j: int) -> None:
        nonlocal best
        if k == len(pat):
            best = max(best, j)
            return
        vals, opt = pat[k]
        if j < len(toks) and toks[j] in vals:
            go(k + 1, j + 1)
        if opt:
            go(k + 1, j)
    go(0, i)
    return best if best > i else -1


class Lexicon:
    def __init__(self, lang: str):
        self.lang = lang
        self.cfg = yaml.safe_load((CUES / f"{lang}.yaml").read_text(encoding="utf-8"))
        self.mode = self.cfg.get("mode", "scope")
        self.char = self.cfg.get("boundary") == "char"
        raw: list[tuple[dict, str]] = []
        for inc in self.cfg.get("include") or []:
            data = json.loads((CUES / inc).read_text(encoding="utf-8"))
            raw += [(r, inc) for r in data["context_rules"]]
        raw += [(r, f"{lang}.yaml") for r in self.cfg.get("rules") or []]
        fam = self.cfg.get("family") or {}
        raw += [({"literal": t, "category": FAMILY, "direction": "TERMINATE"}, f"{lang}.yaml:family.terminate")
                for t in fam.get("terminate") or []]
        self.rules = [Rule(str(r["literal"]), r["category"], r["direction"].upper(),
                           () if self.char else _pattern(r), r.get("anchor", "any"), src) for r, src in raw]
        self.roles = {w.lower() if not self.char else w: role
                      for role, ws in (fam.get("roles") or {}).items() for w in ws}
        self.pat = {k: re.compile(v) for k, v in (self.cfg.get("patterns") or {}).items()}
        self.split = re.compile(self.cfg["clause_split"]) if self.cfg.get("clause_split") else None

    # ---------------------------------------------------------------- scope mode
    def modifiers(self, text: str, targets: list[tuple[int, int]] = ()) -> tuple[list[tuple[int, int, str]], list[Modifier]]:
        """Tokens and the trigger matches of `text`. A trigger lying wholly inside a target is part of
        the finding's own name, not a trigger ("Cognitive decline" is no refusal) — medspaCy's
        `prune_on_target_overlap`, narrowed to containment so "No abnormal gait" keeps its "no"."""
        toks = tokens(text)
        words = [t for _, _, t in toks]
        found = []
        for i in range(len(words)):
            for r in self.rules:
                j = _match(r.pattern, words, i)
                if j > 0:
                    found.append(Modifier(i, j, r, text[toks[i][0]:toks[j - 1][1]]))
        # overlapping matches: the longest wins (then the earlier); a PSEUDO match only blocks
        found.sort(key=lambda m: (-(m.end - m.start), m.start))
        kept: list[Modifier] = []
        for m in found:
            if all(m.end <= k.start or m.start >= k.end for k in kept):
                kept.append(m)
        kept.sort(key=lambda m: m.start)
        inside = lambda m: any(a <= toks[m.start][0] and toks[m.end - 1][1] <= b for a, b in targets)
        return toks, [m for m in kept if m.rule.direction != "PSEUDO" and not inside(m)]

    def _scope(self, m: Modifier, mods: list[Modifier], n: int) -> tuple[int, int, int, int]:
        """(forward_start, forward_end, backward_start, backward_end) in tokens."""
        stops = [k for k in mods if k is not m and k.rule.direction == "TERMINATE" and k.rule.category == m.rule.category]
        f_end = min([k.start for k in stops if k.start >= m.end], default=n)
        b_start = max([k.end for k in stops if k.end <= m.start], default=0)
        return m.end, f_end, b_start, m.start

    def assess(self, toks: list, mods: list[Modifier], a: int, b: int) -> Assessment:
        """ConText values for the target at chars [a, b)."""
        ti = [k for k, (s, e, _) in enumerate(toks) if s < b and e > a]
        if not ti:
            return Assessment()
        t0, t1 = ti[0], ti[-1] + 1
        out = Assessment()
        hits = []
        for m in mods:
            if m.rule.direction == "TERMINATE" or m.rule.category not in (NEGATED, POSSIBLE, HYPOTHETICAL, FAMILY):
                continue
            fs, fe, bs, be = self._scope(m, mods, len(toks))
            # a trigger that overlaps the start of the target still reaches it: in "No abnormal hip
            # bone morphology" the trigger "no abnormal" shares a word with the term it negates
            fwd = m.rule.direction in ("FORWARD", "BIDIRECTIONAL") and m.start <= t0 < fe and t1 > fs
            bwd = m.rule.direction in ("BACKWARD", "BIDIRECTIONAL") and bs < t1 <= m.end and t0 < be
            if fwd or bwd:
                hits.append(m)
        for m in hits:
            out.cues.append(f"{m.rule.category}:{m.text}")
            if m.rule.category == FAMILY and out.subject == "proband":
                word = next((w for _, _, w in toks[m.start:m.end] if w in self.roles), m.text.lower())
                out.subject, out.role, out.hint = "relative", self.roles.get(word, "other_relative"), m.text
        cats = {m.rule.category for m in hits}
        out.polarity = "uncertain" if cats & set(_UNCERTAIN) else "absent" if NEGATED in cats else "present"
        return out

    # ---------------------------------------------------------------- anchored mode
    @lru_cache(maxsize=None)
    def _anchored_re(self, category: str, anchor: str) -> re.Pattern | None:
        lits = sorted({r.literal for r in self.rules if r.category == category and r.anchor == anchor},
                      key=len, reverse=True)
        if not lits:
            return None
        alt = "|".join(map(re.escape, lits))
        return re.compile({"start": f"^(?:{alt})", "end": f"(?:{alt})[。.]?$", "any": f"(?:{alt})"}[anchor])

    def lead(self, s: str, category: str) -> re.Match | None:
        rx = self._anchored_re(category, "start")
        return rx.match(s) if rx else None

    def tail(self, s: str, category: str) -> re.Match | None:
        rx = self._anchored_re(category, "end")
        return rx.search(s) if rx else None

    def anywhere(self, s: str, category: str) -> bool:
        return any(r.literal in s for r in self.rules if r.category == category and r.anchor == "any")

    def search(self, name: str, s: str) -> re.Match | None:
        rx = self.pat.get(name)
        return rx.search(s) if rx else None

    def kind(self, s: str) -> str:
        if self.search("lab_value", s):
            return "lab_value"
        if self.search("diagnosis", s):
            return "diagnosis_hypothesis"
        if self.search("imaging", s):
            return "imaging"
        return "phenotype"


@lru_cache(maxsize=None)
def lexicon(lang: str) -> Lexicon:
    return Lexicon(lang)
