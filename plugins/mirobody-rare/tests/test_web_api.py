"""The `/api/rare/*` surface the upload wizard and review page call (ingest-plan §7)."""
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mirobody.server.auth import verify_token
from mirobody_rare import web
from mirobody_rare.repo import MemoryRepo

VCF = pathlib.Path("/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz")


class Storage:
    def __init__(self, blobs):
        self.blobs = blobs

    async def get(self, key):
        return (self.blobs[key], None) if key in self.blobs else (None, "missing")


@pytest.fixture
def world():
    repo = MemoryRepo()
    # me=10, father=11 grants me EDIT (2), aunt=12 grants VIEW (1), stranger=99 shares nothing
    for subject, access, nick in (("11", 2, "JD-1-F"), ("12", 1, "姑姑")):
        repo.t["care_circle"].append({"operator": "10", "subject": subject, "access": access, "nickname": nick, "name": nick})
    storage = Storage({"k-vcf": VCF.read_bytes()})
    app = FastAPI()
    app.include_router(web.router)
    app.dependency_overrides[web.get_repo] = lambda: repo
    app.dependency_overrides[web.get_storage] = lambda: storage
    who = {"id": "10"}
    app.dependency_overrides[verify_token] = lambda: who["id"]
    return TestClient(app), repo, who


def test_routers_are_exported_for_the_entry_point():
    assert web.router in web.ROUTERS


def test_case_context_says_who_i_may_upload_for(world):
    c, _, _ = world
    d = c.get("/api/rare/case-context").json()
    assert d["code"] == 0 and d["data"]["self_id"] == "10"
    m = {x["user_id"]: x for x in d["data"]["members"]}
    assert m["11"]["can_upload"] is True and m["11"]["access"] == 2
    assert m["12"]["can_upload"] is False and m["12"]["can_view"] is True


def test_status_reports_samples_counts_and_diagnosis(world):
    import asyncio
    c, repo, _ = world
    sid = asyncio.run(repo.add_sample({"user_id": "10", "status": "failed", "sample_label": "HG005", "file_key": "k-vcf"}))
    asyncio.run(repo.add_phenotypes([{"user_id": "10", "hpo_id": "HP:0001250", "hpo_label": "癫痫发作", "negated": False,
                                      "subject": "proband", "source": "nlp", "source_text": "x", "confidence": 1.0}]))
    asyncio.run(repo.add_review({"user_id": "10", "kind": "ambiguous", "source_text": "y", "candidates": ["HP:0002019"]}))
    d = c.get("/api/rare/status").json()["data"]
    assert [s["id"] for s in d["samples"]] == [sid] and d["samples"][0]["status"] == "failed"
    assert d["counts"]["phenotypes"] == 1 and d["counts"]["reviews_open"] == 1 and d["counts"]["samples_failed"] == 1


def test_status_of_a_relative_needs_view_access(world):
    c, _, _ = world
    assert c.get("/api/rare/status", params={"subject_id": "12"}).json()["code"] == 0
    assert c.get("/api/rare/status", params={"subject_id": "99"}).json()["code"] == 403


def test_retry_resumes_a_failed_sample_from_storage(world):
    import asyncio
    c, repo, _ = world
    sid = asyncio.run(repo.add_sample({"user_id": "10", "status": "failed", "sample_label": "HG005", "file_key": "k-vcf"}))
    r = c.post(f"/api/rare/samples/{sid}/retry", params={"wait": "true"}).json()
    assert r["code"] == 0 and r["data"]["status"] == "ready"
    assert len(repo.t["th_sequencing_sample"]) == 1 and repo.t["th_sequencing_sample"][0]["status"] == "ready"
    assert {v["gene_symbol"] for v in asyncio.run(repo.variants("10"))} >= {"TP53"}


def test_retry_refuses_ready_samples_and_strangers(world):
    import asyncio
    c, repo, who = world
    ready = asyncio.run(repo.add_sample({"user_id": "10", "status": "ready", "file_key": "k-vcf"}))
    assert c.post(f"/api/rare/samples/{ready}/retry").json()["code"] == 409
    theirs = asyncio.run(repo.add_sample({"user_id": "12", "status": "failed", "file_key": "k-vcf"}))
    assert c.post(f"/api/rare/samples/{theirs}/retry").json()["code"] == 403      # VIEW is not enough to write
    assert c.post("/api/rare/samples/999999/retry").json()["code"] == 404


def test_review_queue_and_resolve(world):
    import asyncio
    c, repo, who = world
    rid = asyncio.run(repo.add_review({"user_id": "10", "kind": "ambiguous", "source_text": "便秘较明显",
                                       "candidates": ["HP:0002019", "HP:0034782"]}))
    q = c.get("/api/rare/reviews").json()["data"]["items"]
    assert [x["id"] for x in q] == [rid]
    assert [x["hpo_id"] for x in q[0]["candidates"]] == ["HP:0002019", "HP:0034782"]
    assert all(x["label"] for x in q[0]["candidates"])                             # labels resolved for the UI
    who["id"] = "99"
    assert c.post(f"/api/rare/reviews/{rid}/resolve", json={"hpo_id": "HP:0002019"}).json()["code"] == 403
    who["id"] = "10"
    r = c.post(f"/api/rare/reviews/{rid}/resolve", json={"hpo_id": "HP:0002019"}).json()
    assert r["code"] == 0 and r["data"]["resolved"] == "HP:0002019"
    assert c.get("/api/rare/reviews").json()["data"]["items"] == []
    assert any(p["source"] == "clinician" and p["hpo_id"] == "HP:0002019" for p in repo.t["th_phenotype"])
