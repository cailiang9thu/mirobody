"""Diagnosis refresh: once a P/LP variant is on file, the phenotype-ranked candidate is
re-checked against variant evidence (the same promotion rule the shim applies)."""
import pytest

from mirobody_rare.repo import MemoryRepo

SMA_HPO = ["HP:0003701", "HP:0003551", "HP:0031108", "HP:0003731", "HP:0003202"]   # JD-77 gold (proximal weakness ...)


async def _seed(repo, uid="u1", with_variant=True, af_popmax=None):
    await repo.add_phenotypes([{"user_id": uid, "hpo_id": h, "hpo_label": h, "negated": False, "subject": "proband",
                                "source": "nlp", "source_text": h, "confidence": 1.0} for h in SMA_HPO])
    await repo.add_disease_code({"user_id": uid, "system": "ORPHA", "code": "ORPHA:45448", "label": "Miyoshi myopathy",
                                 "status": "candidate", "source": "nlp", "confidence": 0.4, "file_id": None})
    if with_variant:
        sid = await repo.add_sample({"user_id": uid, "file_id": None, "assay": "wgs", "reference": "GRCh38", "sample_label": "p", "status": "ready"})
        await repo.add_variants([{"user_id": uid, "sample_id": sid, "chrom": "5", "pos": 70925108, "ref": "C", "alt": "G", "genotype": "1/1",
                                  "zygosity": "hom", "gene_symbol": "SMN1", "filter": "PASS"}])
        v = (await repo.variants(uid))[0]
        await repo.add_annotations([{"variant_id": v["id"], "source": "clinvar", "source_version": "t", "clinical_significance": "Pathogenic", "review_status": "x"},
                                    {"variant_id": v["id"], "source": "gnomad", "source_version": "t", "af_popmax": af_popmax, "af_global": None}])


@pytest.mark.asyncio
async def test_variant_in_causal_gene_promotes_the_disorder():
    from mirobody_rare.dx_refresh import refresh_diagnosis
    from mirobody_rare.disease.adapter import get_adapter
    repo = MemoryRepo()
    await _seed(repo)
    out = await refresh_diagnosis(repo, "u1")
    codes = [d for d in await repo.disease_codes("u1") if d["source"] == "nlp+variant"]
    assert out["promoted"] and len(codes) == 1
    assert codes[0]["code"] == "ORPHA:70" and "SMN1" in codes[0]["source_text"]     # group disorder: gene via its OMIM ids
    assert codes[0]["code"] == out["promoted"] and codes[0]["status"] == "candidate"
    # idempotent: a second pass does not duplicate the row
    await refresh_diagnosis(repo, "u1")
    assert len([d for d in await repo.disease_codes("u1") if d["source"] == "nlp+variant"]) == 1


@pytest.mark.asyncio
async def test_no_variant_or_common_variant_changes_nothing():
    from mirobody_rare.dx_refresh import refresh_diagnosis
    repo = MemoryRepo()
    await _seed(repo, with_variant=False)
    assert (await refresh_diagnosis(repo, "u1"))["promoted"] is None
    repo2 = MemoryRepo()
    await _seed(repo2, af_popmax=0.2)                  # gnomAD-common even under the recessive ceiling
    assert (await refresh_diagnosis(repo2, "u1"))["promoted"] is None
    assert not [d for d in await repo2.disease_codes("u1") if d["source"] == "nlp+variant"]


@pytest.mark.asyncio
async def test_same_code_as_phenotype_candidate_is_not_duplicated():
    from mirobody_rare.dx_refresh import refresh_diagnosis
    repo = MemoryRepo()
    await _seed(repo)
    for d in repo.t["th_disease_code"]:
        d["code"] = "ORPHA:70"                         # the phenotype ranking already put SMA first
    out = await refresh_diagnosis(repo, "u1")
    assert out["promoted"] == "ORPHA:70" and out["reason"] == "already the phenotype candidate"
    assert len(await repo.disease_codes("u1")) == 1
