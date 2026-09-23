"""assertion_arms.py — the rule arm and the LLM arm on the same narratives, scored by the same judge.

Both arms produce `(evidence_id, Assertion)` pairs for a case's narrative Markdown and go through
the same `code_assertions` (dictionary resolver → HPO), so they differ only in how the sentence is
read. The judge mirrors haenv's `_judge_narrative` (recall / span / polarity / noise abstain, by
char-span overlap with the gold sentence) and adds `subject`, which haenv judges on the ledger only.

    .venv/bin/python3 tools/assertion_arms.py <job.yaml> [--split dev|test|all] [--arms rules,llm] [-v]

`--split` follows the held-out protocol of rare_coding-p5-en-llm: dev = case number < 60, test ≥ 60.
LLM outputs are cached per case under `--cache` (default `reports/assertion_arms/<job>/llm/`); a rerun
calls the model only for cases missing there. SYNTHETIC data, evaluation only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "mirobody-rare"))


def _env(path: str, name: str) -> None:
    if os.environ.get(name):
        return
    p = Path(os.path.expanduser(path))
    if p.is_file():
        for line in p.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                os.environ[name] = line.split("=", 1)[1].strip().strip("'\"")


def _split_of(cid: str) -> str:
    return "dev" if int(cid.split("-")[1][:2]) < 60 else "test"


def judge(md: str, spans: list[dict], coded) -> tuple[Counter, list]:
    t, errs = Counter(), []
    got = [(ca.hpo.hpo_id, ca.assertion) for ca in coded]
    for sp in spans:
        sent = md[sp["start"]:sp["end"]]
        inside = [(h, a) for h, a in got if a.char_span[0] < sp["end"] and a.char_span[1] > sp["start"]]
        if sp["hpo_id"] is None:
            t["noise"] += 1
            if inside:
                t["noise_hit"] += 1
                errs.append(("NOISE", sent, [(h, a.core) for h, a in inside]))
            continue
        t["gold"] += 1
        t["hit"] += any(h == sp["hpo_id"] for h, _ in got)
        mine = [a for h, a in inside if h == sp["hpo_id"]]
        if not mine:
            errs.append(("MISS", sent, sp["hpo_id"], [(h, a.core) for h, a in inside]))
            continue
        a = mine[0]
        t["span"] += 1
        t["pol"] += a.polarity == sp["polarity"]
        t["subj"] += a.subject == sp["subject"]
        if a.polarity != sp["polarity"]:
            errs.append(("POL", sent, f"{a.polarity}≠{sp['polarity']}", a.core))
        if a.subject != sp["subject"]:
            errs.append(("SUBJ", sent, f"{a.subject}≠{sp['subject']}", a.core))
    return t, errs


def scores(t: Counter) -> dict:
    g, s = t["gold"] or 1, t["span"] or 1
    return {"recall": round(t["hit"] / g, 3), "span_ok": round(t["span"] / g, 3), "polarity_ok": round(t["pol"] / s, 3),
            "subject_ok": round(t["subj"] / s, 3),
            "noise_abstain": round(1 - t["noise_hit"] / t["noise"], 3) if t["noise"] else None,
            "n_gold": t["gold"], "n_noise": t["noise"]}


async def _llm_pairs(cases: dict[str, str], cache: Path) -> dict[str, list]:
    from mirobody_rare.assertion.llm import document_sentences, extract_documents, parse_document
    cache.mkdir(parents=True, exist_ok=True)
    out, todo = {}, {}
    for cid, md in cases.items():
        f = cache / f"{cid}.json"
        if f.is_file():
            out[cid] = parse_document(json.loads(f.read_text())["raw"], document_sentences(md))
        else:
            todo[cid] = md
    if todo:
        async for cid, pairs, raw in extract_documents(todo):
            if pairs is not None:
                (cache / f"{cid}.json").write_text(json.dumps({"raw": raw}, ensure_ascii=False))
                out[cid] = pairs
    missing = sorted(set(cases) - set(out))
    if missing:
        logging.error("LLM arm: no output for %s — rerun to retry only these", missing)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--split", default="all", choices=["dev", "test", "all"])
    ap.add_argument("--arms", default="rules,llm")
    ap.add_argument("--cache", default="")
    ap.add_argument("--env-file", default="~/caill/.mirobody_rare_env")
    ap.add_argument("--out", default="")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    from mirobody_rare._config import load as load_cfg
    logging.basicConfig(level=logging.DEBUG if int(load_cfg().get("verbose", 1)) > 1 else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from mirobody_rare.coding import code_assertions
    from mirobody_rare.text_hook import assertions_of
    job = yaml.safe_load(Path(a.job).read_text())
    jid = job["job_id"]
    mds, spans = {}, {}
    for c in job["cases"]:
        cid = c["case_id"]
        if a.split != "all" and _split_of(cid) != a.split:
            continue
        nar = c["latent"]["rare_attachments"]["narrative"]
        mds[cid] = Path(nar["path"]).read_text(encoding="utf-8")
        spans[cid] = nar["spans"]
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    pairs: dict[str, dict[str, list]] = {}
    if "rules" in arms:
        pairs["rules"] = {cid: assertions_of(md) for cid, md in mds.items()}
    if "llm" in arms:
        llm_cfg = load_cfg()["llm"]
        _env(a.env_file, llm_cfg["openrouter_key_env"] if llm_cfg.get("backend") == "openrouter" else llm_cfg["api_key_env"])
        cache = Path(a.cache) if a.cache else ROOT / "reports" / "assertion_arms" / jid / "llm"
        pairs["llm"] = asyncio.run(_llm_pairs(mds, cache))
    report = {"job": jid, "split": a.split, "model": (load_cfg()["llm"]["openrouter_model"] if load_cfg()["llm"].get("backend") == "openrouter"
                                                    else load_cfg()["llm"]["model_name"]) if "llm" in arms else None, "arms": {}}
    for arm, per in pairs.items():
        by_split = {"dev": Counter(), "test": Counter()}
        errs = []
        for cid, pr in per.items():
            t, e = judge(mds[cid], spans[cid], code_assertions(pr).coded)
            by_split[_split_of(cid)] += t
            errs += [(cid, *x) for x in e]
        report["arms"][arm] = {s: scores(t) for s, t in by_split.items() if t["gold"]}
        report["arms"][arm]["errors"] = [list(map(str, e)) for e in errs]
        for s, sc in report["arms"][arm].items():
            if s != "errors":
                print(f"{arm:6s} {s:5s} " + " ".join(f"{k}={v}" for k, v in sc.items()))
        if a.v:
            for e in errs:
                print("   ", " | ".join(map(str, e)))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
