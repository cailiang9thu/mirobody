"""Every assertion points back into the source document (plan §14.2 `char_span`): slicing
the document with the span gives the assertion's own text — across sections, lines and
sentences, i.e. the offsets the text hook passes down, not sentence-relative ones."""
from mirobody_rare.text_hook import assertions_of

DOC = """# 病例 X

## 病史描述
患者近期出现癫痫发作,否认听力受损。母亲有类似病史。
自述肌张力减退,未见全面发育迟缓。

## 治疗
给予左乙拉西坦。

## 其他
父亲否认智力障碍。
"""


def test_char_span_slices_back_to_the_document():
    pairs = assertions_of(DOC)
    assert len(pairs) >= 5
    for ev, a in pairs:
        s, e = a.char_span
        assert e > s and DOC[s:e] == a.text, (ev, a.text, DOC[s:e])
    assert not any("左乙拉西坦" in a.text for _, a in pairs)        # 治疗 section is skipped
    assert any(a.subject == "relative" and "父亲" in a.text for _, a in pairs)
