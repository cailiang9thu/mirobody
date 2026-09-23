"""roundtrip_bundled.py — the §3 roundtrip through the mirobody-rare service's OWN web client
(the bundled `frontend/`, served on the API port, 28085), the way its users upload: files attached
to a question on the Ask page. No wizard, no upload page (the UI stays as it ships).

Per case, at most three Ask turns, each a new conversation:
    Me          PED + proband VCF + narrative + DICOM series, with a question
    Query for   <cid>-F   the father's VCF, with a question   (the father granted EDIT)
    Query for   <cid>-M   the mother's VCF, with a question
This path is not the chunked WebSocket the other roundtrips use: the client POSTs each file to
`/files/upload`, then `/api/chat` files them into th_files and runs the handlers. Then the same
ten-layer `compare_case` as the script path (p3: 879 items, real diffs 0).

Needs `tools/patch_frontend_rare.py` applied: the stock client refuses VCF / PED / zip in the browser.

    .venv/bin/python3 tools/roundtrip_bundled.py --cases JD-50,JD-55 \\
        --batch results/joint_dx/rare_coding-p3/20260922-095441 --out reports/roundtrip/bundled-p3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roundtrip_check as rt                                            # noqa: E402
import roundtrip_web as rw                                              # noqa: E402

log = logging.getLogger("roundtrip_bundled")
BASE = "http://127.0.0.1:28085"


async def sign_in(page, base: str, email: str) -> None:
    await page.goto(f"{base}/login", wait_until="networkidle")
    await page.fill("input[placeholder=Email]", email)
    await page.fill("input[type=password]", rt.PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
    await page.wait_for_timeout(1500)
    for _ in range(10):                                   # the first-visit tour covers the page
        c = page.locator(".ant-tour-close")
        if await c.count() and await c.first.is_visible():
            await c.first.click(); await page.wait_for_timeout(300)
        else:
            break


async def ask_with_files(page, files: list[str], question: str, query_for: str | None, timeout_s: int = 240) -> dict:
    """One Ask turn in a new conversation → {refused, answered, seconds, error}."""
    t0 = time.time()
    await page.locator("button:has-text('Ask')").first.click()
    await page.wait_for_timeout(1200)
    nc = page.locator("button:has-text('New conversation')")
    if await nc.count():
        await nc.first.click(); await page.wait_for_timeout(1200)
    if query_for:
        await page.locator("button:has-text('Query for'):visible").first.click()
        opt = page.locator(f"button:has-text('{query_for}'):visible, li:has-text('{query_for}'):visible").first   # the menu items are buttons
        try:
            await opt.wait_for(state="visible", timeout=20_000)
        except Exception:                                              # noqa: BLE001
            listed = await page.eval_on_selector_all("li", "els=>els.map(e=>e.innerText.trim().split(String.fromCharCode(10)).join(' ')).filter(x=>x)")
            await page.screenshot(path=f"/tmp/claude-1000/-data-xfs-recovery-caill/23fdf233-3769-498d-9f04-3d011a93bc8a/scratchpad/qf-fail.png")
            btns = await page.eval_on_selector_all("button", "els=>els.filter(e=>e.offsetParent).map(e=>(e.innerText||e.getAttribute('aria-label')||'').trim().split(String.fromCharCode(10)).join(' ').slice(0,30))")
            raise RuntimeError(f"Query for has no {query_for!r}; it lists {listed}; url {page.url}; buttons {btns}")
        await opt.click()
        await page.wait_for_timeout(800)
    async with page.expect_file_chooser() as fc:
        await page.locator("button:has-text('Upload files')").first.click()
    await (await fc.value).set_files(files)
    await page.wait_for_timeout(1500)
    ok = page.locator("button:has-text('OK')")
    if await ok.count() and await ok.first.is_visible():             # the client-side gate
        msg = (await page.locator("[class*=modal]").last.inner_text())[-200:]
        await ok.first.click()
        return {"refused": msg, "answered": False, "seconds": round(time.time() - t0)}
    done0 = await page.locator("text=Answer Completed").count()
    finished = asyncio.get_running_loop().create_future()
    def _fin(req):
        if "/api/chat" in req.url and not finished.done():
            finished.set_result(req)
    page.on("requestfinished", _fin)
    page.on("requestfailed", _fin)
    await page.fill("textarea", question)
    await page.locator("button[aria-label=Send]").click()
    try:
        await asyncio.wait_for(finished, timeout_s)             # the turn is over when its stream closes
    except asyncio.TimeoutError:
        return {"refused": None, "answered": False, "seconds": round(time.time() - t0), "error": "no answer in time"}
    finally:
        page.remove_listener("requestfinished", _fin)
        page.remove_listener("requestfailed", _fin)
    await page.wait_for_timeout(1500)
    tail = (await page.inner_text("body"))[-300:].replace("\n", " ")
    answered = (await page.locator("text=Answer Completed").count()) > done0
    out = {"refused": None, "answered": answered, "seconds": round(time.time() - t0)}
    if "No permission" in tail:
        out["error"] = tail[-200:]
    elif not answered:
        out["asked_back"] = tail[-200:]                         # the agent paused on ask_user: files are in, reply is a question
    return out


async def run_case_bundled(browser, s, batch: Path, cid: str, out_dir: Path, base: str = BASE) -> dict:
    case, sp = rt.load_case(batch, cid)
    att = case["case"]["adjudication"]["rare"].get("attachments") or {}
    gen = att.get("genome") or {}
    roles = list((gen.get("files") or {}).keys())
    accts = await rw.prepare_accounts(s, base, cid, roles, prefix="bui")
    uids = {role: uid for role, (_, uid) in accts.items()}
    nar = att.get("narrative") or {}
    if nar.get("path") and Path(nar["path"]).is_file():
        md, src = Path(nar["path"]), "attachment"
    else:
        md, src = out_dir / f"{cid}.md", "rendered_from_ledger"
        md.write_text(rt.narrative_md(cid, sp), encoding="utf-8")
    mine = ([gen["ped"]["path"]] if gen.get("ped") else []) + ([gen["files"]["proband"]["path"]] if "proband" in roles else []) \
        + [str(md)] + [x["path"] for x in (att.get("imaging") or {}).get("series") or []]
    turns = [("proband", mine, None, "我上传了我的病例资料(家系、基因检测、病历、影像),请帮我看看有哪些致病变异和表型。")]
    for role, nick in (("father", f"{cid}-F"), ("mother", f"{cid}-M")):
        if role in roles:
            turns.append((role, [gen["files"][role]["path"]], nick, f"这是{'父亲' if role == 'father' else '母亲'}的基因检测文件,请一起纳入分析。"))
    ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
    page = await ctx.new_page()
    console: list[str] = []
    bad: list[str] = []
    page.on("console", lambda m: console.append(m.text[:200]) if m.type == "error" else None)
    page.on("response", lambda r: bad.append(f"{r.status} {r.url.split('?')[0][-60:]}") if r.status >= 400 else None)
    t0 = time.time()
    results = []
    try:
        await sign_in(page, base, f"bui-{cid.lower()}-proband@rare.test")
        for role, files, qf, q in turns:
            res = await ask_with_files(page, files, q, qf)
            results.append((role, res))
            if res.get("refused") or res.get("error"):
                log.warning("[%s] %s turn: %s", cid, role, res)
    finally:
        await ctx.close()
    from mirobody_rare.repo import PgRepo
    repo = PgRepo()
    for role in roles:
        await rt.wait_for(lambda r=role: rt._ready(repo, uids[r]))
    if sp.get("evidence_ledger"):
        await rt.wait_for(lambda: repo.phenotypes(uids["proband"]), timeout=180)
    await asyncio.sleep(8)                                             # trio backfill after the last parent VCF
    events = {"case": cid, "uids": uids, "narrative_source": src,
              "steps": [(role, "refused" if r.get("refused") else ("failed" if r.get("error") else "completed")) for role, r in results],
              "ui": {"console_errors": console, "http_errors": bad, "turns": results, "seconds": round(time.time() - t0)}}
    log.info("[%s] via Ask page in %ds: %s | console errors %d | http>=400 %d", cid, time.time() - t0,
             [(r, x.get("answered"), x.get("seconds")) for r, x in results], len(console), len(bad))
    return {"case": case, "sp": sp, "events": events, "uids": uids, "narrative": md}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--base", default=BASE)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    batch = (rt.HAENV / a.batch) if not Path(a.batch).is_absolute() else Path(a.batch)
    out = Path(a.out or f"reports/roundtrip/bundled-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    import aiohttp
    from playwright.async_api import async_playwright
    runs = []
    async with async_playwright() as pw, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
        browser = await pw.chromium.launch()
        for cid in a.cases.split(","):
            runs.append(await run_case_bundled(browser, s, batch, cid, out, a.base))
        await browser.close()
    items = []
    for r in runs:
        it, _ = await rt.compare_case(r)
        items += it
        log.info("[%s] compared: %d items, real diffs %d", r["case"]["case_id"], len(it),
                 sum(1 for i in it if i["kind"] in ("transport", "version", "order", "coding")))
    rep = await rt.build_report(batch, runs, items)
    rep["env"]["path"] = "bundled_client_ask_page"
    rep["ui"] = {r["case"]["case_id"]: r["events"]["ui"] for r in runs}
    from mirobody_rare.roundtrip.report import render_html, render_samples_md
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "samples.md").write_text(render_samples_md(rep), encoding="utf-8")
    (out / "summary.html").write_text(render_html(rep), encoding="utf-8")
    turns = [t for v in rep["ui"].values() for t in v["turns"]]
    print(json.dumps({"out": str(out), "counts": rep["counts"], "layers": rep["layers"],
                      "turns": len(turns), "answered": sum(1 for _, t in turns if t.get("answered")),
                      "asked_back": sum(1 for _, t in turns if t.get("asked_back")), "failed": sum(1 for _, t in turns if t.get("error")),
                      "refused": sum(1 for _, t in turns if t.get("refused")),
                      "console_errors": sum(len(v["console_errors"]) for v in rep["ui"].values()),
                      "http_errors": sum(len(v["http_errors"]) for v in rep["ui"].values())}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
