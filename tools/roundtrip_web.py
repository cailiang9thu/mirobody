"""roundtrip_web.py — the §3 roundtrip, but every file goes through the WEB WIZARD (/upload/case)
in a real browser instead of the script's own WebSocket client (ingest-plan §7.3, check 7g).

Same accounts-and-circle setup as `roundtrip_check.py` (relatives grant the proband EDIT here,
because the wizard uploads their files on their behalf), same ten-layer `compare_case`, same
report writer. The point is the DIFFERENCE: the script path is the baseline (p3: 879 items,
real diffs 0); anything the page path adds is loss introduced by the UI.

    .venv/bin/python3 tools/roundtrip_web.py --cases JD-50,JD-52 \\
        --batch results/joint_dx/rare_coding-p3/20260922-095441 --out reports/roundtrip/web-p3
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

log = logging.getLogger("roundtrip_web")
WEB = "http://127.0.0.1:28086"
API = "http://127.0.0.1:28085"


async def prepare_accounts(s, api: str, cid: str, roles: list[str], prefix: str = "ui") -> dict[str, tuple[str, str]]:
    accts: dict[str, tuple[str, str]] = {}
    for role in ["proband", *[r for r in roles if r != "proband"]]:
        accts[role] = await rt.login(s, api, f"{prefix}-{cid.lower()}-{role}@rare.test")
    uids = {role: uid for role, (_, uid) in accts.items()}
    await rt.cleanup(list(uids.values()))
    if len(roles) > 1:
        nick = {"father": f"{cid}-F", "mother": f"{cid}-M"}
        await rt.make_circle(uids["proband"], {uids[r]: nick[r] for r in roles if r != "proband"}, member_access=2)
    return accts


async def drive_wizard(page, web: str, token: str, email: str, slots: dict[str, list[str]], owners: dict[str, str],
                       console: list[str], timeout_ms: int = 20 * 60 * 1000) -> list[dict]:
    """Fill the wizard, press start, wait until every step is terminal. → [{slot, status, message}]."""
    await page.goto(f"{web}/login")
    await page.evaluate("([t, e]) => { localStorage.setItem('access_token', t); localStorage.setItem('user_email', e);"
                        " localStorage.setItem('user_name', ''); }", [token, email])
    await page.goto(f"{web}/upload/case")
    await page.wait_for_selector("[data-testid=proband-select] option", state="attached", timeout=60_000)
    for slot, paths in slots.items():
        if not paths:
            continue
        await page.set_input_files(f"[data-testid=slot-{slot}-input]", paths)
        if slot in owners:
            sel = page.locator(f"[data-testid=slot-{slot}-owner]")
            await sel.locator(f"option[value='{owners[slot]}']").wait_for(state="attached", timeout=30_000)
            await sel.select_option(owners[slot])
    await page.click("[data-testid=start-upload]")
    await page.wait_for_selector("[data-testid=step-0]", timeout=30_000)
    await page.wait_for_function(
        "() => { const s = [...document.querySelectorAll('[data-testid^=\"step-\"][data-status]')];"
        " return s.length > 0 && s.every(x => x.dataset.status === 'completed' || x.dataset.status === 'failed'); }",
        timeout=timeout_ms, polling=1000)
    steps = await page.eval_on_selector_all(
        "[data-testid^='step-'][data-status]",
        "els => els.map(e => ({slot: e.dataset.slot, status: e.dataset.status, message: (e.querySelector('[data-testid$=\"-error\"]')||{}).textContent || ''}))")
    return steps


async def run_case_web(browser, s, batch: Path, cid: str, out_dir: Path, web: str = WEB, api: str = API) -> dict:
    case, sp = rt.load_case(batch, cid)
    rare = case["case"]["adjudication"]["rare"]
    att = rare.get("attachments") or {}
    gen = att.get("genome") or {}
    roles = list((gen.get("files") or {}).keys())
    accts = await prepare_accounts(s, api, cid, roles)
    uids = {role: uid for role, (_, uid) in accts.items()}
    nar = att.get("narrative") or {}
    if nar.get("path") and Path(nar["path"]).is_file():
        md, src = Path(nar["path"]), "attachment"
    else:
        md, src = out_dir / f"{cid}.md", "rendered_from_ledger"
        md.write_text(rt.narrative_md(cid, sp), encoding="utf-8")
    slots = {"ped": [gen["ped"]["path"]] if gen.get("ped") else [],
             "vcf_father": [gen["files"]["father"]["path"]] if "father" in roles else [],
             "vcf_mother": [gen["files"]["mother"]["path"]] if "mother" in roles else [],
             "vcf_proband": [gen["files"]["proband"]["path"]] if "proband" in roles else [],
             "narrative": [str(md)],
             "dicom": [x["path"] for x in (att.get("imaging") or {}).get("series") or []]}
    owners = {k: uids[r] for k, r in (("vcf_father", "father"), ("vcf_mother", "mother")) if r in uids}
    ctx = await browser.new_context()
    page = await ctx.new_page()
    console: list[str] = []
    page.on("console", lambda m: console.append(m.text[:200]) if m.type == "error" else None)
    t0 = time.time()
    try:
        steps = await drive_wizard(page, web, accts["proband"][0], f"ui-{cid.lower()}-proband@rare.test", slots, owners, console)
        # the page's own results panel: every uploaded VCF shows up and settles
        want_samples = sum(1 for k in ("vcf_father", "vcf_mother", "vcf_proband") if slots[k])
        await page.wait_for_function(
            f"() => {{ const s = [...document.querySelectorAll('[data-testid^=\"sample-\"]')];"
            f" return s.length >= {want_samples} && s.every(x => x.dataset.status === 'ready' || x.dataset.status === 'failed'); }}",
            timeout=15 * 60 * 1000, polling=2000)
        ui_samples = await page.eval_on_selector_all("[data-testid^='sample-']", "els => els.map(e => e.dataset.status)")
    finally:
        await ctx.close()
    from mirobody_rare.repo import PgRepo
    repo = PgRepo()
    for role in roles:
        await rt.wait_for(lambda r=role: rt._ready(repo, uids[r]))
    if sp.get("evidence_ledger"):
        await rt.wait_for(lambda: repo.phenotypes(uids["proband"]), timeout=180)
    await asyncio.sleep(5)
    events = {"case": cid, "uids": uids, "narrative_source": src,
              "steps": [(f"{x['slot']}", x["status"]) for x in steps],
              "ui": {"console_errors": console, "ui_samples": ui_samples, "seconds": round(time.time() - t0)}}
    log.info("[%s] via wizard in %ds: %s | ui samples %s | console errors %d", cid, time.time() - t0,
             [x["status"] for x in steps], ui_samples, len(console))
    return {"case": case, "sp": sp, "events": events, "uids": uids, "narrative": md}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--web", default=WEB)
    ap.add_argument("--api", default=API)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    batch = (rt.HAENV / a.batch) if not Path(a.batch).is_absolute() else Path(a.batch)
    out = Path(a.out or f"reports/roundtrip/web-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    import aiohttp
    from playwright.async_api import async_playwright
    runs = []
    async with async_playwright() as pw, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
        browser = await pw.chromium.launch()
        for cid in a.cases.split(","):                       # one at a time: a person uses one browser
            runs.append(await run_case_web(browser, s, batch, cid, out, a.web, a.api))
        await browser.close()
    items = []
    for r in runs:
        it, _ = await rt.compare_case(r)
        items += it
        log.info("[%s] compared: %d items, real diffs %d", r["case"]["case_id"], len(it),
                 sum(1 for i in it if i["kind"] in ("transport", "version", "order", "coding")))
    rep = await rt.build_report(batch, runs, items)
    rep["env"]["path"] = "web_wizard"
    rep["ui"] = {r["case"]["case_id"]: r["events"]["ui"] for r in runs}
    from mirobody_rare.roundtrip.report import render_html, render_samples_md
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "samples.md").write_text(render_samples_md(rep), encoding="utf-8")
    (out / "summary.html").write_text(render_html(rep), encoding="utf-8")
    print(json.dumps({"out": str(out), "counts": rep["counts"], "layers": rep["layers"],
                      "console_errors": sum(len(v["console_errors"]) for v in rep["ui"].values())}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
