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
