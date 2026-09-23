"""Optional LLM-backed D1a extractor (plan §14.3 ④): same `Assertion` schema, Gemini via
`google-genai`, gated by `config.llm.enabled`. Batches run through a Semaphore with
tenacity retries; scoring stays in code. Not used by the benchmark run unless enabled.

The model never hands back an HPO id: it names each finding by the closest HPO term name
(`core`) and the same dictionary resolver the rule arm uses does the coding, so an invented code
cannot reach a row and the two arms differ only in how they read the sentence."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from .._config import load as load_cfg
from .rules import Assertion

log = logging.getLogger(__name__)

_PROMPT = """你是罕见病病历断言抽取器。把下面的临床文本拆成断言列表,只输出 JSON 数组,每项字段:
text(原文逐字片段)、subject(proband|father|mother|sibling|other_relative|unknown)、polarity(present|absent|uncertain)、
kind(phenotype|lab_value|imaging|treatment|diagnosis_hypothesis)、onset_text(原文时间表述或空)、
asserted_by(clinician|patient|prior_clinician)、core(去掉否定词/主体词后的症状短语)。
"A、B、C 均正常" 要拆成三条 polarity=absent。不要编造原文里没有的内容。
文本:
"""

_DOC_PROMPT = """You extract phenotype assertions from a clinical case report for HPO coding.

The report is given as numbered sentences. For EVERY sentence, list each clinical abnormality it
asserts about a person (a sign, symptom, abnormal examination / laboratory / imaging finding). A
sentence may carry none (scaffolding, vital signs, social history, pending tests) or several.

For each finding return:
- "sentence": the sentence number
- "core": the name of the closest Human Phenotype Ontology term, in English, as HPO names it
  (e.g. "Inability to walk", "Elevated circulating creatine kinase concentration", "Tall stature").
  Name the finding itself, never its negation.
- "polarity": "present", "absent" (stated as not present / denied / not observed / normal), or
  "uncertain" (suspected, possible, to be ruled out)
- "subject": "proband" when the patient has it, else the relative who has it: "father", "mother",
  "sibling" or "other_relative". A relative who only REPORTS the patient's finding is not its subject.
- "evidence": the words of the sentence that state it, verbatim

Do not name a disease, syndrome or gene as a finding. Return ONLY JSON:
{{"findings": [{{"sentence": 1, "core": "...", "polarity": "...", "subject": "...", "evidence": "..."}}]}}

Sentences:
{sentences}"""


def _client():
    cfg = load_cfg()["llm"]
    from google import genai
    key = os.environ.get(cfg.get("api_key_env", "GEMINI_API_KEY"), "")
    if not key:
        raise RuntimeError(f"{cfg.get('api_key_env')} not set")
    return genai.Client(api_key=key), cfg


def _parse(raw: str, section: str) -> list[Assertion]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").split("\n", 1)[-1]
    rows = json.loads(s)
    out = []
    for r in rows:
        role = r.get("subject", "proband")
        out.append(Assertion(text=r.get("text", ""), subject="proband" if role == "proband" else "relative",
                             subject_role=role, polarity=r.get("polarity", "present"), kind=r.get("kind", "phenotype"),
                             onset_text=r.get("onset_text", ""), asserted_by=r.get("asserted_by", "clinician"),
                             section=section, core=r.get("core") or r.get("text", "")))
    return out


async def extract_many(texts: list[str], section: str = "") -> list[tuple[int, list[Assertion]]]:
    """Return [(index, assertions)] as they complete (tqdm_asyncio.as_completed pattern)."""
    from tenacity import retry, stop_after_attempt, wait_exponential
    from tqdm.asyncio import tqdm_asyncio
    client, cfg = _client()
    sem = asyncio.Semaphore(int(cfg.get("max_concurrency", 8)))

    @retry(stop=stop_after_attempt(int(cfg.get("max_retries", 3))), wait=wait_exponential(min=1, max=30))
    async def one(i: int) -> tuple[int, list[Assertion]]:
        async with sem:
            r = await client.aio.models.generate_content(model=cfg["model_name"], contents=_PROMPT + texts[i])
            return i, _parse(r.text or "[]", section)

    out = []
    for fut in tqdm_asyncio.as_completed([one(i) for i in range(len(texts))], total=len(texts)):
        out.append(await fut)
    return out


def document_sentences(text: str) -> list[tuple[str, str, tuple[int, int]]]:
    """(evidence_id, sentence, char_span) — the same segmentation the rule arm's text hook uses."""
    from ..text_hook import _SENT, _SKIP_SECTIONS, clean_lines, split_sections
    out, cursor = [], 0
    for section, body in split_sections(text) or [("", text)]:
        skip = any(k in section for k in _SKIP_SECTIONS)
        for li, line in enumerate(clean_lines(body)):
            for si, sent in enumerate(s for s in _SENT.split(line) if s.strip()):
                sent = sent.strip()
                at = text.find(sent, cursor)
                if at >= 0:
                    cursor = at + len(sent)
                if not skip:
                    core = sent.rstrip(".").rstrip()
                    out.append((f"{section}#{li}.{si}", core, (max(at, 0), max(at, 0) + len(core))))
    return out


def parse_document(raw: str, sents: list[tuple[str, str, tuple[int, int]]]) -> list[tuple[str, Assertion]]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").split("\n", 1)[-1]
    rows = json.loads(s)
    rows = rows.get("findings", []) if isinstance(rows, dict) else rows
    out = []
    for r in rows:
        try:
            ev, sent, span = sents[int(r["sentence"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        role = str(r.get("subject") or "proband")
        pol = str(r.get("polarity") or "present")
        out.append((ev, Assertion(text=sent, subject="proband" if role == "proband" else "relative", subject_role=role,
                                  polarity=pol if pol in ("present", "absent", "uncertain") else "present",
                                  section=ev.split("#", 1)[0], char_span=span, core=str(r.get("core") or "").strip(),
                                  extra={"evidence": r.get("evidence") or "", "extractor": "llm"})))
    return out


def _doc_backend():
    """(call(prompt) -> raw text, reinit(), model id) for the configured backend."""
    cfg = load_cfg()["llm"]
    if cfg.get("backend", "genai") == "openrouter":
        from openai import AsyncOpenAI
        key = os.environ.get(cfg["openrouter_key_env"], "")
        if not key:
            raise RuntimeError(f"{cfg['openrouter_key_env']} not set")
        state = {}

        def reinit():
            state["c"] = AsyncOpenAI(base_url=cfg["openrouter_base_url"], api_key=key)

        async def call(prompt: str) -> str:
            r = await state["c"].chat.completions.create(
                model=cfg["openrouter_model"], messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, temperature=0)
            return r.choices[0].message.content or "{}"
        reinit()
        return call, reinit, cfg["openrouter_model"], cfg
    state = {}

    def reinit():
        state["c"] = _client()[0]

    async def call(prompt: str) -> str:
        r = await state["c"].aio.models.generate_content(
            model=cfg["model_name"], contents=prompt, config={"response_mime_type": "application/json", "temperature": 0})
        return r.text or "{}"
    reinit()
    return call, reinit, cfg["model_name"], cfg


async def extract_documents(docs: dict[str, str]):
    """Yield (key, [(evidence_id, assertion)], raw_model_text) per document as they complete. A
    document whose model output does not parse after `max_retries` yields (key, None, None) — the
    caller reruns only those."""
    from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential
    from tqdm.asyncio import tqdm_asyncio
    call, reinit, model, cfg = _doc_backend()
    sem = asyncio.Semaphore(int(cfg.get("max_concurrency", 8)))
    log.info("[llm] %d documents via %s", len(docs), model)

    async def one(key: str, text: str):
        sents = document_sentences(text)
        prompt = _DOC_PROMPT.format(sentences="\n".join(f"{i}. {s}" for i, (_, s, _) in enumerate(sents, 1)))
        async with sem:
            try:
                async for attempt in AsyncRetrying(stop=stop_after_attempt(int(cfg.get("max_retries", 3)) + 1),
                                                   wait=wait_exponential(min=2, max=60), reraise=True):
                    with attempt:
                        try:
                            raw = await call(prompt)
                            if '"findings"' not in (raw or ""):
                                # an empty completion ("{}") is a failed call, not a report with no findings
                                raise ValueError(f"no findings key in model output ({len(raw or '')} chars)")
                        except Exception as e:
                            if "An API or other error occurred" in str(e):
                                await asyncio.sleep(5)
                                reinit()
                            raise
                        return key, parse_document(raw, sents), raw
            except Exception as e:  # noqa: BLE001 — one bad document must not sink the batch
                log.warning("[llm] %s failed: %s", key, e)
                return key, None, None

    for fut in tqdm_asyncio.as_completed([one(k, t) for k, t in docs.items()], total=len(docs)):
        yield await fut
