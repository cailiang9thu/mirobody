"""The `mirobody.text_hooks` hook (ingest-plan §2.2): narrative text → assertions → HPO / ORPHA →
th_phenotype (source='nlp') + th_disease_code (candidate) + th_phenotype_review (ambiguous /
abstained). Lab values are not touched here: the shipped indicator extractor owns them."""

from __future__ import annotations

import asyncio
import logging
import re

from ._config import load as load_cfg
from .assertion import extract
from .assertion.rules import Assertion
from .coding import code_assertions

log = logging.getLogger(__name__)

_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s*|[一二三四五六七八九十]+[、.．]\s*|\d+[、.．]\s*)?([^\n：:]{1,24})[：:]?\s*$")
_KNOWN = ("病史", "现病史", "既往史", "主诉", "查体", "体格检查", "辅助检查", "实验室检查", "影像", "家族史", "诊断", "出院诊断",
          "治疗", "讨论", "病例", "个人史", "基因检测")
_MCQ = re.compile(r"^\s*[A-EＡ-Ｅ][\.．、)）]\s*\S")
_MAX_CHARS = 200_000


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(section, body)] by markdown / numbered headings; a heading counts when it is short and
    names a known clinical section or is a markdown heading."""
    out: list[tuple[str, str]] = []
    cur, buf = "", []
    for line in text.replace("\r", "").split("\n"):
        m = _HEADING.match(line)
        name = m.group(1).strip() if m else ""
        is_head = bool(m) and (line.lstrip().startswith("#") or any(k in name for k in _KNOWN)) and len(name) <= 24
        if is_head and name and not (line.lstrip().startswith("#") and name in ("出院小结", "病例报告") and not out):
            if cur or buf:
                out.append((cur, "\n".join(buf)))
            cur, buf = name, []
        else:
            buf.append(line)
    if cur or buf:
        out.append((cur, "\n".join(buf)))
    def _real(name, body):           # an unnamed preamble that is only a document title is not a section
        lines = [l for l in body.split("\n") if l.strip()]
        return bool(lines) and not (name == "" and all(l.lstrip().startswith("#") for l in lines))
    return [(n, b) for n, b in out if _real(n, b)]


def clean_lines(body: str) -> list[str]:
    """§14.3 ①: drop multiple-choice options, HTML remnants, empty lines."""
    lines = []
    for raw in body.split("\n"):
        s = re.sub(r"<[^>]+>", "", raw).strip()
        if not s or _MCQ.match(s):
            continue
        lines.append(s)
    return lines


_SENT = re.compile(r"[。；;！!？?\n]")


async def _wait_file_id(repo, user_id: str, file_key: str, timeout: float = 10.0) -> int | None:
    t = 0.0
    while t < timeout:
        fid = await repo.file_id_by_key(user_id, file_key)
        if fid:
            return fid
        await asyncio.sleep(0.5)
        t += 0.5
    return None


_SKIP_SECTIONS = ("治疗", "讨论", "团队", "专家")


def assertions_of(text: str) -> list[tuple[str, "Assertion"]]:
    """(evidence_id, assertion) for every sentence of the document, evidence_id =
    `<section>#<line>.<sentence>`. Each assertion's `char_span` is an offset into `text`
    itself (not into the sentence), so a reviewer can jump from a row back to the document:
    the sentence is located by searching forward from where the previous one ended."""
    pairs: list[tuple[str, Assertion]] = []
    cursor = 0
    for section, body in split_sections(text) or [("", text)]:
        skip = any(k in section for k in _SKIP_SECTIONS)
        for li, line in enumerate(clean_lines(body)):
            for si, sent in enumerate(s for s in _SENT.split(line) if s.strip()):
                sent = sent.strip()
                at = text.find(sent, cursor)
                if at >= 0:
                    cursor = at + len(sent)
                if skip:
                    continue
                for a in extract(sent, section=section, offset=max(at, 0)):
                    pairs.append((f"{section}#{li}.{si}", a))
    return pairs


async def run_rare_coding(ctx, repo=None, file_id: int | None = None) -> dict:
    cfg = load_cfg()
    if repo is None:
        from .repo import PgRepo
        repo = PgRepo()
    if ctx.content_hash and await repo.phenotypes_exist_for_hash(ctx.user_id, ctx.content_hash):
        log.info("[text_hook] %s already coded for %s (hash %s); skip", ctx.file_name, ctx.user_id, ctx.content_hash[:12])
        return {"duplicate": True, "phenotypes": 0, "review": 0}
    if file_id is None and ctx.file_key:
        file_id = await _wait_file_id(repo, ctx.user_id, ctx.file_key)
        if file_id is None:
            log.warning("[text_hook] th_files row for %s not found within 10 s; rows get file_id=NULL", ctx.file_key)
    text = ctx.text
    truncated = len(text) > _MAX_CHARS
    if truncated:
        text = text[:_MAX_CHARS]
    pairs = assertions_of(text)
    res = code_assertions(pairs)
    ph_rows, rv_rows = [], []
    for c in res.coded:
        a = c.assertion
        if c.hpo.ambiguous:
            rv_rows.append({"user_id": ctx.user_id, "file_id": file_id, "kind": "ambiguous", "source_text": a.text,
                            "candidates": [x.hpo_id for x in c.hpo.candidates[:5]], "section": a.section,
                            "subject": a.subject, "negated": a.polarity == "absent"})
            continue
        if a.polarity == "uncertain":
            rv_rows.append({"user_id": ctx.user_id, "file_id": file_id, "kind": "abstained", "source_text": a.text,
                            "candidates": [c.hpo.hpo_id], "section": a.section, "subject": a.subject, "negated": None})
            continue
        ph_rows.append({"user_id": ctx.user_id, "hpo_id": c.hpo.hpo_id, "hpo_label": c.hpo.label, "negated": a.polarity == "absent",
                        "subject": a.subject, "subject_role": a.subject_role if a.subject == "relative" else None,
                        "source": "nlp", "source_text": a.text, "confidence": c.hpo.score, "asserted_at": None,
                        "file_id": file_id, "section": a.section, "content_hash": ctx.content_hash})
    for ab in res.abstained:
        if str(ab.get("reason", "")).startswith("kind=lab_value"):
            continue                                   # the indicator extractor's job
        rv_rows.append({"user_id": ctx.user_id, "file_id": file_id, "kind": "abstained", "source_text": ab["text"],
                        "candidates": ab.get("candidates") or [], "section": None, "subject": None, "negated": None})
    if truncated:
        rv_rows.append({"user_id": ctx.user_id, "file_id": file_id, "kind": "truncated", "source_text": f"document cut at {_MAX_CHARS} chars",
                        "candidates": [], "section": None, "subject": None, "negated": None})
    n_ph = await repo.add_phenotypes(ph_rows) if ph_rows else 0
    n_rv = 0
    for r in rv_rows:
        await repo.add_review(r)
        n_rv += 1
    dx = res.diagnosis
    if (dx.get("codes") or {}).get("orpha"):
        await repo.add_disease_code({"user_id": ctx.user_id, "system": "ORPHA", "code": dx["codes"]["orpha"], "label": dx.get("name") or "",
                                     "status": "candidate", "source": "nlp", "source_text": None,
                                     "confidence": float(dx.get("confidence") or 0), "file_id": file_id})
    log.info("[text_hook] %s user=%s: %d phenotype rows, %d review rows, dx=%s", ctx.file_name, ctx.user_id, n_ph, n_rv, (dx.get("codes") or {}).get("orpha"))
    from .dx_refresh import refresh_diagnosis
    promoted = (await refresh_diagnosis(repo, ctx.user_id, file_id=file_id)).get("promoted")   # a VCF may already be on file
    return {"duplicate": False, "phenotypes": n_ph, "review": n_rv, "diagnosis": (dx.get("codes") or {}).get("orpha"), "promoted": promoted}


async def rare_coding_hook(ctx) -> None:
    if not load_cfg().get("text_hook", {}).get("enabled", True):
        return
    try:
        await run_rare_coding(ctx)
    except Exception:                                   # noqa: BLE001
        log.exception("[text_hook] rare coding failed for %s", getattr(ctx, "file_key", "?"))


HOOKS = (rare_coding_hook,)
