import pytest

from mirobody_rare.coding import code_ledger
from mirobody_rare.hpo import get_adapter


@pytest.fixture(scope="module")
def hpo():
    return get_adapter()


def test_hpo_exact_and_contains(hpo):
    r = hpo.resolve("癫痫发作")
    assert r.resolved and r.hpo_id == "HP:0001250" and r.method == "exact"
    r2 = hpo.resolve("Seizure")
    assert r2.hpo_id == "HP:0001250"
    r3 = hpo.resolve("患者出现癫痫发作较明显")
    assert r3.hpo_id == "HP:0001250" and r3.method == "contains"


def test_hpo_abstains_on_nonsense(hpo):
    assert not hpo.resolve("整理文件时划到").resolved


def test_ledger_roundtrip():
    ledger = [
        {"evidence_id": "EV-1", "symptom": "癫痫发作较明显"},
        {"evidence_id": "EV-2", "symptom": "未见听力受损"},
        {"evidence_id": "EV-3", "symptom": "姐姐有小头畸形史"},
        {"evidence_id": "EV-4", "symptom": "右手食指被纸张划伤", "context": "贴创可贴次日愈合"},
        {"evidence_id": "EV-5", "note": "参加线上读书会"},
    ]
    res = code_ledger(ledger)
    d = res.to_solver()
    by = {a["evidence_id"]: a for a in d["assertions"]}
    assert by["EV-1"]["codes"]["hpo"] == "HP:0001250" and by["EV-1"]["polarity"] == "present"
    assert by["EV-2"]["polarity"] == "absent" and by["EV-2"]["codes"]["hpo"].startswith("HP:")
    assert by["EV-3"]["subject"] == "relative" and by["EV-3"]["codes"]["hpo"] == "HP:0000252"
    assert "EV-5" not in by
    assert d["diagnosis"]["codes"].get("orpha", "").startswith("ORPHA:")
