"""Case-upload wizard and review page in a real browser (ingest-plan §7.3, checks 7a–7f).

Needs a RUNNING deployment: MIROBODY_RARE_WEB_BASE (Next.js), MIROBODY_RARE_E2E_BASE (API),
MIROBODY_RARE_PG_DSN (readback). `-m web`. The 20-case version of 7a/7b (7g) is
`tools/roundtrip_web.py`; here one trio case keeps the suite under ten minutes.
"""
import gzip
import os
import sys
import time
from pathlib import Path

import pytest

WEB = os.environ.get("MIROBODY_RARE_WEB_BASE", "")
API = os.environ.get("MIROBODY_RARE_E2E_BASE", "")
pytestmark = [pytest.mark.web, pytest.mark.skipif(not (WEB and API and os.environ.get("MIROBODY_RARE_PG_DSN")),
                                                  reason="MIROBODY_RARE_WEB_BASE / MIROBODY_RARE_E2E_BASE / PG DSN not set")]
REPO = Path(__file__).resolve().parents[3]
BATCH = os.environ.get("MIROBODY_RARE_RT_BATCH", "results/joint_dx/rare_coding-p3/20260922-095441")
TRIO = os.environ.get("MIROBODY_RARE_WEB_TRIO", "JD-52")
VCF = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz"


def _rt():
    sys.path.insert(0, str(REPO / "tools"))
    import roundtrip_check as rt
    import roundtrip_web as rw
    return rt, rw


def _run(coro):
    """pytest-playwright holds a loop in this thread; run our coroutines on a fresh one."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def _account(email: str) -> tuple[str, str]:
    import aiohttp
    rt, _ = _rt()
    async with aiohttp.ClientSession() as s:
        tok, uid = await rt.login(s, API, email)
    await rt.cleanup([uid])
    return tok, uid


async def _sql(q: str, *args):
    rt, _ = _rt()
    p = await rt.pool()
    return await p.fetch(q, *args)


def _sign_in(page, token: str, email: str, path: str) -> None:
    page.goto(f"{WEB}/login")
    page.evaluate("([t, e]) => { localStorage.setItem('access_token', t); localStorage.setItem('user_email', e);"
                  " localStorage.setItem('user_name', ''); }", [token, email])
    page.goto(f"{WEB}{path}")


def _wait_steps_terminal(page, timeout=15 * 60 * 1000):
    page.wait_for_function(
        "() => { const s = [...document.querySelectorAll('[data-testid^=\"step-\"][data-status]')];"
        " return s.length > 0 && s.every(x => x.dataset.status === 'completed' || x.dataset.status === 'failed'); }",
        timeout=timeout, polling=1000)


# --- 7a / 7b / 7c ------------------------------------------------------------------------
def test_7a_7b_7c_trio_case_through_the_wizard_matches_the_script_path():
    """Order, attribution to relatives' own accounts, ten-layer readback, no console errors."""
    rt, rw = _rt()

    async def go():
        import aiohttp
        from playwright.async_api import async_playwright
        batch = rt.HAENV / BATCH
        out = REPO / "reports" / "roundtrip" / "web-pytest"
        out.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw, aiohttp.ClientSession() as s:
            b = await pw.chromium.launch()
            run = await rw.run_case_web(b, s, batch, TRIO, out, WEB, API)
            await b.close()
        items, _ = await rt.compare_case(run)
        return run, items
    run, items = _run(go())
    steps = run["events"]["steps"]
    assert steps and all(st == "completed" for _, st in steps), steps
    assert [sl for sl, _ in steps][:3] == ["ped", "vcf_father", "vcf_mother"], "pedigree must go first, then the parents"
    real = [i for i in items if i["kind"] in ("transport", "version", "order", "coding")]
    assert real == [], real[:3]
    layers = {i["layer"] for i in items}
    assert {"文件", "样本", "变异", "遗传来源", "家系", "表型", "诊断", "权限"} <= layers
    ui = run["events"]["ui"]
    assert ui["console_errors"] == [], ui["console_errors"][:3]                      # 7c
    assert ui["ui_samples"] and all(x == "ready" for x in ui["ui_samples"])


def test_7c_chat_page_opens_no_dead_progress_socket(page):
    tok, uid = _run(_account("ui7c-proband@rare.test"))
    sockets, errors = [], []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    page.on("console", lambda m: errors.append(m.text[:160]) if m.type == "error" else None)
    _sign_in(page, tok, "ui7c-proband@rare.test", "/chat")
    page.wait_for_selector("input[type=file]", state="attached", timeout=60_000)
    page.wait_for_timeout(6000)
    assert not [u for u in sockets if "file-progress" in u], sockets
    assert not [e for e in errors if "file-progress" in e or "WebSocket" in e], errors


# --- 7d ---------------------------------------------------------------------------------
def test_7d_a_failed_sample_shows_and_retry_restores_it(page):
    tok, uid = _run(_account("ui7d-proband@rare.test"))
    _sign_in(page, tok, "ui7d-proband@rare.test", "/upload/case")
    page.wait_for_selector("[data-testid=proband-select] option", state="attached", timeout=60_000)
    page.set_input_files("[data-testid=slot-vcf_proband-input]", VCF)
    page.click("[data-testid=start-upload]")
    _wait_steps_terminal(page)
    page.wait_for_selector("[data-testid^=sample-][data-status=ready]", timeout=10 * 60 * 1000)
    [row] = _run(_sql("SELECT id FROM th_sequencing_sample WHERE user_id = $1", uid))
    sid = row["id"]
    n_before = _run(_sql("SELECT count(*) AS n FROM th_variant WHERE sample_id = $1", sid))[0]["n"]
    assert n_before > 0
    # the state an interrupted parse leaves behind: sample failed, its rows gone
    _run(_sql("DELETE FROM th_variant_annotation WHERE variant_id IN (SELECT id FROM th_variant WHERE sample_id = $1)", sid))
    _run(_sql("DELETE FROM th_variant WHERE sample_id = $1", sid))
    _run(_sql("UPDATE th_sequencing_sample SET status = 'failed' WHERE id = $1", sid))
    page.click("[data-testid=refresh-status]")
    page.wait_for_selector(f"[data-testid=sample-{sid}][data-status=failed]", timeout=30_000)
    assert page.locator(f"[data-testid=retry-{sid}]").is_visible()
    page.click(f"[data-testid=retry-{sid}]")
    page.wait_for_selector(f"[data-testid=sample-{sid}][data-status=ready]", timeout=10 * 60 * 1000)
    n_after = _run(_sql("SELECT count(*) AS n FROM th_variant WHERE sample_id = $1", sid))[0]["n"]
    assert n_after == n_before
    assert len(_run(_sql("SELECT id FROM th_sequencing_sample WHERE user_id = $1", uid))) == 1


# --- 7e ---------------------------------------------------------------------------------
def test_7e_review_page_closes_an_item_with_a_clinician_row(page):
    tok, uid = _run(_account("ui7e-proband@rare.test"))
    [r] = _run(_sql("INSERT INTO th_phenotype_review (user_id, kind, source_text, candidates) VALUES ($1, 'ambiguous', '便秘较明显',"
                    " ARRAY['HP:0002019','HP:0034782']) RETURNING id", uid))
    rid = r["id"]
    _sign_in(page, tok, "ui7e-proband@rare.test", "/upload/review")
    page.wait_for_selector(f"[data-testid=review-item-{rid}]", timeout=60_000)
    page.click(f"[data-testid=\"review-pick-{rid}-HP:0002019\"]")
    page.wait_for_selector(f"[data-testid=review-done-{rid}]", timeout=30_000)
    [row] = _run(_sql("SELECT resolved_hpo, resolved_at FROM th_phenotype_review WHERE id = $1", rid))
    assert row["resolved_hpo"] == "HP:0002019" and row["resolved_at"] is not None
    ph = _run(_sql("SELECT hpo_id FROM th_phenotype WHERE user_id = $1 AND source = 'clinician'", uid))
    assert [p["hpo_id"] for p in ph] == ["HP:0002019"]


# --- 7f ---------------------------------------------------------------------------------
def test_7f_refusals_say_why(page, tmp_path):
    """The server's own reason reaches the step: admission (a FASTQ) and proxy write access."""
    tok, uid = _run(_account("ui7f-proband@rare.test"))
    ftok, fuid = _run(_account("ui7f-father@rare.test"))
    rt, _ = _rt()
    _run(rt.make_circle(uid, {fuid: "F"}, member_access=2))
    fq = tmp_path / "reads.fastq.gz"
    with gzip.open(fq, "wt") as fh:
        fh.write("@r1\nACGT\n+\nIIII\n")
    _sign_in(page, tok, "ui7f-proband@rare.test", "/upload/case")
    page.wait_for_selector(f"[data-testid=slot-vcf_father-owner] option[value='{fuid}']", state="attached", timeout=60_000)
    page.set_input_files("[data-testid=slot-narrative-input]", str(fq))
    page.set_input_files("[data-testid=slot-vcf_father-input]", VCF)
    page.select_option("[data-testid=slot-vcf_father-owner]", fuid)
    # the father withdraws edit access after the page loaded: the server must refuse, and say so
    _run(_sql("UPDATE care_circle_members SET health_access = 1 WHERE user_id = $1", int(fuid)))
    page.click("[data-testid=start-upload]")
    _wait_steps_terminal(page, timeout=5 * 60 * 1000)
    msgs = {page.locator(f"[data-testid=step-{i}]").get_attribute("data-slot"):
            (page.locator(f"[data-testid=step-{i}-error]").inner_text() if page.locator(f"[data-testid=step-{i}-error]").count() else "")
            for i in range(page.locator("[data-testid^=step-][data-status]").count())}
    assert "write access" in msgs["vcf_father"], msgs
    assert "FASTQ" in msgs["narrative"], msgs
    assert not _run(_sql("SELECT id FROM th_files WHERE user_id = $1", fuid)), "a refused proxy upload stored nothing"
