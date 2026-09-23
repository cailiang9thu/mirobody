"""D1a: clinical text → assertions (plan §14.2 schema), rule-based.

The four rare-disease-specific fields — `subject`, `polarity`, `asserted_by`,
`onset_text` — are decided here, at extraction time, because they cannot be
recovered from an HPO id afterwards. The extractor is deterministic; the
optional LLM extractor (`assertion/llm.py`) produces the same schema and is
judged by the same gold set, which is the only way a prompt change can be
called an improvement (plan §14.4).

No cue words live in this file: negation / uncertainty / kinship / filler cues are data in
`res/cues/<lang>.yaml`, and `assertion/context.py` is the one engine that reads them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from .context import NEGATED, POSSIBLE, language, lexicon


@dataclass
class Assertion:
    text: str                      # verbatim source span
    subject: str = "proband"       # proband | relative   (haenv contract; role below is the plan's finer axis)
    subject_role: str = "proband"  # proband | father | mother | sibling | other_relative | unknown
    subject_hint: str = ""
    polarity: str = "present"      # present | absent | uncertain
    kind: str = "phenotype"        # phenotype | lab_value | imaging | treatment | diagnosis_hypothesis
    onset_text: str = ""
    asserted_by: str = "clinician"  # clinician | patient | prior_clinician
    section: str = ""
    char_span: tuple[int, int] = (0, 0)
    page: int | None = None
    core: str = ""                 # the phrase handed to the coder (cues stripped)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["char_span"] = list(self.char_span)
        return d


# --- anchored mode (Chinese) ------------------------------------------------------------------
def _alt(words, longest_first: bool = True) -> str:
    ws = sorted(words, key=len, reverse=True) if longest_first else list(words)
    return "|".join("$" if w == "$" else re.escape(w) for w in ws)


@lru_cache(maxsize=None)
def _anchored(lang: str):
    """The compiled cue patterns of an anchored-mode language (built once from its cue file)."""
    lex = lexicon(lang)
    fam = lex.cfg["family"]
    kin = _alt(lex.roles)
    fill = lex.cfg["filler"]

    class P:
        rel = re.compile(rf"(?:^|(?<={fam['clause_start']}))\s*({kin})(?={_alt(fam['subject_after_start'], False)})"
                         rf"|({kin})(?={_alt(fam['subject_after_any'], False)})")
        family_tail = re.compile(rf"^(?:{_alt(fam['tail_prefix'], False)})?(.*?)(?:{_alt(fam['tail_suffix'], False)})?$")
        present_prefix = re.compile(rf"^(?:{_alt(fill['prefix'])})")
        present_suffix = re.compile(rf"(?:{_alt(fill['suffix'])})$")
        strip_chars = fill["strip_chars"]
    return lex, P


def _strip_present(s: str, P) -> str:
    prev = None
    while prev != s:
        prev = s
        s = P.present_prefix.sub("", s)
        s = P.present_suffix.sub("", s)
    return s.strip(P.strip_chars)


def _polarity(core: str, lex) -> tuple[str, str]:
    """Return (polarity, core-with-cue-stripped)."""
    s = core.strip()
    if lex.anywhere(s, POSSIBLE):
        return "uncertain", s
    m = lex.lead(s, NEGATED)
    if m:
        return "absent", s[m.end():].strip()
    m = lex.tail(s, NEGATED)
    if m and m.start() > 0:
        return "absent", s[: m.start()].strip()
    return "present", s


def _subject(text: str, lex, P) -> tuple[str, str, str, str]:
    """Return (subject, role, hint, remainder)."""
    m = P.rel.search(text)
    if not m:
        return "proband", "proband", "", text
    w = m.group(1) or m.group(2)
    rem = (text[: m.start()] + text[m.end():]).strip()
    rem = P.family_tail.sub(r"\1", rem).strip()
    return "relative", lex.roles[w], w, rem


def _extract_anchored(src: str, section: str, page: int | None, offset: int, lang: str) -> list[Assertion]:
    lex, P = _anchored(lang)
    s = src.strip()
    asserted_by = "prior_clinician" if lex.search("prior_clinician", s) else "patient" if lex.search("patient", s) else "clinician"
    onset = lex.search("onset", s)
    onset_text = onset.group(1) if onset else ""
    body = s
    # whole-line polarity: a trailing "均正常/均阴性" applies to every item
    tail_pol, tail_core = _polarity(body, lex)
    lead_pol = None
    m = lex.lead(body, NEGATED)
    if m and lex.split.search(body):
        lead_pol, body = "absent", body[m.end():]
    elif tail_pol == "absent" and lex.split.search(body) and tail_core != body:
        lead_pol, body = "absent", tail_core
    parts = [p for p in lex.split.split(body) if p.strip()] if lex.split.search(body) else [body]
    dx_prefix = lex.pat["dx_prefix"]
    out: list[Assertion] = []
    for part in parts:
        start = src.find(part.strip())
        span = (offset + start, offset + start + len(part.strip())) if start >= 0 else (offset, offset + len(src))
        subj, role, hint, rem = _subject(part.strip(), lex, P)
        if lead_pol:
            pol, core = lead_pol, rem
        else:
            pol, core = _polarity(rem, lex)
        core = dx_prefix.sub("", core)
        core = _strip_present(core, P)
        if hint and not core:
            continue
        out.append(Assertion(text=part.strip(), subject=subj, subject_role=role, subject_hint=hint,
                             polarity=pol, kind=lex.kind(part), onset_text=onset_text, asserted_by=asserted_by,
                             section=section, char_span=span, page=page, core=core or part.strip()))
    return out


# --- scope mode (English) ---------------------------------------------------------------------
def _extract_scope(src: str, section: str, page: int | None, offset: int, lang: str) -> list[Assertion]:
    """ConText over the sentence: the targets are the HPO terms in it (one assertion each), and
    each gets the polarity / experiencer of the triggers whose scope covers it."""
    from ..hpo import get_adapter
    lex = lexicon(lang)
    s = src.strip().rstrip(".").rstrip()
    at = max(src.find(s), 0)
    span = (offset + at, offset + at + len(s))
    kind = lex.kind(s)
    asserted_by = "patient" if lex.search("patient", s) else "clinician"
    targets = get_adapter().find_all(s)
    if not targets:
        # nothing the dictionary knows: one assertion for the whole sentence, so the coder can
        # abstain on it and the review queue still sees it
        return [Assertion(text=s, kind=kind, asserted_by=asserted_by, section=section, char_span=span, page=page, core=s)]
    toks, mods = lex.modifiers(s, targets)
    out = []
    for a, b in targets:
        ctx = lex.assess(toks, mods, a, b)
        out.append(Assertion(text=s, subject=ctx.subject, subject_role=ctx.role, subject_hint=ctx.hint,
                             polarity=ctx.polarity, kind=kind, asserted_by=asserted_by, section=section,
                             char_span=span, page=page, core=s[a:b],
                             extra={"target_span": [offset + at + a, offset + at + b], "cues": ctx.cues}))
    return out


def extract(text: str, section: str = "", page: int | None = None, offset: int = 0) -> list[Assertion]:
    """One sentence / ledger line → one or more assertions.

    A packed negation ("A、B、C 均正常", "No A, B or C") expands to one `absent` assertion per
    item (plan §15.1); a plain enumeration stays one assertion per item as well.
    """
    src = text or ""
    s = src.strip()
    if not s:
        return []
    lang = language(s)
    if lexicon(lang).mode == "scope":
        return _extract_scope(src, section, page, offset, lang)
    return _extract_anchored(src, section, page, offset, lang)


def extract_ledger(ledger: list[dict]) -> list[tuple[str, Assertion]]:
    """haenv `evidence_ledger[]` → [(evidence_id, assertion)]. Only `symptom` entries are
    phenotype text; `note` entries are context and are not coded."""
    out: list[tuple[str, Assertion]] = []
    for e in ledger or []:
        sym = e.get("symptom")
        if not sym:
            continue
        for a in extract(str(sym), section=str(e.get("source_type") or "evidence_ledger")):
            a.extra = {**a.extra, "context": e.get("context") or "", "source_timestamp": e.get("source_timestamp")}
            out.append((str(e.get("evidence_id") or ""), a))
    return out
