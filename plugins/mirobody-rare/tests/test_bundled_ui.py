"""The mirobody-rare service's OWN web client (bundled `frontend/`, served on the API port) — the UI
users upload through: files attached to a question on the Ask page. `-m web`; needs a running API
(MIROBODY_RARE_E2E_BASE, which serves the client too) and MIROBODY_RARE_PG_DSN for readback.

The 20-case ten-layer roundtrip through this page is `tools/roundtrip_bundled.py`; these are the
checks that must hold for any single upload."""
import os
import sys
from pathlib import Path

import pytest

API = os.environ.get("MIROBODY_RARE_E2E_BASE", "")
pytestmark = [pytest.mark.web, pytest.mark.skipif(not (API and os.environ.get("MIROBODY_RARE_PG_DSN")),
                                                  reason="MIROBODY_RARE_E2E_BASE / PG DSN not set")]
REPO = Path(__file__).resolve().parents[3]
R = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55"


def _tools():
    sys.path.insert(0, str(REPO / "tools"))
    import roundtrip_bundled as rb
    import roundtrip_check as rt
    return rt, rb


def _run(coro):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def _accounts(*emails):
    import aiohttp
    rt, _ = _tools()
    out = []
    async with aiohttp.ClientSession() as s:
        for e in emails:
            out.append(await rt.login(s, API, e))
    await rt.cleanup([u for _, u in out])
    return out


async def _sql(q, *args):
    rt, _ = _tools()
    return await (await rt.pool()).fetch(q, *args)


async def _composer_accepts(email, files):
    """→ {file name: None if the page took it, else the refusal text}."""
    from playwright.async_api import async_playwright
    _, rb = _tools()
    out = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        page = await b.new_page(viewport={"width": 1400, "height": 900})
        await rb.sign_in(page, API, email)
        await page.locator("button:has-text('Ask')").first.click()
        await page.wait_for_timeout(1500)
        for f in files:
            async with page.expect_file_chooser() as fc:
                await page.locator("button:has-text('Upload files')").first.click()
            await (await fc.value).set_files([f])
            await page.wait_for_timeout(1200)
            ok = page.locator("button:has-text('OK')")
            if await ok.count() and await ok.first.is_visible():
                out[Path(f).name] = (await page.locator("[class*=modal]").last.inner_text())[-160:]
                await ok.first.click()
            else:
                out[Path(f).name] = None
        await b.close()
    return out


def test_b1_rare_files_pass_the_client_gate_and_other_types_still_do_not(tmp_path):
    """`tools/patch_frontend_rare.py`: before it, every one of these was refused in the browser."""
    [(_, _uid)] = _run(_accounts("bui-b1@rare.test"))
    other = tmp_path / "notes.bin"
    other.write_bytes(b"\x00\x01binary")
    got = _run(_composer_accepts("bui-b1@rare.test", [f"{R}/proband.vcf.gz", f"{R}/JD-55.ped", f"{R}/JD-55.md",
                                                      "/data/xfs_recovery/data/rare/03_dicom/UPENN-GBM/UPENN-GBM-00005/"
                                                      "1.3.6.1.4.1.14519.5.2.1.61459741640552642122253987509717553365.zip",
                                                      str(other)]))
    assert got["proband.vcf.gz"] is None and got["JD-55.ped"] is None and got["JD-55.md"] is None
    assert got["1.3.6.1.4.1.14519.5.2.1.61459741640552642122253987509717553365.zip"] is None
    assert got["notes.bin"] and "allowed file types" in got["notes.bin"]       # the stock rule still holds for the rest


def test_b2_a_view_only_member_cannot_file_into_a_relatives_record():
    """Chatting about a relative needs view access; attaching files INTO their record needs edit."""
    rt, rb = _tools()
    (_, pu), (_, fu) = _run(_accounts("bui-b2-proband@rare.test", "bui-b2-father@rare.test"))
    _run(rt.make_circle(pu, {fu: "B2-F"}, member_access=1))

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            page = await b.new_page(viewport={"width": 1400, "height": 900})
            await rb.sign_in(page, API, "bui-b2-proband@rare.test")
            res = await rb.ask_with_files(page, [f"{R}/proband.vcf.gz"], "这是父亲的基因检测文件", "B2-F")
            await b.close()
            return res
    res = _run(go())
    assert "No permission to upload files" in (res.get("error") or ""), res
    assert not _run(_sql("SELECT id FROM th_files WHERE query_user_id = $1 OR user_id = $1", fu))
    assert not _run(_sql("SELECT id FROM th_sequencing_sample WHERE user_id = $1", fu))


def test_b3_the_page_loads_only_the_patched_build(page):
    """The patched chunks carry new names (assets are `immutable`): nothing 404s, nothing stale."""
    [(_, _uid)] = _run(_accounts("bui-b3@rare.test"))
    bad, js = [], []
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 and "/assets/" in r.url else None)
    page.on("response", lambda r: js.append(r.url.rsplit("/", 1)[-1]) if r.url.endswith(".js") else None)
    page.goto(f"{API}/login", wait_until="networkidle")
    assert not bad, bad
    assert any(n.endswith("-rare.js") and n.startswith("index-") for n in js), js
