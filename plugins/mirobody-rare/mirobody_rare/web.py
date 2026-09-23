"""`/api/rare/*`: the REST surface the case-upload wizard and the review page call.

Mounted by the host through the `mirobody.routers` entry point (`ROUTERS`). The upload itself
stays on the host's WebSocket (`/ws/upload-health-report`); these endpoints cover what that
socket cannot tell a user (ingest-plan §7):

    GET  /api/rare/case-context              who I may upload for (care-circle access)
    GET  /api/rare/status?subject_id=        samples + parse status, counts, diagnosis
    POST /api/rare/samples/{id}/retry        re-parse a FAILED sample from its stored bytes
    GET  /api/rare/reviews?subject_id=       the open review queue, candidates with labels
    POST /api/rare/reviews/{id}/resolve      close one item (writes a clinician row)

Access follows the care-circle rules the tools use: reading needs the subject's
`health_access` >= 1 toward the caller, anything that writes needs 2 (the same bar the host
applies to a proxy upload). Answers use the host envelope: HTTP 200, `code` 0 or HTTP-like.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from mirobody.server.auth import verify_token
from mirobody.server.envelope import err, ok

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rare", tags=["rare"])
ROUTERS = (router,)

ACCESS_VIEW, ACCESS_EDIT = 1, 2


def get_repo():
    from .repo import PgRepo
    return PgRepo()


def get_storage():
    from mirobody.utils.config.storage.factory import get_storage_client
    return get_storage_client()


async def _access(repo, caller: str, subject: str) -> int | None:
    """The subject's grant toward the caller; the caller has full access to themself."""
    if str(subject) == str(caller):
        return ACCESS_EDIT
    return await repo.circle_access(str(caller), str(subject))


def _plain(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _pick(row: dict, keys: tuple[str, ...]) -> dict:
    return {k: _plain(row.get(k)) for k in keys}


@router.get("/case-context")
async def case_context(caller: str = Depends(verify_token), repo=Depends(get_repo)):
    """Who the wizard may attribute a relative's file to. A member is listed with the grant
    they gave me: `can_upload` needs edit (2) — the host refuses a proxy upload below that."""
    seen: dict[str, dict] = {}
    for m in await repo.circle_members(str(caller)):
        uid = str(m.get("user_id") or "")
        if not uid or uid == str(caller) or uid in seen:
            continue
        acc = await repo.circle_access(str(caller), uid)
        seen[uid] = {"user_id": uid, "nickname": m.get("nickname"), "name": m.get("name"), "email": m.get("email"),
                     "access": acc, "can_view": (acc or 0) >= ACCESS_VIEW, "can_upload": (acc or 0) >= ACCESS_EDIT}
    return ok({"self_id": str(caller), "members": list(seen.values())})


@router.get("/status")
async def status(subject_id: str = Query(""), caller: str = Depends(verify_token), repo=Depends(get_repo)):
    subject = subject_id or str(caller)
    if (await _access(repo, caller, subject) or 0) < ACCESS_VIEW:
        return err(403, "You do not have access to this person's record.")
    samples = await repo.samples_of(subject)
    phen = await repo.phenotypes(subject)
    reviews = await repo.open_reviews(subject)
    dcs = await repo.disease_codes(subject)
    sig = await repo.signals(subject)
    variants = await repo.variants(subject, limit=100_000)
    by_status: dict[str, int] = {}
    for s in samples:
        by_status[str(s.get("status"))] = by_status.get(str(s.get("status")), 0) + 1
    return ok({
        "subject_id": subject,
        "samples": [_pick(s, ("id", "sample_label", "status", "variant_count", "file_key", "assay", "reference")) for s in samples],
        "counts": {"phenotypes": sum(1 for p in phen if not p.get("negated")),
                   "phenotypes_negated": sum(1 for p in phen if p.get("negated")),
                   "reviews_open": len(reviews), "variants": len(variants), "signals": len(sig),
                   "samples_ready": by_status.get("ready", 0), "samples_failed": by_status.get("failed", 0),
                   "samples_parsing": by_status.get("parsing", 0) + by_status.get("pending", 0)},
        "diagnosis": [_pick(d, ("code", "label", "source", "status", "confidence")) for d in dcs],
    })


async def _reparse(repo, storage, sample: dict) -> dict:
    """Fetch the stored bytes, resume the sample's shards, then trio backfill + diagnosis refresh."""
    from .dx_refresh import refresh_diagnosis
    from .handlers import backfill_family
    from .ingest import ingest_vcf_retrying
    data, e = await storage.get(sample["file_key"])
    if data is None:
        raise FileNotFoundError(e or sample["file_key"])
    # By content, not by key: a storage key need not carry the file's extension, and the VCF
    # reader chooses gzip vs text from the suffix it is handed.
    suffix = ".vcf.gz" if data[:2] == b"\x1f\x8b" else ".vcf"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="rare-retry-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        res = await ingest_vcf_retrying(repo, str(sample["user_id"]), tmp, sample_id=int(sample["id"]),
                                        file_key=sample["file_key"])
        await backfill_family(repo, str(sample["user_id"]))
        await refresh_diagnosis(repo, str(sample["user_id"]))
        return res
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@router.post("/samples/{sample_id}/retry")
async def retry_sample(sample_id: int, wait: bool = Query(False), caller: str = Depends(verify_token),
                       repo=Depends(get_repo), storage=Depends(get_storage)):
    s = await repo.sample(sample_id)
    if not s:
        return err(404, "No such sample.")
    if (await _access(repo, caller, str(s["user_id"])) or 0) < ACCESS_EDIT:
        return err(403, "Retrying needs edit access to this person's record.")
    if s.get("status") != "failed":
        return err(409, f"Only a failed sample can be retried (this one is {s.get('status')}).")
    if not s.get("file_key"):
        return err(409, "This sample has no stored file to re-parse; upload the file again.")
    data, e = await storage.get(s["file_key"])
    if data is None:
        return err(410, "The original file is no longer in storage; upload it again.")
    if wait:
        try:
            await _reparse(repo, storage, s)
        except Exception as ex:                                       # noqa: BLE001
            log.warning("[rare] retry of sample %s failed: %s", sample_id, type(ex).__name__)
        now = await repo.sample(sample_id) or s
        return ok({"sample_id": sample_id, "status": now.get("status")})
    from mirobody.utils.tasks import spawn
    spawn(_reparse(repo, storage, s), name=f"rare_retry:{sample_id}")
    return ok({"sample_id": sample_id, "status": "parsing"})


@router.get("/reviews")
async def reviews(subject_id: str = Query(""), caller: str = Depends(verify_token), repo=Depends(get_repo)):
    subject = subject_id or str(caller)
    if (await _access(repo, caller, subject) or 0) < ACCESS_VIEW:
        return err(403, "You do not have access to this person's record.")
    from .hpo import get_adapter
    hpo = get_adapter()
    items = []
    for r in await repo.open_reviews(subject):
        cands = [c for c in (r.get("candidates") or []) if c]
        items.append({**_pick(r, ("id", "kind", "source_text", "section", "subject", "negated")),
                      "candidates": [{"hpo_id": c, "label": hpo.label(c)} for c in cands]})
    return ok({"subject_id": subject, "items": items})


class ResolveBody(BaseModel):
    hpo_id: str = ""
    reject: bool = False


@router.post("/reviews/{review_id}/resolve")
async def resolve_review(review_id: int, body: ResolveBody, caller: str = Depends(verify_token), repo=Depends(get_repo)):
    if body.hpo_id and not body.reject:
        from .hpo import get_adapter
        if not get_adapter().label(body.hpo_id):
            return err(400, f"{body.hpo_id} is not an HPO term this server knows.")
    from .tools import RareQueryService
    out = await RareQueryService(repo=repo).resolve_phenotype_review({"user_id": str(caller)}, review_id,
                                                                    hpo_id=body.hpo_id, reject=body.reject)
    if out.get("status") == "ok":
        return ok((out.get("data") or [{}])[0])
    kind = out.get("error_kind")
    code = {"denied": 403, "no_data": 404, "invalid_arguments": 409}.get(kind, 500)
    return err(code, "; ".join(out.get("assumptions") or []) or "Could not resolve this review item.")
