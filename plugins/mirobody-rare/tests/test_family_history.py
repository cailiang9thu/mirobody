"""`query_family_history`: one table from three sources, each row labelled, each gap named."""
from mirobody_rare.repo import MemoryRepo
from mirobody_rare.tools import RareQueryService


async def _setup():
    repo = MemoryRepo()
    # pedigree: proband u1, father u7 (account, in circle), mother u8 (account, NOT in circle), sister no account
    await repo.upsert_pedigree({"family_id": "F1"}, [
        {"individual_id": "F1-P", "paternal_id": "F1-F", "maternal_id": "F1-M", "sex": 1, "affected": 2, "is_proband": True, "analysis_only": False, "user_id": "u1"},
        {"individual_id": "F1-F", "paternal_id": None, "maternal_id": None, "sex": 1, "affected": 1, "is_proband": False, "analysis_only": True, "user_id": "u7"},
        {"individual_id": "F1-M", "paternal_id": None, "maternal_id": None, "sex": 2, "affected": 2, "is_proband": False, "analysis_only": True, "user_id": "u8"},
        {"individual_id": "F1-S", "paternal_id": "F1-F", "maternal_id": "F1-M", "sex": 2, "affected": 2, "is_proband": False, "analysis_only": True, "user_id": None}])
    repo.t["care_circle"].append({"operator": "u1", "subject": "u7", "access": 1})
    await repo.add_phenotypes([
        {"user_id": "u7", "hpo_id": "HP:0001250", "hpo_label": "癫痫发作", "negated": False, "subject": "proband", "source": "clinician"},
        {"user_id": "u8", "hpo_id": "HP:0003002", "hpo_label": "乳腺癌", "negated": False, "subject": "proband", "source": "clinician"},
        {"user_id": "u1", "hpo_id": "HP:0003002", "hpo_label": "乳腺癌", "negated": False, "subject": "relative", "subject_role": "mother",
         "source": "nlp", "source_text": "母亲也出现过乳腺癌"},
        {"user_id": "u1", "hpo_id": "HP:0001332", "hpo_label": "肌张力障碍", "negated": False, "subject": "proband", "source": "nlp"}])
    repo.t["th_disease_code"].append({"id": 1, "user_id": "u7", "system": "ORPHA", "code": "ORPHA:79314", "label": "L-2-HGA", "status": "candidate", "source": "nlp"})
    return repo


async def test_family_history_merges_three_sources_and_names_gaps():
    repo = await _setup()
    r = await RareQueryService(repo).query_family_history({"user_id": "u1"})
    assert r["status"] == "ok"
    rows = r["data"]
    by = {}
    for x in rows:
        by.setdefault(x["member"], []).append(x)
    # father: in circle → his own record is read
    assert any(x["source"] == "relative_account" and x["hpo_id"] == "HP:0001250" for x in by["F1-F"])
    assert any(x["source"] == "relative_account" and x.get("code") == "ORPHA:79314" for x in by["F1-F"])
    # mother: has an account but is NOT in the circle → her record is not read, only the proband's narrative
    assert all(x["source"] != "relative_account" for x in by["F1-M"])
    assert any(x["source"] == "narrative_in_proband_record" and x["hpo_id"] == "HP:0003002" for x in by["F1-M"])
    # the gap is stated, not silently absent
    assert any("F1-M" in g and "not in" in g for g in r["gaps"])
    assert any("F1-S" in g and "no account" in g for g in r["gaps"])
    # proband's own phenotype is not family history
    assert not any(x["hpo_id"] == "HP:0001332" for x in rows)
    assert r["assumptions"]


async def test_query_phenotype_subject_filter():
    repo = await _setup()
    svc = RareQueryService(repo)
    r = await svc.query_phenotype({"user_id": "u1"}, subject="relative")
    assert [x["hpo_id"] for x in r["data"]] == ["HP:0003002"]
    r = await svc.query_phenotype({"user_id": "u1"}, subject="proband")
    assert [x["hpo_id"] for x in r["data"]] == ["HP:0001332"]
