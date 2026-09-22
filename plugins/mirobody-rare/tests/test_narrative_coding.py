"""Coding a narrative record (`apply_narrative`): the same rules the upload text hook uses,
each row carrying a char_span into the file, life-event sentences abstained not coded."""
import pytest

from mirobody_rare.coding import CodingResult, apply_narrative

DOC = """# 病例 T-1

## 主诉
患者女性，40-44岁，因“肿瘤”就诊。

## 现病史
起病初出现横纹肌肉瘤。
近日搬家，作息尚在调整。
此后自述乳腺癌。

## 家族史
母亲也出现过乳腺癌。

## 体格检查
生命体征平稳，神志清楚，对答切题。
视力异常阴性。
"""


@pytest.fixture
def coded(tmp_path):
    p = tmp_path / "T-1.md"
    p.write_text(DOC, encoding="utf-8")
    res = apply_narrative(CodingResult(), {"narrative": {"path": str(p), "sha256": "x"}})
    return res.narrative, DOC


def test_every_assertion_slices_back_to_the_file(coded):
    nar, doc = coded
    assert nar and nar["chars"] == len(doc)
    assert nar["assertions"], "nothing coded"
    for a in nar["assertions"]:
        s, e = a["char_span"]
        assert doc[s:e] == a["text"], (a["text"], doc[s:e])


def test_terms_polarity_and_subject_come_from_the_prose(coded):
    nar, doc = coded
    by_text = {a["text"]: a for a in nar["assertions"]}
    rhabdo = next(a for t, a in by_text.items() if "横纹肌肉瘤" in t)
    assert rhabdo["polarity"] == "present" and rhabdo["subject"] == "proband"
    fam = next(a for t, a in by_text.items() if "母亲" in t)
    assert fam["subject"] == "relative"
    neg = next(a for t, a in by_text.items() if "视力异常" in t)
    assert neg["polarity"] == "absent"


def test_life_event_sentence_is_not_coded(coded):
    nar, _ = coded
    coded_texts = " ".join(a["text"] for a in nar["assertions"] if a.get("codes"))
    assert "搬家" not in coded_texts


def test_missing_file_leaves_the_result_untouched():
    res = apply_narrative(CodingResult(), {"narrative": {"path": "/nonexistent/x.md"}})
    assert res.narrative is None
    assert apply_narrative(CodingResult(), None).narrative is None
