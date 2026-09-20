"""D1a: clinical text → assertions (plan §14.2 schema), rule-based.

The four rare-disease-specific fields — `subject`, `polarity`, `asserted_by`,
`onset_text` — are decided here, at extraction time, because they cannot be
recovered from an HPO id afterwards. The extractor is deterministic; the
optional LLM extractor (`assertion/llm.py`) produces the same schema and is
judged by the same gold set, which is the only way a prompt change can be
called an improvement (plan §14.4).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# --- subject -----------------------------------------------------------------
_REL = {
    "father": ("父亲", "爸爸", "生父"),
    "mother": ("母亲", "妈妈", "生母"),
    "sibling": ("姐姐", "妹妹", "哥哥", "弟弟", "姐妹", "兄弟", "同胞", "胞兄", "胞弟", "胞姐", "胞妹", "双胞胎"),
    "other_relative": ("舅舅", "外祖父", "外祖母", "祖父", "祖母", "爷爷", "奶奶", "外公", "外婆", "叔叔", "姑姑",
                       "姨妈", "表兄", "表弟", "表姐", "表妹", "堂兄", "堂弟", "堂姐", "堂妹", "儿子", "女儿",
                       "侄子", "侄女", "外甥", "家族史", "家族中", "亲属"),
}
_REL_WORDS = sorted((w for ws in _REL.values() for w in ws), key=len, reverse=True)
# a relative word counts only as the sentence's subject: at the start, or followed by a
# possession/occurrence verb. "脑脊液丙酮酸家族氨基酸" must not become a family history.
_REL_RE = re.compile(r"(?:^|(?<=[,，;；:：。]))\s*(" + "|".join(map(re.escape, _REL_WORDS)) + r")(?=有|曾|也|亦|均|患|存在|:|：|无|否认|$)"
                     r"|(" + "|".join(map(re.escape, _REL_WORDS)) + r")(?=有|曾有|也出现过|也有|亦有|均有|患有|中有)")
_FAMILY_TAIL = re.compile(r"^(?:有|曾有|也出现过|也有|亦有|均有|患有|存在|也曾)?(.*?)(?:史|病史)?$")

# --- polarity ----------------------------------------------------------------
_NEG_PREFIX = ("未见", "否认", "查体无", "查体未见", "未出现", "未发现", "不伴", "没有", "无明显", "无", "未闻及",
               "未触及", "未及", "不存在", "排除", "已排除")
_NEG_SUFFIX = ("阴性", "未见异常", "正常", "(-)", "（-）", "均正常", "未见", "阴性。")
_UNCERTAIN = ("可疑", "疑似", "不除外", "待排", "可能", "考虑")
_NEG_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, sorted(_NEG_PREFIX, key=len, reverse=True))) + ")")
_NEG_SUFFIX_RE = re.compile("(?:" + "|".join(map(re.escape, sorted(_NEG_SUFFIX, key=len, reverse=True))) + ")[。.]?$")

# --- present-template noise (haenv + everyday clinic phrasing) ---------------
_PRESENT_PREFIX = ("出现", "近期", "自述", "主诉", "患者诉", "表现为", "呈", "有")
_PRESENT_SUFFIX = ("较明显", "明显", "加重", "为主", "表现", "症状")
_PRESENT_PREFIX_RE = re.compile("^(?:" + "|".join(map(re.escape, sorted(_PRESENT_PREFIX, key=len, reverse=True))) + ")")
_PRESENT_SUFFIX_RE = re.compile("(?:" + "|".join(map(re.escape, sorted(_PRESENT_SUFFIX, key=len, reverse=True))) + ")$")

# --- onset / asserted_by / kind ---------------------------------------------
_ONSET_RE = re.compile(r"((?:出生|生后|新生儿期|婴儿期|幼年|儿童期|青春期|成年后|近期|最近|"
                       r"\d+\s*(?:岁|个月|月|天|周|年)(?:时|起|龄|左右)?(?:起病|发病)?))")
_PRIOR_RE = re.compile(r"(外院|曾被诊断|既往诊断|曾诊断|前医|误诊)")
_PATIENT_RE = re.compile(r"^(自述|主诉|患者诉|自诉|自觉)")
_LAB_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mmol/L|μmol/L|umol/L|g/L|mg/dL|U/L|IU/L|ng/mL|pg/mL|%|×10|x10)")
_IMAGING_RE = re.compile(r"(MRI|CT|超声|B超|X线|影像|核磁|PET|脑电图|EEG|肌电图|EMG)", re.I)
_DX_RE = re.compile(r"(诊断为|考虑|疑似|符合)")
_DX_PREFIX_RE = re.compile(r"^(?:外院|既往|曾|前医)?(?:被)?(?:诊断为|诊断|考虑为|考虑|疑似|符合)")
_SPLIT_RE = re.compile(r"[、，,；;]")


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


def _strip_present(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _PRESENT_PREFIX_RE.sub("", s)
        s = _PRESENT_SUFFIX_RE.sub("", s)
    return s.strip(" ,，。.;；:：")


def _polarity(core: str) -> tuple[str, str]:
    """Return (polarity, core-with-cue-stripped)."""
    s = core.strip()
    if any(u in s for u in _UNCERTAIN):
        return "uncertain", s
    m = _NEG_PREFIX_RE.match(s)
    if m:
        return "absent", s[m.end():].strip()
    m = _NEG_SUFFIX_RE.search(s)
    if m and m.start() > 0:
        return "absent", s[: m.start()].strip()
    return "present", s


def _subject(text: str) -> tuple[str, str, str, str]:
    """Return (subject, role, hint, remainder)."""
    m = _REL_RE.search(text)
    if not m:
        return "proband", "proband", "", text
    w = m.group(1) or m.group(2)
    role = next(r for r, ws in _REL.items() if w in ws)
    rem = (text[: m.start()] + text[m.end():]).strip()
    rem = _FAMILY_TAIL.sub(r"\1", rem).strip()
    return "relative", role, w, rem


def _kind(text: str) -> str:
    if _LAB_RE.search(text):
        return "lab_value"
    if _DX_RE.search(text):
        return "diagnosis_hypothesis"
    if _IMAGING_RE.search(text):
        return "imaging"
    return "phenotype"


def extract(text: str, section: str = "", page: int | None = None, offset: int = 0) -> list[Assertion]:
    """One sentence / ledger line → one or more assertions.

    A packed negation ("A、B、C 均正常") expands to one `absent` assertion per item
    (plan §15.1); a plain enumeration stays one assertion per item as well.
    """
    src = text or ""
    s = src.strip()
    if not s:
        return []
    asserted_by = "prior_clinician" if _PRIOR_RE.search(s) else "patient" if _PATIENT_RE.match(s) else "clinician"
    onset = _ONSET_RE.search(s)
    onset_text = onset.group(1) if onset else ""
    body = s
    # whole-line polarity: a trailing "均正常/均阴性" applies to every item
    tail_pol, tail_core = _polarity(body)
    lead_pol = None
    m = _NEG_PREFIX_RE.match(body)
    if m and _SPLIT_RE.search(body):
        lead_pol, body = "absent", body[m.end():]
    elif tail_pol == "absent" and _SPLIT_RE.search(body) and tail_core != body:
        lead_pol, body = "absent", tail_core
    parts = [p for p in _SPLIT_RE.split(body) if p.strip()] if _SPLIT_RE.search(body) else [body]
    out: list[Assertion] = []
    for part in parts:
        start = src.find(part.strip())
        span = (offset + start, offset + start + len(part.strip())) if start >= 0 else (offset, offset + len(src))
        subj, role, hint, rem = _subject(part.strip())
        if lead_pol:
            pol, core = lead_pol, rem
        else:
            pol, core = _polarity(rem)
        core = _DX_PREFIX_RE.sub("", core)
        core = _strip_present(core)
        if hint and not core:
            continue
        out.append(Assertion(text=part.strip(), subject=subj, subject_role=role, subject_hint=hint,
                             polarity=pol, kind=_kind(part), onset_text=onset_text, asserted_by=asserted_by,
                             section=section, char_span=span, page=page, core=core or part.strip()))
    return out


def extract_ledger(ledger: list[dict]) -> list[tuple[str, Assertion]]:
    """haenv `evidence_ledger[]` → [(evidence_id, assertion)]. Only `symptom` entries are
    phenotype text; `note` entries are context and are not coded."""
    out: list[tuple[str, Assertion]] = []
    for e in ledger or []:
        sym = e.get("symptom")
        if not sym:
            continue
        for a in extract(str(sym), section=str(e.get("source_type") or "evidence_ledger")):
            a.extra = {"context": e.get("context") or "", "source_timestamp": e.get("source_timestamp")}
            out.append((str(e.get("evidence_id") or ""), a))
    return out
