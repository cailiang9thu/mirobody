"""Deleting an uploaded file erases what the plugin derived from it (erase.py, main-package
`mirobody.delete_hooks`). Before this, a deleted VCF still answered `query_variant`, a deleted
narrative kept its HPO rows, and a deleted parent's VCF left the proband's `biparental` in place."""
from mirobody_rare.erase import erase_file
from mirobody_rare.ingest import ingest_dicom, ingest_ped, ingest_vcf
from mirobody_rare.repo import MemoryRepo

ROOT = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50"
DICOM = "/data/xfs_recovery/data/rare/03_dicom/UPENN-GBM/UPENN-GBM-00005/1.3.6.1.4.1.14519.5.2.1.61459741640552642122253987509717553365.zip"


class _LocalStorage:
    async def get(self, key):
        try:
            return open(key, "rb").read(), None
        except OSError as e:
            return None, str(e)


def _file(repo, uid, key, content_hash, fid=None, is_del=False):
    fid = fid or len(repo.t["th_files"]) + 1
    repo.t["th_files"].append({"id": fid, "user_id": uid, "query_user_id": None, "file_key": key,
                               "content_hash": content_hash, "is_del": is_del})
    return fid


async def _trio(repo):
    for uid, role, sex in (("u1", "proband", "M"), ("u7", "father", "M"), ("u8", "mother", "F")):
        key = f"{ROOT}/{role}.vcf.gz"
        await ingest_vcf(repo, uid, key, sex=sex, file_key=key)
        _file(repo, uid, key, f"sha-{role}")
    _file(repo, "u1", f"{ROOT}/JD-50.ped", "sha-ped")
    await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u1", "JD-50-F": "u7", "JD-50-M": "u8"},
                     owner_id="u1", file_key=f"{ROOT}/JD-50.ped")
    repo.t["care_circle"] += [{"operator": "u1", "subject": "u7", "access": 0}, {"operator": "u1", "subject": "u8", "access": 0}]
    from mirobody_rare.genome import trio_backfill
    await trio_backfill(repo, "u1", storage=_LocalStorage())


def _l2(variants):
    return next(v for v in variants if v["gene_symbol"] == "L2HGDH")


async def test_deleting_a_vcf_erases_its_sample_variants_and_annotations_only():
    repo = MemoryRepo()
    await _trio(repo)
    mine = [v["id"] for v in await repo.variants("u1", limit=10 ** 6)]
    assert mine and repo.t["th_variant_annotation"]
    out = await erase_file(repo, "u1", f"{ROOT}/proband.vcf.gz", storage=_LocalStorage())
    assert out["action"] == "erased" and out["samples"] == 1 and out["variants"] == len(mine)
    assert await repo.variants("u1") == [] and await repo.samples_of("u1") == []
    assert not [a for a in repo.t["th_variant_annotation"] if a["variant_id"] in mine]
    assert await repo.variants("u7") and await repo.samples_of("u8")            # the parents' own data is theirs
    assert await repo.pedigree_of("u1")                                          # the PED is a different file


async def test_deleting_a_parents_vcf_resets_the_probands_trio_inheritance():
    repo = MemoryRepo()
    await _trio(repo)
    assert _l2(await repo.variants("u1"))["inheritance"] == "biparental"
    out = await erase_file(repo, "u7", f"{ROOT}/father.vcf.gz", storage=_LocalStorage())
    assert "u1" in out["inheritance"]                                            # the proband it fed was recomputed
    l2 = _l2(await repo.variants("u1"))
    assert l2["inheritance"] == "unknown" and l2["is_de_novo"] is None           # not the stale biparental


async def test_deleting_the_ped_erases_the_family_and_the_inheritance_it_enabled():
    repo = MemoryRepo()
    await _trio(repo)
    out = await erase_file(repo, "u1", f"{ROOT}/JD-50.ped", storage=_LocalStorage())
    assert out["pedigrees"] == 1 and set(out["pedigree_users"]) == {"u1", "u7", "u8"}
    assert await repo.pedigree_of("u1") is None and not repo.t["th_pedigree_member"]
    assert _l2(await repo.variants("u1"))["inheritance"] == "unknown"
    assert await repo.samples_of("u7")                                           # samples stay: they are other files


async def test_deleting_a_narrative_erases_its_phenotypes_reviews_and_diagnosis():
    repo = MemoryRepo()
    fid, other = _file(repo, "u1", "web_uploads/a.md", "sha-a"), _file(repo, "u1", "web_uploads/b.md", "sha-b")
    await repo.add_phenotypes([{"user_id": "u1", "hpo_id": "HP:0001250", "hpo_label": "Seizure", "file_id": fid, "source": "nlp"},
                               {"user_id": "u1", "hpo_id": "HP:0001263", "hpo_label": "GDD", "file_id": other, "source": "nlp"},
                               {"user_id": "u1", "hpo_id": "HP:0002019", "hpo_label": "Constipation", "file_id": fid, "source": "clinician"}])
    await repo.add_review({"user_id": "u1", "file_id": fid, "kind": "ambiguous", "source_text": "x", "candidates": []})
    await repo.add_disease_code({"user_id": "u1", "system": "ORPHA", "code": "ORPHA:1", "label": "d", "file_id": fid, "source": "nlp"})
    out = await erase_file(repo, "u1", "web_uploads/a.md")
    assert (out["phenotypes"], out["reviews"], out["disease_codes"]) == (2, 1, 1)
    assert [p["hpo_id"] for p in await repo.phenotypes("u1")] == ["HP:0001263"]  # the other document's row stays


async def test_a_live_copy_of_the_same_bytes_keeps_the_rows_and_takes_them_over():
    """Two identical uploads: ingest reuses the first sample. Deleting the FIRST must not orphan it."""
    repo = MemoryRepo()
    key = f"{ROOT}/proband.vcf.gz"
    await ingest_vcf(repo, "u1", key, sex="M", file_key=key)
    _file(repo, "u1", key, "sha-p", fid=1, is_del=True)                          # the main package soft-deleted it first
    _file(repo, "u1", "web_uploads/copy.vcf.gz", "sha-p", fid=2)
    n = len(await repo.variants("u1", limit=10 ** 6))
    out = await erase_file(repo, "u1", key)
    assert out["action"] == "repointed" and out["to"] == "web_uploads/copy.vcf.gz" and out["samples"] == 1
    assert len(await repo.variants("u1", limit=10 ** 6)) == n
    assert (await repo.samples_of("u1"))[0]["file_key"] == "web_uploads/copy.vcf.gz"


async def test_deleting_a_dicom_zip_erases_its_index_row():
    repo = MemoryRepo()
    await ingest_dicom(repo, "u1", DICOM, file_id=0, file_key="web_uploads/s.zip")
    await ingest_dicom(repo, "u1", DICOM, file_id=0, file_key="web_uploads/t.zip")
    out = await erase_file(repo, "u1", "web_uploads/s.zip")
    assert out["signals"] == 1 and [s["file_key"] for s in await repo.signals("u1")] == ["web_uploads/t.zip"]


async def test_the_delete_hook_is_registered_for_the_main_package():
    from importlib.metadata import entry_points
    assert "mirobody_rare.erase" in [e.value for e in entry_points(group="mirobody.delete_hooks")]
