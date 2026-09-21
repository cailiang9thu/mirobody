import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("MIROBODY_RARE_PG_DSN"), reason="MIROBODY_RARE_PG_DSN not set")
VCF = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz"
PED = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50/JD-50.ped"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def test_pg_repo_roundtrip():
    from mirobody_rare.ingest import ingest_ped, ingest_vcf
    from mirobody_rare.reference import run_schema
    from mirobody_rare.repo import PgRepo
    from mirobody_rare.tools import RareQueryService
    await run_schema()
    repo = PgRepo()
    uid = "pytest-rare-u1"
    pool = await repo._p()
    for t in ("th_variant_annotation", "th_variant", "th_sequencing_sample", "th_phenotype", "th_signal_object", "th_consent"):
        await pool.execute(f"DELETE FROM {t} WHERE user_id = $1" if t not in ("th_variant_annotation",) else
                           "DELETE FROM th_variant_annotation WHERE variant_id IN (SELECT id FROM th_variant WHERE user_id = $1)", uid)
    res = await ingest_vcf(repo, uid, VCF, sex="F")
    assert res["status"] == "ready" and res["kept"] >= 1
    n1 = len(await repo.variants(uid, limit=10 ** 6))
    res2 = await ingest_vcf(repo, uid, VCF, sex="F", sample_id=res["sample_id"])
    assert res2["shards"] == 0 and len(await repo.variants(uid, limit=10 ** 6)) == n1        # resume: no duplicates
    rows = await repo.variants(uid, genes=("TP53",))
    assert rows and rows[0]["annotations"][0]["clinical_significance"].startswith("Pathogenic")
    svc = RareQueryService(repo)
    r = await svc.query_variant({"user_id": uid}, genes=["TP53"])
    assert r["status"] == "ok" and r["data"][0]["gene_symbol"] == "TP53" and r["assumptions"]
    await repo.add_phenotypes([{"user_id": uid, "hpo_id": "HP:0001250", "hpo_label": "癫痫发作", "negated": False, "source": "nlp"}])
    r = await svc.query_phenotype({"user_id": uid})
    assert [x["hpo_id"] for x in r["data"]] == ["HP:0001250"]
    pid = await ingest_ped(repo, PED, user_ids={"JD-50-M": "pytest-rare-mother"})
    assert pid and await repo.is_analysis_only("pytest-rare-mother")
    r = await svc.query_variant({"user_id": "pytest-rare-mother"})
    assert r["status"] == "error" and r["error_kind"] == "denied"


async def test_pg_clinvar_lookup_many():
    from mirobody_rare.variant.clinvar import ClinVarPg
    cv = ClinVarPg()
    got = cv.lookup_many([("17", 7674220, "C", "G"), ("1", 1, "A", "T")])
    assert ("17", 7674220, "C", "G") in got and got[("17", 7674220, "C", "G")]["gene"] == "TP53"
    assert ("1", 1, "A", "T") not in got


async def test_pg_signal_index_roundtrip():
    import glob
    from mirobody_rare.ingest import ingest_dicom
    from mirobody_rare.repo import PgRepo
    from mirobody_rare.tools import RareQueryService
    sts = sorted(glob.glob("/data/xfs_recovery/data/rare/03_dicom/Soft-tissue-Sarcoma/STS_027/*.zip"))
    if not sts:
        pytest.skip("TCIA sample not on disk")
    repo = PgRepo()
    uid = "pytest-rare-u1"
    row = await ingest_dicom(repo, uid, sts[0], file_id=1)
    assert row["deid_status"] == "done" and row["id"]
    r = await RareQueryService(repo).query_signal_index({"user_id": uid})
    assert any(x["id"] == row["id"] and x["study_date"] for x in r["data"])
