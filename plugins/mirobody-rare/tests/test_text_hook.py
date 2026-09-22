"""ingest-plan §2: narrative documents run the rare coder through the `mirobody.text_hooks`
entry point and land in th_phenotype / th_disease_code / th_phenotype_review."""
import asyncio

import pytest

from mirobody.collect.files.text_hooks import TextHookContext, _text_hooks
from mirobody_rare.repo import MemoryRepo
from mirobody_rare.text_hook import HOOKS, run_rare_coding, split_sections

DOC = """# 出院小结
## 病史描述
患者男性，6岁。1岁时出现癫痫发作较明显，近期失语症。母亲也出现过乳腺癌。
## 查体
未见听力受损，否认全面发育迟缓。肝脾肿大、黄疸均正常。
## 辅助检查
血红蛋白 118 g/L。
## 诊断
考虑 L-2-羟基戊二酸尿症。
"""


def test_main_package_loader_is_empty_without_plugins(monkeypatch):
    import importlib.metadata as md
    import mirobody.collect.files.text_hooks as th
    monkeypatch.setattr(th, "_HOOKS", None)
    monkeypatch.setattr(md, "entry_points", lambda **kw: [])
    assert _text_hooks() == []


def test_plugin_exports_hooks():
    assert HOOKS and all(callable(h) for h in HOOKS)


def test_sections_are_routed():
    secs = split_sections(DOC)
    assert [s[0] for s in secs] == ["病史描述", "查体", "辅助检查", "诊断"]


async def test_hook_writes_phenotypes_disease_and_review():
    repo = MemoryRepo()
    ctx = TextHookContext(text=DOC, user_id="u1", operator_id="u1", file_key="k1", file_name="a.md", message_id=None, content_hash="h1")
    out = await run_rare_coding(ctx, repo=repo, file_id=42)
    rows = await repo.phenotypes("u1")
    assert {r["hpo_id"] for r in rows} >= {"HP:0001250", "HP:0002381"}                   # seizure, aphasia
    assert any(r["subject"] == "relative" and r["subject_role"] == "mother" for r in rows)
    assert any(r["negated"] and r["hpo_id"] == "HP:0000365" for r in rows)                # 听力受损 absent
    assert all(r["file_id"] == 42 and r["source"] == "nlp" and r["source_text"] for r in rows)
    assert all(r.get("section") in ("病史描述", "查体") for r in rows)
    codes = await repo.disease_codes("u1")
    assert codes and codes[0]["system"] == "ORPHA" and codes[0]["status"] == "candidate"
    reviews = repo.t["th_phenotype_review"]
    assert any(r["kind"] == "abstained" for r in reviews)                                  # 血红蛋白 118 g/L → lab, not coded
    assert out["phenotypes"] >= 4 and out["review"] >= 1


async def test_hook_dedups_by_content_hash():
    repo = MemoryRepo()
    ctx = TextHookContext(text=DOC, user_id="u1", operator_id="u1", file_key="k1", file_name="a.md", message_id=None, content_hash="h1")
    await run_rare_coding(ctx, repo=repo, file_id=1)
    n = len(await repo.phenotypes("u1"))
    out = await run_rare_coding(ctx, repo=repo, file_id=2)
    assert out.get("duplicate") and len(await repo.phenotypes("u1")) == n


async def test_resolve_review_tool_writes_clinician_row():
    from mirobody_rare.tools import RareQueryService
    repo = MemoryRepo()
    rid = await repo.add_review({"user_id": "u1", "file_id": 1, "kind": "ambiguous", "source_text": "肌无力", "candidates": ["HP:0001324", "HP:0010547"]})
    r = await RareQueryService(repo).resolve_phenotype_review({"user_id": "u1"}, review_id=rid, hpo_id="HP:0010547")
    assert r["status"] == "ok"
    rows = await repo.phenotypes("u1")
    assert rows and rows[0]["hpo_id"] == "HP:0010547" and rows[0]["source"] == "clinician"
    assert repo.t["th_phenotype_review"][0]["resolved_hpo"] == "HP:0010547"


def test_a_sentence_is_not_a_heading_even_when_it_starts_with_a_section_word():
    """`查体无听力受损。` opens with 查体 and is short, so the old rule filed it as a SECTION
    heading — and a heading is not coded, so the finding was silently lost (7 of 222 gold
    terms in haenv batch rare_coding-p3). A line that ends in sentence punctuation is a
    sentence; a section name is the name itself, not a name plus a clause."""
    from mirobody_rare.text_hook import assertions_of, split_sections
    doc = ("## 体格检查\n生命体征平稳，神志清楚。\n查体无听力受损。\n查体未见肝大。\n"
           "既往史：无特殊。\n现病史\n起病初出现癫痫发作。\n")
    names = [n for n, _ in split_sections(doc)]
    assert "体格检查" in names and "现病史" in names
    assert not any("听力受损" in n or "肝大" in n or "无特殊" in n for n in names), names
    texts = [a.text for _, a in assertions_of(doc)]
    assert any("听力受损" in t for t in texts), texts
    assert any("肝大" in t for t in texts), texts
    pol = {a.text: a.polarity for _, a in assertions_of(doc)}
    assert all(v == "absent" for k, v in pol.items() if "听力受损" in k or "肝大" in k)


def test_markdown_and_bare_section_names_still_split():
    from mirobody_rare.text_hook import split_sections
    doc = "# 病例 X\n\n## 主诉\n因头痛就诊。\n\n家族史\n母亲有糖尿病史。\n\n实验室检查：\n血常规正常。\n"
    names = [n for n, _ in split_sections(doc)]
    for want in ("主诉", "家族史", "实验室检查"):
        assert want in names, (want, names)
