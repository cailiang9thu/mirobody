"""A failed VCF parse must be resumable, a transient database error must not leave the
case incomplete, and a review item may only be closed by someone allowed to close it.

Found on 2026-09-23: in the 20-case roundtrip one sample went `failed` on an asyncpg pool
TimeoutError under concurrent ingest, and nothing retried it — its variants and the trio
inheritance were simply missing. Re-uploading the same file then created a SECOND sample row,
because `sample_by_hash` only matched `ready` samples."""
import asyncio

import pytest

from mirobody_rare.ingest import ingest_vcf, ingest_vcf_retrying
from mirobody_rare.repo import MemoryRepo
from mirobody_rare.tools import RareQueryService

VCF = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz"


class Flaky(MemoryRepo):
    """add_variants raises `n` times, then behaves."""
    def __init__(self, n: int, exc: type[BaseException] = TimeoutError):
        super().__init__()
        self.left, self.exc = n, exc

    async def add_variants(self, rows):
        if self.left > 0:
            self.left -= 1
            raise self.exc("simulated")
        return await super().add_variants(rows)


async def test_failed_sample_is_resumed_not_duplicated():
    repo = Flaky(1)
    with pytest.raises(TimeoutError):
        await ingest_vcf(repo, "u1", VCF, sex="F")
    [s] = repo.t["th_sequencing_sample"]
    assert s["status"] == "failed"
    res = await ingest_vcf(repo, "u1", VCF, sex="F")             # same bytes again, e.g. the user re-uploads
    assert res["status"] == "ready" and res["sample_id"] == s["id"]
    assert len(repo.t["th_sequencing_sample"]) == 1, "a re-upload of a failed file must resume it"
    assert {v["gene_symbol"] for v in await repo.variants("u1")} >= {"TP53"}


async def test_transient_errors_are_retried_with_the_same_sample():
    repo = Flaky(2)
    res = await ingest_vcf_retrying(repo, "u1", VCF, attempts=3, base_delay=0, sex="F")
    assert res["status"] == "ready"
    assert len(repo.t["th_sequencing_sample"]) == 1
    assert repo.t["th_sequencing_sample"][0]["status"] == "ready"


async def test_deterministic_errors_are_not_retried(tmp_path):
    import gzip
    p = tmp_path / "b37.vcf.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("##fileformat=VCFv4.2\n##reference=GRCh37\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
    calls = []

    class Counting(MemoryRepo):
        async def add_sample(self, row):
            calls.append(1)
            return await super().add_sample(row)
    with pytest.raises(ValueError):
        await ingest_vcf_retrying(Counting(), "u1", p, attempts=3, base_delay=0)
    assert calls == []                                          # refused at the header, once, no retry


async def test_retry_gives_up_and_leaves_the_sample_failed():
    repo = Flaky(10)
    with pytest.raises(TimeoutError):
        await ingest_vcf_retrying(repo, "u1", VCF, attempts=2, base_delay=0, sex="F")
    [s] = repo.t["th_sequencing_sample"]
    assert s["status"] == "failed"                              # visible to the UI, retryable later


async def test_denied_resolve_does_not_close_the_review():
    repo = MemoryRepo()
    rid = await repo.add_review({"user_id": "owner", "kind": "ambiguous", "source_text": "便秘较明显",
                                 "candidates": ["HP:0002019", "HP:0034782"]})
    out = await RareQueryService(repo=repo).resolve_phenotype_review({"user_id": "stranger"}, rid, hpo_id="HP:0002019")
    assert out["status"] == "error"
    [r] = [x for x in repo.t["th_phenotype_review"] if x["id"] == rid]
    assert r["resolved_at"] is None, "a refused caller must not have closed someone else's review item"
    assert not [p for p in repo.t["th_phenotype"] if p["user_id"] == "owner"]
    ok = await RareQueryService(repo=repo).resolve_phenotype_review({"user_id": "owner"}, rid, hpo_id="HP:0002019")
    assert ok["status"] == "ok"
    assert r["resolved_at"] is not None
