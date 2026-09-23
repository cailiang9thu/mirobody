"""delete_cascade_check.py — delete through the product's own API and prove the derived rows went too.

A trio (PED + three VCFs) and the proband's narrative and a DICOM series are uploaded over the
upload WebSocket; then the files are deleted one at a time with `POST /api/v1/data/delete-files`
— exactly what the chat UI calls — and after each delete the rare-disease tables are read back:

    delete the father's VCF   → his sample and variants gone; the proband's trio inheritance back to unknown
    delete the narrative      → the proband's phenotypes / review items / diagnoses from it gone
    delete the DICOM zip      → its index row gone
    delete the PED            → the family gone
    delete the proband's VCF  → nothing rare-disease left under the proband

    .venv/bin/python3 tools/delete_cascade_check.py [--api http://127.0.0.1:28085] [--case JD-50]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roundtrip_check as rt                                            # noqa: E402


async def counts(p, uid: str) -> dict:
    q = {"samples": "SELECT count(*) FROM th_sequencing_sample WHERE user_id = $1",
         "variants": "SELECT count(*) FROM th_variant WHERE user_id = $1",
         "inherited": "SELECT count(*) FROM th_variant WHERE user_id = $1 AND COALESCE(inheritance, 'unknown') <> 'unknown'",
         "phenotypes": "SELECT count(*) FROM th_phenotype WHERE user_id = $1 AND NOT deleted",
         "reviews": "SELECT count(*) FROM th_phenotype_review WHERE user_id = $1",
         "diagnoses": "SELECT count(*) FROM th_disease_code WHERE user_id = $1 AND NOT deleted",
         "signals": "SELECT count(*) FROM th_signal_object WHERE user_id = $1",
         "pedigrees": "SELECT count(*) FROM th_pedigree WHERE owner_user_id = $1",
         "files": "SELECT count(*) FROM th_files WHERE user_id = $1 AND is_del = false"}
    return {k: int(await p.fetchval(s, uid)) for k, s in q.items()}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:28085")
    ap.add_argument("--case", default="JD-50")
    ap.add_argument("--batch", default="results/joint_dx/rare_coding-p3/20260922-095441")
    a = ap.parse_args()
    import aiohttp
    from mirobody_rare.repo import PgRepo
    case, sp = rt.load_case(rt.HAENV / a.batch, a.case)
    att = case["case"]["adjudication"]["rare"]["attachments"]
    gen = att["genome"]
    p = await rt.pool()
    steps, ok = [], True
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
        acc = {r: await rt.login(s, a.api, f"del-{a.case.lower()}-{r}@rare.test") for r in ("proband", "father", "mother")}
        uid = {r: u for r, (_, u) in acc.items()}
        await rt.cleanup(list(uid.values()))
        await rt.make_circle(uid["proband"], {uid["father"]: f"{a.case}-F", uid["mother"]: f"{a.case}-M"})
        tok = acc["proband"][0]
        await rt.upload(s, a.api, tok, [(Path(gen["ped"]["path"]).name, Path(gen["ped"]["path"]), "text/plain")])
        for r in ("father", "mother", "proband"):
            f = Path(gen["files"][r]["path"])
            await rt.upload(s, a.api, acc[r][0], [(f.name, f, "application/gzip")])
        md = Path(att["narrative"]["path"])
        await rt.upload(s, a.api, tok, [(md.name, md, "text/markdown")])
        z = Path(att["imaging"]["series"][0]["path"])
        await rt.upload(s, a.api, tok, [(z.name, z, "application/zip")])
        repo = PgRepo()
        for r in uid:
            await rt.wait_for(lambda r=r: rt._ready(repo, uid[r]))
        await rt.wait_for(lambda: repo.phenotypes(uid["proband"]), timeout=180)
        await asyncio.sleep(8)                                 # trio backfill after the last VCF
        before = {r: await counts(p, u) for r, u in uid.items()}
        steps.append(("uploaded", before))
        # file_name is encrypted (decrypt_content needs the main package's connection); the stored key
        # keeps the extension: web_uploads/<uuid>.gz / .md / .zip / .ped
        files = {r: [dict(x) for x in await p.fetch("SELECT file_key, created_source_id, file_key AS name"
                                                     " FROM th_files WHERE user_id = $1 AND is_del = false", u)] for r, u in uid.items()}

        async def delete(role: str, pred) -> dict:
            [f] = [f for f in files[role] if pred(f["name"])]
            async with s.post(f"{a.api}/api/v1/data/delete-files", headers={"Authorization": f"Bearer {acc[role][0]}"},
                              json={"message_id": f["created_source_id"], "file_keys": [f["file_key"]]}) as resp:
                body = await resp.json()
            d = (body.get("data") or body)
            return {"http": resp.status, "cascade_errors": [x.get("cascade_errors") for x in d.get("deleted_files") or [] if x.get("cascade_errors")]}

        plan = [("father VCF", "father", lambda n: n.endswith(".gz"),
                 lambda c: c["father"]["samples"] == 0 and c["father"]["variants"] == 0 and c["proband"]["inherited"] == 0),
                ("narrative", "proband", lambda n: n.endswith(".md"),
                 lambda c: c["proband"]["phenotypes"] == 0 and c["proband"]["reviews"] == 0),
                ("DICOM zip", "proband", lambda n: n.endswith(".zip"), lambda c: c["proband"]["signals"] == 0),
                ("PED", "proband", lambda n: n.endswith(".ped"), lambda c: c["proband"]["pedigrees"] == 0),
                ("proband VCF", "proband", lambda n: n.endswith(".gz"),
                 lambda c: all(v == 0 for v in c["proband"].values()) and c["mother"]["samples"] == 1)]
        for label, role, pred, check in plan:
            res = await delete(role, pred)
            after = {r: await counts(p, u) for r, u in uid.items()}
            passed = res["http"] == 200 and not res["cascade_errors"] and check(after)
            ok &= passed
            steps.append((f"delete {label}", {"pass": passed, **res, **after}))
        await rt.cleanup(list(uid.values()))
    for name, d in steps:
        print(name, json.dumps(d, ensure_ascii=False))
    print("ALL PASS" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
