"""D6 consent write path (plan §7): research_use is never granted by circle membership alone —
it needs a live consent row, and `granted=false` recorded later revokes it."""
import pytest

from mirobody_rare.consent.gate import permit
from mirobody_rare.repo import MemoryRepo
from mirobody_rare.tools import RareQueryService


def _repo():
    r = MemoryRepo()
    r.t["care_circle"].append({"operator": "doc", "subject": "pat", "access": 2})
    return r


@pytest.mark.asyncio
async def test_research_use_needs_a_consent_row_even_inside_the_circle():
    r = _repo()
    d = await permit(r, "doc", "pat", "variant", purpose="research_use")
    assert not d.allowed and "consent" in d.reason
    svc = RareQueryService(repo=r)
    out = await svc.record_consent({"user_id": "pat"}, scope="research_use", layer="variant")
    assert out["status"] == "ok" and out["data"][0]["scope"] == "research_use"
    d = await permit(r, "doc", "pat", "variant", purpose="research_use")
    assert d.allowed and d.reason.startswith("consent#")
    # a layer-scoped consent does not open another layer
    assert not (await permit(r, "doc", "pat", "phenotype", purpose="research_use")).allowed


@pytest.mark.asyncio
async def test_later_granted_false_revokes_the_consent():
    r = _repo()
    svc = RareQueryService(repo=r)
    await svc.record_consent({"user_id": "pat"}, scope="research_use")
    assert (await permit(r, "doc", "pat", "variant", purpose="research_use")).allowed
    await svc.record_consent({"user_id": "pat"}, scope="research_use", granted=False)
    d = await permit(r, "doc", "pat", "variant", purpose="research_use")
    assert not d.allowed, "the tool docstring promises revoke-by-granted=false"
