"""Web-level roundtrip (ingest-plan §3.9): the browser picks the files, the page uploads them over
the WebSocket hook, the chat reads them back through the plugin's tools.

Needs a RUNNING deployment: MIROBODY_RARE_WEB_BASE (Next.js, e.g. http://127.0.0.1:28086) and
MIROBODY_RARE_E2E_BASE (API, e.g. http://127.0.0.1:28085) plus the plugin's PG DSN for the readback.
Run:  .venv/bin/python3 -m pytest -m web plugins/mirobody-rare/tests/test_web_playwright.py \
        --screenshot only-on-failure --output reports/roundtrip/web
"""
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import pytest

WEB = os.environ.get("MIROBODY_RARE_WEB_BASE", "")
API = os.environ.get("MIROBODY_RARE_E2E_BASE", "")
pytestmark = [pytest.mark.web, pytest.mark.skipif(not (WEB and API and os.environ.get("MIROBODY_RARE_PG_DSN")),
                                                  reason="MIROBODY_RARE_WEB_BASE / MIROBODY_RARE_E2E_BASE / PG DSN not set")]
REPO = Path(__file__).resolve().parents[3]
BATCH = os.environ.get("MIROBODY_RARE_RT_BATCH", "results/joint_dx/rare_coding-p1/20260921-112815")
CASE = os.environ.get("MIROBODY_RARE_WEB_CASE", "JD-55")          # single-sample: PED + VCF + narrative + DICOM
TRIO = os.environ.get("MIROBODY_RARE_WEB_TRIO", "JD-52")          # for the father-denied check (accounts from the API roundtrip)


def _rt():
    sys.path.insert(0, str(REPO / "tools"))
    import roundtrip_check as rt
    return rt


def _run(coro):
    """pytest-asyncio keeps a loop running in this thread; run our helpers on a fresh loop elsewhere."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _sign_in(page, token: str, email: str, path: str = "/chat") -> None:
    """The web keeps the JWT in localStorage (common/consts ACCESS_TOKEN_KEY); seed it and open the page.
    Uploads go through /upload: it is the page wired to the WebSocket protocol the API serves
    (`/ws/upload-health-report`); the chat input posts to a REST data endpoint this backend has no route for (405)."""
    page.goto(f"{WEB}/login")
    page.evaluate("([t, e]) => { localStorage.setItem('access_token', t); localStorage.setItem('user_email', e); localStorage.setItem('user_name', ''); }",
                  [token, email])
    page.goto(f"{WEB}{path}")
    page.wait_for_selector("input[type=file]", state="attached", timeout=60_000)


def _choose_self(page, email: str) -> None:
    """/upload refuses to start ("请选择受益人") until a share member is picked; the API's
    beneficiary list always contains the caller (is_current_user), listed by name."""
    page.get_by_role("button", name=re.compile("share member|受益", re.I)).first.click()
    page.get_by_text(email.split("@")[0], exact=False).last.click()
    page.wait_for_timeout(500)


def _ask(page, question: str) -> None:
    box = page.get_by_placeholder(re.compile("question|问题|输入", re.I)).last
    box.fill(question)
    box.press("Enter")


@pytest.fixture(scope="module")
def case():
    rt = _rt()
    batch = (rt.HAENV / BATCH) if not Path(BATCH).is_absolute() else Path(BATCH)
    c, sp = rt.load_case(batch, CASE)
    rare = c["case"]["adjudication"]["rare"]
    gen = (rare.get("attachments") or {}).get("genome") or {}
    md = REPO / "reports" / "roundtrip" / "web" / f"{CASE}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(rt.narrative_md(CASE, sp), encoding="utf-8")
    return {"rt": rt, "rare": rare, "ped": Path(gen["ped"]["path"]), "vcf": Path(gen["files"]["proband"]["path"]), "md": md,
            "gene": rare["gene"], "hpo": rare["hpo_gold"]}


@pytest.fixture(scope="module")
def proband(case):
    """API login (password branch) + per-account cleanup, exactly like the API roundtrip."""
    import aiohttp
    rt = case["rt"]

    async def go():
        async with aiohttp.ClientSession() as s:
            tok, uid = await rt.login(s, API, f"haenv-{CASE.lower()}-proband@rare.test")
        await rt.cleanup([uid])
        return tok, uid
    tok, uid = _run(go())
    return {"token": tok, "uid": uid, "email": f"haenv-{CASE.lower()}-proband@rare.test"}


def test_3d1_accept_lists_genomic_extensions(page, proband):
    """Before the fix the OS picker hid .vcf/.gz/.ped/.zip (accept whitelist) and the JS gate rejected them."""
    for path in ("/upload", "/chat"):
        _sign_in(page, proband["token"], proband["email"], path)
        accept = page.locator("input[type=file]").first.get_attribute("accept") or ""
        for ext in (".vcf", ".gz", ".ped", ".zip", ".md"):
            assert ext in accept, f"{path}: {ext} missing from accept={accept}"


def test_3d2_page_upload_lands_files_with_matching_hashes(page, proband, case):
    rt = case["rt"]
    _sign_in(page, proband["token"], proband["email"], "/upload")
    _choose_self(page, proband["email"])
    files = [case["ped"], case["vcf"], case["md"]]
    page.locator("input[type=file]").first.set_input_files([str(f) for f in files])
    page.wait_for_timeout(1500)
    err = page.get_by_text(re.compile("unsupported|not supported|too large|不支持|过大", re.I))
    assert err.count() == 0, "the page refused a genomic file: " + err.first.inner_text()
    want = {hashlib.sha256(f.read_bytes()).hexdigest() for f in files}

    async def ready():
        p = await rt.pool()
        for _ in range(120):                                   # ≤ 10 min: VCF parse runs in the server process
            rows = await p.fetch("SELECT content_hash FROM th_files WHERE user_id = $1 AND is_del = false", proband["uid"])
            got = {r["content_hash"] for r in rows}
            samples = await p.fetch("SELECT status FROM th_sequencing_sample WHERE user_id = $1", proband["uid"])
            ph = await p.fetchval("SELECT count(*) FROM th_phenotype WHERE user_id = $1", proband["uid"])
            if want <= got and samples and all(s["status"] == "ready" for s in samples) and ph > 0:
                return got, [s["status"] for s in samples], ph
            time.sleep(5)
        return got, [s["status"] for s in samples], ph
    got, statuses, ph = _run(ready())
    assert want <= got, f"th_files hashes {got} lack {want - got}"
    assert statuses and all(s == "ready" for s in statuses), statuses
    assert ph > 0


def test_3d3_chat_reads_back_through_tools(page, proband, case):
    _sign_in(page, proband["token"], proband["email"])
    _ask(page, "查询我的基因变异,列出基因名")
    page.get_by_text(re.compile("Query Variant", re.I)).first.wait_for(timeout=180_000)     # ToolCallCard title
    page.get_by_text(case["gene"]).first.wait_for(timeout=180_000)
    _ask(page, "查询我的表型记录")
    page.get_by_text(re.compile("Query Phenotype", re.I)).first.wait_for(timeout=180_000)
    labels = [h["label"] for h in case["hpo"] if h.get("polarity") == "present"]
    page.get_by_text(re.compile("|".join(map(re.escape, labels)))).first.wait_for(timeout=180_000)
    _ask(page, "查询我的家族史")
    page.get_by_text(re.compile("Query Family History", re.I)).first.wait_for(timeout=180_000)


def test_3d4_query_for_selector_follows_health_access(page, case):
    """The page's "Query For" selector is fed by /api/beneficiary-users, i.e. `accepted_membership`
    (health_access ≥ 1). In the trio fixture the father sits in the proband's circle with
    health_access=0 and the proband (owner) shares at 2: so the proband must NOT be offered the
    father, while the father IS offered the proband. The consent gate behind the tool applies the
    same rule (API roundtrip layer 权限), so the web cannot reach a subject the gate would refuse."""
    import aiohttp
    rt = case["rt"]

    async def ids():
        async with aiohttp.ClientSession() as s:
            ftok, fuid = await rt.login(s, API, f"haenv-{TRIO.lower()}-father@rare.test")
            ptok, puid = await rt.login(s, API, f"haenv-{TRIO.lower()}-proband@rare.test")
        return ftok, fuid, ptok, puid
    ftok, fuid, ptok, puid = _run(ids())
    # proband: the father (access=0) is not selectable
    _sign_in(page, ptok, f"haenv-{TRIO.lower()}-proband@rare.test")
    sel = page.locator("select").first
    sel.locator(f"option[value='{puid}']").wait_for(state="attached", timeout=60_000)
    assert sel.locator(f"option[value='{fuid}']").count() == 0, "father with health_access=0 offered to the proband"
    # father: the proband (owner, access=2) is selectable
    _sign_in(page, ftok, f"haenv-{TRIO.lower()}-father@rare.test")
    sel = page.locator("select").first
    sel.locator(f"option[value='{fuid}']").wait_for(state="attached", timeout=60_000)
    assert sel.locator(f"option[value='{puid}']").count() == 1
