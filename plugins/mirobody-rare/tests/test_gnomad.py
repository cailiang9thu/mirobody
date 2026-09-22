"""§4.4 ④ gnomAD: population frequency per candidate, popmax > threshold flagged common."""
import json

import pytest

from mirobody_rare.variant.gnomad import GnomadClient, parse_variant, VARIANT_FIELDS


def _fake_response(vid):
    if vid == "19-44908684-T-C":
        return {"variant_id": vid,
                "exome": {"ac": 200000, "an": 1400000, "af": 0.142857, "populations": [{"id": "afr", "ac": 8000, "an": 30000}, {"id": "nfe", "ac": 100000, "an": 1000000}]},
                "genome": {"ac": 20000, "an": 150000, "af": 0.1333, "populations": [{"id": "afr", "ac": 900, "an": 3000}]}}
    return None


def test_parse_combines_exome_and_genome_and_finds_popmax():
    rec = parse_variant(_fake_response("19-44908684-T-C"))
    assert abs(rec["af_global"] - (220000 / 1550000)) < 1e-6
    assert rec["popmax_pop"] == "afr" and abs(rec["af_popmax"] - (8900 / 33000)) < 1e-6
    assert rec["allele_count"] == 220000


def test_absent_variant_is_recorded_as_not_found_not_zero():
    rec = parse_variant(None)
    assert rec["af_global"] is None and rec["not_found"] is True


async def test_client_batches_and_caches(tmp_path):
    calls = []

    async def fetch(query):
        calls.append(query)
        return {"data": {f"v{i}": _fake_response(v) for i, v in enumerate(["19-44908684-T-C", "1-1-A-T"])}}
    c = GnomadClient(cache_dir=tmp_path, fetch=fetch)
    out = await c.lookup_many([("19", 44908684, "T", "C"), ("1", 1, "A", "T")])
    assert out[("19", 44908684, "T", "C")]["af_popmax"] > 0.01 and out[("1", 1, "A", "T")]["not_found"]
    assert len(calls) == 1 and "v1:" in calls[0]
    out2 = await c.lookup_many([("19", 44908684, "T", "C")])
    assert len(calls) == 1 and out2[("19", 44908684, "T", "C")]["af_global"] == out[("19", 44908684, "T", "C")]["af_global"]


def test_common_variant_is_dropped_from_gene_candidates():
    from mirobody_rare.genome import filter_common
    from mirobody_rare.variant.vcf import VariantCall
    cv = {"vid": "1", "clnsig": "Pathogenic", "rev": "", "stars": 1, "gene": "APOE", "dn": [], "disdb": "", "mc": ""}
    a = VariantCall("19", 44908684, "T", "C", "0/1", "het", clinvar=cv); a.gnomad = {"af_popmax": 0.27, "af_global": 0.14}
    b = VariantCall("17", 7674220, "C", "G", "0/1", "het", clinvar={**cv, "gene": "TP53"}); b.gnomad = {"af_popmax": None, "af_global": None, "not_found": True}
    kept, common = filter_common([a, b], max_af_popmax=0.01)
    assert [v.gene for v in kept] == ["TP53"] and [v.gene for v in common] == ["APOE"]


def test_popmax_ignores_bottleneck_populations_and_relaxes_for_homozygous():
    """Founder mutations (HMBS in fin, MEFV in mid/asj) sit at 2–3% in a bottleneck population and
    below 0.1% everywhere else; a 1% popmax over ALL populations deletes the true variant (P5e)."""
    from mirobody_rare.genome import filter_common
    from mirobody_rare.variant.vcf import VariantCall
    v = {"variant_id": "19-44908684-T-C", "exome": {"ac": 300, "an": 1400000, "af": 0.0002, "populations": [
        {"id": "fin", "ac": 260, "an": 10000}, {"id": "nfe", "ac": 40, "an": 1000000}, {"id": "mid", "ac": 0, "an": 5000}]}}
    rec = parse_variant(v)
    assert rec["popmax_pop"] == "nfe" and rec["af_popmax"] < 0.001            # fin excluded from popmax
    assert rec["af_popmax_all"] > 0.02 and rec["popmax_pop_all"] == "fin"     # but still reported
    cv = {"vid": "1", "clnsig": "Pathogenic", "rev": "", "stars": 2, "gene": "MEFV", "dn": [], "disdb": "", "mc": ""}
    hom = VariantCall("16", 3243407, "T", "C", "1/1", "hom", clinvar=cv); hom.gnomad = {"af_popmax": 0.03, "af_global": 0.004}
    het = VariantCall("16", 3243407, "T", "C", "0/1", "het", clinvar=cv); het.gnomad = {"af_popmax": 0.03, "af_global": 0.004}
    kept, common = filter_common([hom, het], max_af_popmax=0.01, max_af_popmax_recessive=0.05)
    assert [x.gt for x in kept] == ["1/1"] and [x.gt for x in common] == ["0/1"]


async def test_stale_cache_entry_without_continental_popmax_is_refetched(tmp_path):
    """Cache files written before the continental-popmax fix carry the bottleneck popmax
    (HMBS 19-44908684-T-C: hgdp:maya 0.026) and no `af_popmax_all`; they must not be trusted."""
    import json
    (tmp_path / "19-44908684-T-C.json").write_text(json.dumps(
        {"af_global": 6e-06, "af_popmax": 0.0263, "popmax_pop": "hgdp:maya", "allele_count": 10, "not_found": False}))
    calls = []

    async def fetch(query):
        calls.append(query)
        return {"data": {"v0": _fake_response("19-44908684-T-C")}}
    c = GnomadClient(cache_dir=tmp_path, fetch=fetch)
    out = await c.lookup_many([("19", 44908684, "T", "C")])
    assert len(calls) == 1 and "af_popmax_all" in out[("19", 44908684, "T", "C")]
    assert "af_popmax_all" in json.loads((tmp_path / "19-44908684-T-C.json").read_text())


def test_popmax_ignores_hgdp_1kg_subcohorts():
    """gnomAD v4 genomes also report HGDP/1KG sub-cohorts (`hgdp:maya`, n≈38): one carrier there is
    2.6 % (HMBS 11-119091431-C-T, P5f miss). popmax is over continental populations only."""
    v = {"variant_id": "11-119091431-C-T", "genome": {"ac": 10, "an": 1500000, "af": 6e-06, "populations": [
        {"id": "hgdp:maya", "ac": 1, "an": 38}, {"id": "nfe", "ac": 8, "an": 1200000}, {"id": "1kg:gbr", "ac": 1, "an": 180}]}}
    rec = parse_variant(v)
    assert rec["popmax_pop"] == "nfe" and rec["af_popmax"] < 0.001
    assert rec["popmax_pop_all"] == "hgdp:maya"
