"""Optional LLM-backed D1a extractor (plan §14.3 ④): same `Assertion` schema, Gemini via
`google-genai`, gated by `config.llm.enabled`. Batches run through a Semaphore with
tenacity retries; scoring stays in code. Not used by the benchmark run unless enabled."""

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
