"""OpenAI-compatible HTTP shim: haenv posts its solver prompt here, gets the coding
result back as `choices[0].message.content` JSON (决策 2026-09-20 #5).

The request is the standard `/v1/chat/completions` body; the prompt's payload is
the JSON object after the `pre-T 数据(JSON):` marker (haenv `DDX_PROMPT`). The
answer carries haenv's DDX fields so the frame parses, plus the coding fields the
`rare_coding` judge reads. No model is called: the "model" is the coding pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ._config import load as load_cfg, setup_logging
from .coding import apply_genome, apply_narrative, apply_signals, code_ledger

log = logging.getLogger(__name__)

PAYLOAD_MARKER = "pre-T 数据(JSON):"


def extract_payload(prompt: str) -> dict:
    i = prompt.find(PAYLOAD_MARKER)
    if i < 0:
        # last resort: first top-level JSON object in the prompt
        i = prompt.find("{")
        if i < 0:
            raise ValueError("no payload in prompt")
        j = i
    else:
        j = prompt.find("{", i)
    obj, _ = json.JSONDecoder().raw_decode(prompt[j:])
    return obj


def answer_for_payload(payload: dict) -> dict:
    ledger = payload.get("evidence_ledger") or []
    res = code_ledger(ledger)
    att = (payload.get("prediction_context") or {}).get("attachments")
    res = apply_genome(res, att, sex_hint=(payload.get("user_profile") or {}).get("sex"))
    res = apply_signals(res, att)
    res = apply_narrative(res, att)
    sol = res.to_solver()
    coded_ids = [c.evidence_id for c in res.coded]
    by_hpo: dict[str, list[str]] = {}
    for c in res.coded:
        if c.hpo and c.hpo.resolved and c.assertion.polarity == "present" and c.assertion.subject == "proband":
            by_hpo.setdefault(c.hpo.hpo_id, []).append(c.evidence_id)
    hpo = None
    differential = []
    for k, h in enumerate(res.differential, 1):
        support = sorted({ev for q in h.matched for ev in by_hpo.get(q, [])})
        ruled = None
        if k > 1 and h.against:
            neg_ev = [c.evidence_id for c in res.coded if c.hpo and c.hpo.hpo_id in h.against]
            ruled = neg_ev[0] if neg_ev else None
        differential.append({"rank": k, "diagnosis": f"{h.name} ({h.orpha})", "orpha": h.orpha,
                             "supporting_evidence": support, "ruled_out_by": ruled, "score": h.score})
    if len(differential) < 2:
        differential.append({"rank": len(differential) + 1, "diagnosis": "证据不足,无法给出第二候选",
                             "supporting_evidence": [], "ruled_out_by": None})
    top = res.differential[0] if res.differential else None
    gene = sol["gene"].get("symbol")
    if sol["gene"].get("method", "").startswith("variant"):
        tests = ([f"Sanger 验证 {gene} 变异 {', '.join(sol['gene'].get('evidence') or [])}"] if gene
                 else [f"对候选基因 {', '.join(sol['gene'].get('candidates') or [])} 的变异做家系验证"])
    else:
        tests = ([f"基因检测({gene})"] if gene else
             [f"多基因 panel / WES(候选:{', '.join(sol['gene'].get('candidates') or [])})"] if sol["gene"].get("candidates")
             else ["罕见病遗传咨询"])
    n_var = len(sol.get("variants") or [])
    n_present = sum(1 for c in res.coded if c.assertion.polarity == "present" and c.assertion.subject == "proband")
    join = "unified" if top and len(top.matched) >= 2 else ("independent" if n_present <= 1 else "comorbidity")
    doc = {
        "differential": differential,
        "join_type": join,
        "join_reason": (f"{len(top.matched)} 条表型同时落在 {top.name} 的注释集合内(IC 相似度 {top.score})"
                        if top else "无可编码表型,不归并"),
        "tests_to_order": tests,
        "referral_specialty": ["医学遗传科", "罕见病多学科门诊"],
        "forecast": {"target_event": payload.get("prediction_context", {}).get("target_event_type"),
                     "risk": 0.5, "risk_category": "indeterminate", "confidence": 0.2,
                     "key_predictive_evidence": coded_ids[:3]},
        "drivers": [],
        "action": {"selected_action_class": "A2", "specific_action": "转诊遗传科并安排基因检测;不做药物调整",
                   "what_not_to_do": ["不据表型编码直接下诊断", "不自行调整用药"],
                   "clinician_review_required": True, "followup_interval": "14d"},
        "data_quality": {"data_sufficiency": "sufficient" if n_present >= 2 else "insufficient_data",
                         "signal_quality": {"coded_assertions": len(res.coded), "abstained": len(res.abstained),
                                            "clinvar_plp_variants": n_var, "genome": (sol.get("genome") or {}).get("counts")}},
        "cited_evidence": coded_ids,
        **sol,
    }
    return doc


class Handler(BaseHTTPRequestHandler):
    model_name = "mirobody-rare-coding"

    def log_message(self, fmt, *args):  # route to logging, not stderr
        log.debug("http %s", fmt % args)

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._send(200, {"object": "list", "data": [{"id": self.model_name, "object": "model"}]})
        if self.path.startswith("/healthz"):
            return self._send(200, {"ok": True, "model": self.model_name})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            msgs = req.get("messages") or []
            prompt = "".join(m.get("content", "") if isinstance(m.get("content"), str)
                             else "".join(p.get("text", "") for p in m.get("content", []) if isinstance(p, dict))
                             for m in msgs if m.get("role") == "user")
            t0 = time.time()
            payload = extract_payload(prompt)
            doc = answer_for_payload(payload)
            text = json.dumps(doc, ensure_ascii=False)
            log.info("[shim] case=%s coded=%d abstained=%d dx=%s gene=%s/%s variants=%d in %.2fs",
                     payload.get("case_id"), len(doc["assertions"]), len(doc["abstained"]),
                     doc["diagnosis"].get("codes", {}).get("orpha"), doc["gene"].get("symbol"),
                     doc["gene"].get("method"), len(doc.get("variants") or []), time.time() - t0)
        except Exception as e:  # noqa: BLE001
            log.exception("[shim] request failed")
            return self._send(400, {"error": {"message": f"{type(e).__name__}: {e}"}})
        p_tok, c_tok = max(len(prompt) // 4, 1), max(len(text) // 4, 1)
        self._send(200, {
            "id": f"chatcmpl-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": req.get("model") or self.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok},
        })


def main(argv: list[str] | None = None) -> None:
    cfg = load_cfg()
    ap = argparse.ArgumentParser(description="mirobody-rare OpenAI-compatible coding shim")
    ap.add_argument("--host", default=cfg["server"]["host"])
    ap.add_argument("--port", type=int, default=int(cfg["server"]["port"]))
    ap.add_argument("-v", "--verbose", type=int, default=None)
    a = ap.parse_args(argv)
    setup_logging(a.verbose)
    Handler.model_name = cfg["server"].get("model_name", Handler.model_name)
    # warm every table before accepting traffic, so the first request is not a 60s build
    from .coding import get_disease, get_hgnc, get_hpo
    t0 = time.time()
    get_hpo(); get_disease(); get_hgnc()
    log.info("[shim] tables ready in %.1fs; serving http://%s:%d/v1/chat/completions", time.time() - t0, a.host, a.port)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
