"""ingest-plan §3: pure comparison / classification / sampling / rendering, no server."""
from mirobody_rare.roundtrip.compare import classify, compare_variants, norm_gt, norm_key, truncate
from mirobody_rare.roundtrip.report import render_html, render_samples_md


def test_normalisation():
    assert norm_key("chr17", 7674220, "C", "G") == norm_key("17", "7674220", "C", "G")
    assert norm_gt("0|1") == "0/1" and norm_gt("1") == "1"


def test_compare_variants_marks_filter_as_design_not_error():
    original = [("17", 7674220, "C", "G", "0/1", 2), ("5", 70946127, "G", "T", "0/1", 2), ("1", 1, "A", "T", "0/1", 0)]   # last: 0 stars
    stored = [{"chrom": "chr17", "pos": 7674220, "ref": "C", "alt": "G", "genotype": "0|1"},
              {"chrom": "5", "pos": 70946127, "ref": "G", "alt": "T", "genotype": "0/1"}]
    res = compare_variants(original, stored, min_stars=1)
    assert res["same"] == 2 and res["design"] == 1 and res["missing"] == [] and res["extra"] == []
    res = compare_variants(original[:1], stored, min_stars=1)
    assert res["extra"] == [norm_key("5", 70946127, "G", "T")]


def test_classify_six_kinds():
    assert classify(sha_ok=False) == "transport"
    assert classify(sha_ok=True, version_ok=False) == "version"
    assert classify(sha_ok=True, version_ok=True, order_ok=False) == "order"
    assert classify(sha_ok=True, version_ok=True, order_ok=True, permitted=False) == "permission"
    assert classify(sha_ok=True, version_ok=True, order_ok=True, permitted=True, design=True) == "design"
    assert classify(sha_ok=True, version_ok=True, order_ok=True, permitted=True, design=False, equal=False) == "coding"
    assert classify(sha_ok=True, version_ok=True, order_ok=True, permitted=True, design=False, equal=True) == "same"


def test_truncate_marks_remaining_chars():
    s = "x" * 1000
    t = truncate(s, 600)
    assert t.endswith("…(+400 chars)") and len(t) < 620
    assert truncate("short", 600) == "short"


def _report():
    return {"batch": "b1", "cases": ["JD-50", "JD-55"], "env": {"world_sha": "abc", "clinvar": "clinvar_grch38_plp:2026-09-20", "schema": "mirobody_rare"},
            "counts": {"same": 40, "design": 6, "transport": 0, "version": 0, "order": 0, "permission": 2, "coding": 0},
            "layers": [{"layer": "文件", "n": 6, "same": 6, "design": 0, "real": 0}, {"layer": "变异", "n": 6, "same": 4, "design": 2, "real": 0}],
            "samples": [{"layer": "变异", "case": "JD-50", "rows": [{"before": "chr14:50269318 G>A GT 1/1", "after": "14:50269318:G:A 1/1 hom L2HGDH", "diff": "相同"}],
                         "count": {"n": 3, "k": 1, "same": 1, "design": 0, "real": 0}}],
            "gaps": ["PED 后到回填未测"]}


def test_html_is_nojs_responsive_cream_blue():
    html = render_html(_report())
    assert "<script" not in html.lower()
    assert 'name="viewport"' in html and "@media" in html and "auto-fit" in html
    assert "#fbf7ec" in html and "#1d4ed8" in html
    assert "http://" not in html.split("<body")[0] and "https://" not in html.split("<body")[0]   # no external assets in head
    assert "<details" in html and "JD-50" in html and "world_sha" in html.lower() or "abc" in html
    assert len(html.encode()) < 200_000


def test_samples_md_has_three_columns_and_count_line():
    md = render_samples_md(_report())
    assert "| 入库前" in md and "| 入库后" in md and "| 差异" in md
    assert "共 3 条,抽样 1 条" in md
