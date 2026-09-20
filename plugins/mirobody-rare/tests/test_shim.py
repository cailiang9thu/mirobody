import json

from mirobody_rare.serve_coding import answer_for_payload, extract_payload

PROMPT = "你是求解器。\n\npre-T 数据(JSON):\n" + json.dumps({"case_id": "X", "prediction_context": {"target_event_type": "t"},
    "evidence_ledger": [{"evidence_id": "EV-1", "symptom": "出现共济失调"}, {"evidence_id": "EV-2", "symptom": "否认肝脾肿大"}]},
    ensure_ascii=False) + "\n\n只输出一个 JSON 对象"


def test_extract_and_answer():
    p = extract_payload(PROMPT)
    assert p["case_id"] == "X"
    doc = answer_for_payload(p)
    assert {"differential", "join_type", "action", "cited_evidence", "assertions", "diagnosis", "gene"} <= set(doc)
    assert len(doc["differential"]) >= 2
    ids = {a["evidence_id"]: a for a in doc["assertions"]}
    assert ids["EV-1"]["codes"]["hpo"] == "HP:0001251" and ids["EV-2"]["polarity"] == "absent"
