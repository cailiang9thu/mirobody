"""亲属数据走关爱圈:relatives' data lives under their own account; reading it, uploading for
them and computing with it all go through care-circle membership. Pedigree adds biology only."""
import pytest

from mirobody_rare.consent import permit
from mirobody_rare.ingest import ingest_ped, ingest_vcf
from mirobody_rare.pedigree import map_ped_to_circle, parse_ped
from mirobody_rare.repo import MemoryRepo

ROOT = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-50"


async def test_permit_requires_care_circle_for_someone_else():
    repo = MemoryRepo()
    assert not (await permit(repo, "u1", "u7", "variant")).allowed                 # not in any shared circle
    repo.t["care_circle"].append({"operator": "u1", "subject": "u7", "access": 1})  # u7 shares view with u1
    assert (await permit(repo, "u1", "u7", "variant")).allowed                     # circle = the person's own consent
    assert not (await permit(repo, "u1", "u7", "variant", "research_use")).allowed  # research still needs th_consent
    repo.t["th_consent"].append({"id": 1, "user_id": "u7", "scope": "research_use", "granted": True, "layer": None,
                                 "revoked_at": None, "cross_border_allowed": False})
    assert (await permit(repo, "u1", "u7", "variant", "research_use")).allowed
    repo.t["care_circle"].clear()
    assert not (await permit(repo, "u1", "u7", "variant", "research_use")).allowed  # consent without circle: still no


def test_ped_members_map_to_circle_accounts(tmp_path):
    p = tmp_path / "f.ped"
    p.write_text("F1\tF1-P\tF1-F\tF1-M\t1\t2\nF1\tF1-F\t0\t0\t1\t1\nF1\tF1-M\t0\t0\t2\t1\nF1\tF1-S\t0\t0\t2\t1\n")
    members = [{"user_id": 7, "nickname": "F1-F", "name": "Dad", "email": "dad@x"},
               {"user_id": 8, "nickname": None, "name": "F1-M", "email": "mom@x"},
               {"user_id": 9, "nickname": "grandpa", "name": "", "email": "gp@x"}]
    m = map_ped_to_circle(parse_ped(p), members, uploader_id="1")
    assert m == {"F1-P": "1", "F1-F": "7", "F1-M": "8"}                             # F1-S has no account: stays unmapped


class _LocalStorage:
    async def get(self, key):
        try:
            return open(key, "rb").read(), None
        except OSError as e:
            return None, str(e)


async def test_trio_backfill_reads_parents_from_their_own_accounts():
    from mirobody_rare.genome import trio_backfill
    repo = MemoryRepo()
    await ingest_vcf(repo, "u1", f"{ROOT}/proband.vcf.gz", sex="M", file_key=f"{ROOT}/proband.vcf.gz")
    await ingest_vcf(repo, "u7", f"{ROOT}/father.vcf.gz", sex="M", file_key=f"{ROOT}/father.vcf.gz")
    await ingest_vcf(repo, "u8", f"{ROOT}/mother.vcf.gz", sex="F", file_key=f"{ROOT}/mother.vcf.gz")
    await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u1", "JD-50-F": "u7", "JD-50-M": "u8"})
    # no circle yet: parents' data may not be computed with
    res = await trio_backfill(repo, "u1", storage=_LocalStorage())
    assert res["updated"] == 0 and "father" in res["skipped"] and "mother" in res["skipped"]
    repo.t["care_circle"] += [{"operator": "u1", "subject": "u7", "access": 0}, {"operator": "u1", "subject": "u8", "access": 0}]
    res = await trio_backfill(repo, "u1", storage=_LocalStorage())
    l2 = [v for v in await repo.variants("u1") if v["gene_symbol"] == "L2HGDH"][0]
    assert l2["inheritance"] == "biparental" and l2["is_de_novo"] is False and res["updated"] >= 1
    # membership with access 0 lets the computation run, but the parent's own rows are still not readable
    assert not (await permit(repo, "u1", "u7", "variant")).allowed


async def test_ped_after_vcf_still_backfills():
    """ingest-plan §3.1: the PED may arrive after the parents' VCFs; the import must trigger the backfill."""
    from mirobody_rare.handlers import backfill_family
    repo = MemoryRepo()
    await ingest_vcf(repo, "u1", f"{ROOT}/proband.vcf.gz", sex="M", file_key=f"{ROOT}/proband.vcf.gz")
    await ingest_vcf(repo, "u7", f"{ROOT}/father.vcf.gz", sex="M", file_key=f"{ROOT}/father.vcf.gz")
    await ingest_vcf(repo, "u8", f"{ROOT}/mother.vcf.gz", sex="F", file_key=f"{ROOT}/mother.vcf.gz")
    repo.t["care_circle"] += [{"operator": "u1", "subject": "u7", "access": 0}, {"operator": "u1", "subject": "u8", "access": 0}]
    await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u1", "JD-50-F": "u7", "JD-50-M": "u8"})
    res = await backfill_family(repo, "u1", storage=_LocalStorage())
    assert res["u1"]["updated"] >= 1
    assert [v for v in await repo.variants("u1") if v["gene_symbol"] == "L2HGDH"][0]["inheritance"] == "biparental"


async def test_same_family_id_from_two_users_are_separate_pedigrees():
    """PED family ids are lab-local ("FAM1"); two accounts importing the same id must not share a row."""
    repo = MemoryRepo()
    p1 = await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u1"}, owner_id="u1")
    p2 = await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u9"}, owner_id="u9")
    assert p1 != p2
    assert (await repo.pedigree_of("u1"))["id"] == p1 and (await repo.pedigree_of("u9"))["id"] == p2
    # re-import by the same owner with a newly linked member updates the link instead of ignoring it
    await ingest_ped(repo, f"{ROOT}/JD-50.ped", user_ids={"JD-50-P": "u1", "JD-50-F": "u7"}, owner_id="u1")
    fam = await repo.pedigree_of("u1")
    assert fam["id"] == p1 and any(m["individual_id"] == "JD-50-F" and m["user_id"] == "u7" for m in fam["members"])
