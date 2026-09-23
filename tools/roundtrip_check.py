"""ingest-plan §3: drive N haenv cases through the REAL deployment (accounts → circles → PED →
parents' VCFs → proband VCF → narrative → DICOM), read everything back (raw SQL and the MCP
tools), compare nine layers against the source directory, classify every difference, and write
report.json / samples.md / summary.html.

    set -a; . ~/caill/.mirobody_rare_env; set +a
    .venv/bin/python3 tools/roundtrip_check.py --cases JD-50,JD-55 --batch results/joint_dx/rare_coding-p1/<batch>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path

import aiohttp

log = logging.getLogger("roundtrip")
HAENV = Path(os.environ.get("HAENV_RARE_ROOT", "/data/xfs_recovery/caill/haenv-rare"))
STORAGE = Path(os.environ.get("MIROBODY_LOCAL_STORAGE", "/data/xfs_recovery/caill/mirobody-rare/.theta/mcp/upload"))
PASSWORD = "Roundtrip2026!"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def jwt_sub(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return str(json.loads(base64.urlsafe_b64decode(payload))["sub"])


# ---------------------------------------------------------------- server calls
async def login(s: aiohttp.ClientSession, base: str, email: str) -> tuple[str, str]:
    async with s.post(f"{base}/password/register", json={"email": email, "password": PASSWORD}) as r:
        d = await r.json()
    if d.get("code") != 0:
        async with s.post(f"{base}/password/login", json={"email": email, "password": PASSWORD}) as r:
            d = await r.json()
    assert d.get("code") == 0, d
    tok = d["data"]["access_token"]
    return tok, jwt_sub(tok)


async def upload(s: aiohttp.ClientSession, base: str, token: str, files: list[tuple[str, Path, str]], query: str = "") -> dict:
    ws_url = base.replace("http", "ws", 1) + f"/ws/upload-health-report?token={token}"
    async with s.ws_connect(ws_url, max_msg_size=64 * 1024 * 1024) as ws:
        await ws.receive(timeout=20)
        mid = str(uuid.uuid4())
        await ws.send_json({"type": "upload_start", "messageId": mid, "sessionId": str(uuid.uuid4()), "query": query,
                            "files": [{"filename": n, "size": p.stat().st_size, "type": ct} for n, p, ct in files]})
        first = json.loads((await ws.receive(timeout=20)).data)
        if first.get("type") == "upload_error":
            return {"status": "refused", "message": first.get("message")}
        for n, p, ct in files:
            data = p.read_bytes()
            ch = 4 * 1024 * 1024
            total = (len(data) + ch - 1) // ch
            for i in range(total):
                await ws.send_json({"type": "upload_chunk", "messageId": mid, "filename": n, "chunkIndex": i, "totalChunks": total,
                                    "chunk": base64.b64encode(data[i * ch:(i + 1) * ch]).decode()})
        t0 = time.time()
        while time.time() - t0 < 600:
            m = await ws.receive(timeout=120)
            if m.type != aiohttp.WSMsgType.TEXT:
                break
            d = json.loads(m.data)
            if d.get("type") == "upload_completed" and d.get("status") != "uploading":
                return d
    return {"status": "timeout"}


# ---------------------------------------------------------------- database
async def pool():
    from mirobody_rare.reference.db import get_pool
    return await get_pool()


async def cleanup(uids: list[str]) -> None:
    """Everything the case's accounts produced: DB rows AND the stored bytes (th_files.file_key ->
    storage object); accounts themselves are kept and reused."""
    p = await pool()
    ids = [int(u) for u in uids]
    async with p.acquire() as c:
        keys = [r["file_key"] for r in await c.fetch("SELECT file_key FROM th_files WHERE user_id = ANY($1::text[])", uids)]
        if keys:
            from mirobody.utils.config.storage.factory import get_storage_client
            st = get_storage_client()
            for k in keys:
                try:
                    await st.delete(k)
                except Exception as e:                       # noqa: BLE001
                    log.warning("[cleanup] storage delete %s: %s", k, e)
        await c.execute("DELETE FROM th_variant_annotation WHERE variant_id IN (SELECT id FROM th_variant WHERE user_id = ANY($1::text[]))", uids)
        for t in ("th_variant", "th_sequencing_sample", "th_phenotype", "th_phenotype_review", "th_disease_code", "th_signal_object", "th_consent", "th_files"):
            await c.execute(f"DELETE FROM {t} WHERE user_id = ANY($1::text[])", uids)
        await c.execute("DELETE FROM th_pedigree_member WHERE user_id = ANY($1::text[]) OR pedigree_id IN (SELECT id FROM th_pedigree WHERE owner_user_id = ANY($1::text[]))", uids)
        await c.execute("DELETE FROM th_pedigree WHERE owner_user_id = ANY($1::text[])", uids)
        await c.execute("DELETE FROM care_circle_members WHERE user_id = ANY($1::int[]) OR care_circle_id IN (SELECT id FROM care_circles WHERE owner_user_id = ANY($1::int[]))", ids)
        await c.execute("DELETE FROM care_circles WHERE owner_user_id = ANY($1::int[])", ids)


async def purge_orphans(base_dir: str) -> int:
    """Local-storage objects no th_files row references any more (residue of runs before cleanup
    deleted bytes). Never touches a key that is still referenced."""
    p = await pool()
    live = {r["file_key"] for r in await p.fetch("SELECT file_key FROM th_files")}
    n = 0
    base = Path(base_dir)
    for f in base.rglob("*"):                      # keys are paths relative to the base: web_uploads/<uuid>.gz
        if f.is_file() and str(f.relative_to(base)) not in live:
            f.unlink()
            n += 1
    return n


async def make_circle(owner: str, members: dict[str, str]) -> int:
    """owner's circle with `members` {uid: nickname} accepted at health_access=0 (analysis_only)."""
    p = await pool()
    async with p.acquire() as c:
        cid = await c.fetchval("INSERT INTO care_circles (owner_user_id, name) VALUES ($1, $2) RETURNING id", int(owner), f"roundtrip-{owner}")
        await c.execute("INSERT INTO care_circle_members (care_circle_id, user_id, role, status, health_access) VALUES ($1, $2, 2, 2, 2)", cid, int(owner))
        for uid, nick in members.items():
            await c.execute("INSERT INTO care_circle_members (care_circle_id, user_id, role, status, health_access, nickname) VALUES ($1, $2, 0, 2, 0, $3)", cid, int(uid), nick)
    return cid


async def wait_for(fn, timeout: float = 240, every: float = 3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = await fn()
        if v:
            return v
        await asyncio.sleep(every)
    return None


# ---------------------------------------------------------------- case material
def load_case(batch: Path, cid: str) -> tuple[dict, dict]:
    case = next(json.loads(l) for l in open(batch / "cases.jsonl") if json.loads(l)["case_id"] == cid)
    sp = next(json.loads(l)["sp"] for l in open(batch / "payloads.jsonl") if json.loads(l)["case_id"] == cid)
    return case, sp


def narrative_md(cid: str, sp: dict) -> str:
    sym = [e["symptom"] for e in sp["evidence_ledger"] if e.get("symptom")]
    notes = [e["note"] for e in sp["evidence_ledger"] if e.get("note")]
    prof = sp.get("user_profile", {})
    return (f"# 病例 {cid}\n\n## 病史描述\n患者{ '男' if prof.get('sex')=='M' else '女'}性,{prof.get('age_range','')}岁。\n"
            + "\n".join(f"{s}。" for s in sym) + "\n\n## 其他\n" + "\n".join(f"{n}。" for n in notes) + "\n")


# ---------------------------------------------------------------- run one case
async def run_case(s, base: str, batch: Path, cid: str, out_dir: Path) -> dict:
    case, sp = load_case(batch, cid)
    rare = case["case"]["adjudication"]["rare"]
    att = rare.get("attachments") or {}
    gen = att.get("genome") or {}
    roles = list((gen.get("files") or {}).keys())
    accts: dict[str, tuple[str, str]] = {}
    for role in ["proband", *[r for r in roles if r != "proband"]]:
        accts[role] = await login(s, base, f"haenv-{cid.lower()}-{role}@rare.test")
    uids = {role: uid for role, (_, uid) in accts.items()}
    await cleanup(list(uids.values()))
    events = {"case": cid, "uids": uids, "steps": []}
    # circle: parents accepted with access 0, nickname = PED individual id
    if len(roles) > 1:
        nick = {"father": f"{cid}-F", "mother": f"{cid}-M"}
        await make_circle(uids["proband"], {uids[r]: nick[r] for r in roles if r != "proband"})
        events["steps"].append("circle")
    tok_p = accts["proband"][0]
    # order: PED → parents' VCFs → proband VCF → narrative → DICOM
    if gen.get("ped"):
        d = await upload(s, base, tok_p, [(f"{cid}.ped", Path(gen["ped"]["path"]), "text/plain")])
        events["steps"].append(("ped", d.get("status")))
    for role in [r for r in roles if r != "proband"] + (["proband"] if "proband" in roles else []):
        f = gen["files"][role]
        d = await upload(s, base, accts[role][0], [(f"{role}.vcf.gz", Path(f["path"]), "application/gzip")])
        events["steps"].append((f"vcf:{role}", d.get("status")))
    # the record itself when the batch ships one (rare_narrative.py, 期刊体八节), else the
    # ledger rendered into sentences — a题包 built before 2026-09-22 has no narrative attachment
    nar = att.get("narrative") or {}
    if nar.get("path") and Path(nar["path"]).is_file():
        md = Path(nar["path"])
        events["narrative_source"] = "attachment"
    else:
        md = out_dir / f"{cid}.md"
        md.write_text(narrative_md(cid, sp), encoding="utf-8")
        events["narrative_source"] = "rendered_from_ledger"
    d = await upload(s, base, tok_p, [(f"{cid}.md", md, "text/markdown")])
    events["steps"].append(("narrative", d.get("status")))
    img = att.get("imaging") or {}
    for i, ser in enumerate(img.get("series") or []):
        d = await upload(s, base, tok_p, [(f"{cid}-series{i}.zip", Path(ser["path"]), "application/zip")])
        events["steps"].append((f"dicom:{i}", d.get("status")))
    # wait for async work: samples ready, phenotype rows (hook), backfill
    from mirobody_rare.repo import PgRepo
    repo = PgRepo()
    for role in roles:
        await wait_for(lambda r=role: _ready(repo, uids[r]))
    if sp.get("evidence_ledger"):
        await wait_for(lambda: repo.phenotypes(uids["proband"]), timeout=180)
    await asyncio.sleep(5)
    return {"case": case, "sp": sp, "events": events, "uids": uids, "narrative": md}


async def _ready(repo, uid):
    ss = await repo.samples_of(uid)
    return ss and all(x["status"] in ("ready", "failed") for x in ss)


# ---------------------------------------------------------------- compare
async def compare_case(run: dict, min_stars: int = 1) -> tuple[list[dict], list[dict]]:
    """→ (items, samples). item = {layer, case, kind, before, after}."""
    from mirobody_rare.repo import PgRepo
    from mirobody_rare.roundtrip.compare import classify, norm_gt, norm_key, truncate
    from mirobody_rare.tools import RareQueryService
    from mirobody_rare.variant import get_clinvar, read_candidates
    from mirobody_rare.genome import filter_common, annotate_gnomad
    from mirobody_rare._config import load as load_cfg
    repo = PgRepo(); svc = RareQueryService(repo); p = await pool()
    case, sp, uids = run["case"], run["sp"], run["uids"]
    cid = case["case_id"]; rare = case["case"]["adjudication"]["rare"]; att = rare.get("attachments") or {}; gen = att.get("genome") or {}
    items: list[dict] = []
    def add(layer, kind, before, after):
        items.append({"layer": layer, "case": cid, "kind": kind, "before": truncate(before, 300), "after": truncate(after, 300)})
    # 1 文件
    files = await p.fetch("SELECT id, user_id, file_key, content_hash FROM th_files WHERE user_id = ANY($1::text[]) AND is_del = false", list(uids.values()))
    by_hash = {r["content_hash"]: r for r in files}
    srcs = [(role, Path(f["path"]), f["sha256"]) for role, f in (gen.get("files") or {}).items()]
    if gen.get("ped"):
        srcs.append(("ped", Path(gen["ped"]["path"]), gen["ped"]["sha256"]))
    for i, ser in enumerate((att.get("imaging") or {}).get("series") or []):
        srcs.append((f"dicom{i}", Path(ser["path"]), ser["sha256"]))
    srcs.append(("narrative", run["narrative"], sha256(run["narrative"])))
    for role, path, want in srcs:
        row = by_hash.get(want)
        stored = STORAGE / row["file_key"] if row else None
        ok = bool(row) and stored is not None and stored.exists() and sha256(stored) == want
        add("文件", classify(sha_ok=ok), f"{role}: {path.name} sha256 {want[:12]}… {path.stat().st_size} B",
            f"th_files#{row['id'] if row else '—'} content_hash {row['content_hash'][:12] if row else '—'}… " + ("bytes verified" if ok else "NOT FOUND / MISMATCH"))
    # 2 样本  3 变异  4 注释  5 遗传来源
    cv = get_clinvar(); cfg = load_cfg().get("variant", {})
    for role, f in (gen.get("files") or {}).items():
        uid = uids[role]
        samples = await repo.samples_of(uid)
        smp = samples[0] if samples else None
        hdr_ok = bool(smp) and smp["reference"] == "GRCh38" and smp["status"] == "ready"
        add("样本", classify(equal=hdr_ok), f"{role}: {f['sample']} GRCh38", f"sample#{smp['id'] if smp else '—'} {smp['sample_label'] if smp else ''} {smp['reference'] if smp else ''} {smp['status'] if smp else ''}")
        cands, counts = read_candidates(f["path"], cv, sex=(sp.get("user_profile") or {}).get("sex") if role == "proband" else None)
        annotate_gnomad(cands)
        kept, common = filter_common([c for c in cands if (c.clinvar or {}).get("stars", 0) >= min_stars], float(cfg.get("max_af_popmax", 0.01)), float(cfg.get("max_af_popmax_recessive", 0.05)))
        stored = await repo.variants(uid, limit=10 ** 6)
        got = {norm_key(v["chrom"], v["pos"], v["ref"], v["alt"]): v for v in stored}
        truth = gen.get("variant") or {}
        tkey = norm_key(truth["chrom"], truth["pos"], truth["ref"], truth["alt"]) if truth else None
        for c in kept:
            v = got.get(c.key)
            tag = " (真值)" if c.key == tkey else ""
            if v is None:
                add("变异", "coding", f"{c.key} GT {c.gt}{tag}", "MISSING in th_variant")
            else:
                add("变异", classify(equal=norm_gt(v["genotype"]) == norm_gt(c.gt)), f"{c.key} GT {c.gt} {c.gene}{tag}", f"th_variant#{v['id']} {v['genotype']} {v['zygosity']} {v['gene_symbol']}")
                ann = {a["source"]: a for a in v.get("annotations", [])}
                ca = ann.get("clinvar")
                add("注释", classify(version_ok=bool(ca) and ca["source_version"] == cv.version, equal=bool(ca) and ca["clinical_significance"] == c.clinvar["clnsig"]),
                    f"ClinVar {c.clinvar['clnsig']} ★{c.clinvar['stars']} {cv.version}", f"{ca['clinical_significance'] if ca else '—'} {ca['source_version'] if ca else '—'}" + (f"; gnomAD af_popmax={ann['gnomad']['af_popmax']}" if ann.get("gnomad") else "; gnomAD not_found/not queried"))
                if role == "proband" and gen.get("genotypes") and len(gen["genotypes"]) == 3 and c.key == tkey:
                    exp = _expected_inh(gen["genotypes"])
                    add("遗传来源", classify(order_ok=v["inheritance"] != "unknown", equal=v["inheritance"] == exp), f"期望 {exp}(PED+父母 GT)", f"{v['inheritance']} is_de_novo={v['is_de_novo']}")
        for c in common:
            add("变异", "design", f"{c.key} {c.gene} gnomAD popmax {(c.gnomad or {}).get('af_popmax')}", "dropped by popmax rule (design)")
        add("变异", "design", f"{role}: {counts['n_records']} PASS records in file", f"{len(stored)} rows stored (only ClinVar P/LP ≥{min_stars}★ candidates, by design)")
        common_keys = {c.key for c in common}
        for k, v in got.items():
            if k not in {c.key for c in kept}:
                if k in common_keys:
                    ann = {a["source"]: a for a in v.get("annotations", [])}
                    add("变异", "design", f"{k} {v['gene_symbol']} gnomAD common", f"stored WITH frequency (af_popmax={(ann.get('gnomad') or {}).get('af_popmax')}); the shim drops it, the DB keeps it annotated (design)")
                else:
                    add("变异", "coding", "—", f"EXTRA row {k} {v['gene_symbol']}")
    # 6 家系
    if gen.get("ped"):
        fam = await repo.pedigree_of(uids["proband"])
        n_ped = sum(1 for l in Path(gen["ped"]["path"]).read_text().splitlines() if l.strip() and not l.startswith("#"))
        members = fam["members"] if fam else []
        mapped = sum(1 for m in members if m.get("user_id"))
        add("家系", classify(equal=bool(fam) and len(members) == n_ped and mapped == len(uids)), f"PED {n_ped} members; accounts for {len(uids)}",
            f"th_pedigree {fam['family_id'] if fam else '—'}: {len(members)} members, {mapped} linked to accounts, analysis_only={[m['individual_id'] for m in members if m.get('analysis_only')]}")
    # 7 影像
    img = att.get("imaging") or {}
    if img.get("series"):
        sig = await repo.signals(uids["proband"])
        add("影像", classify(equal=len(sig) == len(img["series"]) and all(x["deid_status"] == "done" for x in sig)),
            f"{len(img['series'])} series (TCIA {img.get('collection')})", f"{len(sig)} th_signal_object rows, deid={[x['deid_status'] for x in sig]}, modality={[x['modality'] for x in sig]}")
    # 8 表型
    gold = rare.get("hpo_gold") or []
    real_ids = case["question"]["injected_manifest"]["real_symptom_evidence_ids"]
    led = {e["evidence_id"]: e for e in sp["evidence_ledger"]}
    ph = await repo.phenotypes(uids["proband"])
    # What was uploaded decides what the expected sentence is: with a narrative attachment the
    # coder saw the record's own prose, so the span ledger (`narrative.spans`) is the gold text,
    # not the ledger's one-liner. Comparing against the wrong one reports false misses.
    nar_text, spans = "", {}
    if (att.get("narrative") or {}).get("path") and Path(att["narrative"]["path"]).is_file():
        nar_text = Path(att["narrative"]["path"]).read_text(encoding="utf-8")
        spans = {x["idx"]: x for x in att["narrative"].get("spans") or [] if x.get("hpo_id")}
    for g in gold:
        sp_g = spans.get(g["idx"])
        sent = nar_text[sp_g["start"]:sp_g["end"]] if sp_g else led[real_ids[g["idx"]]].get("symptom", "")
        hit = next((r for r in ph if r.get("source_text") and (r["source_text"] in sent or sent in r["source_text"])), None)
        if hit is None:
            rv = await p.fetchrow("SELECT kind, candidates FROM th_phenotype_review WHERE user_id=$1 AND (source_text = ANY($2::text[]) OR $3 LIKE '%' || source_text || '%') LIMIT 1", uids["proband"], [sent], sent)
            if rv:
                add("表型", "design", f"「{sent}」→ {g['hpo_id']} {g['label']}", f"queued for human review ({rv['kind']}, candidates {list(rv['candidates'] or [])[:3]}) — by design, not auto-written")
            else:
                add("表型", "coding", f"「{sent}」→ {g['hpo_id']} {g['label']} {g['polarity']}/{g['subject']}", "no th_phenotype row")
        else:
            ok = hit["hpo_id"] == g["hpo_id"] and bool(hit["negated"]) == (g["polarity"] == "absent") and hit["subject"] == g["subject"]
            add("表型", classify(equal=ok), f"「{sent}」→ {g['hpo_id']} {g['label']} {g['polarity']}/{g['subject']}",
                f"th_phenotype#{hit['id']} {hit['hpo_id']} {hit['hpo_label']} negated={hit['negated']} {hit['subject']}/{hit.get('subject_role') or ''} src={hit['source']} §{hit.get('section') or ''}")
    # 9 诊断
    dc = await repo.disease_codes(uids["proband"])
    add("诊断", classify(equal=any(d["code"] == rare["orpha"] for d in dc)), f"金标 {rare['orpha']}", f"th_disease_code {[d['code'] for d in dc]}")
    # 权限面(工具回读)
    if len(uids) > 1:
        r = await svc.query_variant({"user_id": uids["proband"]}, subject_id=uids["father"])
        add("权限", classify(permitted=False) if r["status"] == "error" else "coding", "父亲行经工具读(access=0)", f"{r['status']} {r.get('error_kind')}(预期拒)")
    n_own = len(await repo.variants(uids["proband"], limit=10 ** 6))
    r = await svc.query_variant({"user_id": uids["proband"]})
    add("权限", classify(equal=r["status"] == "ok" and len(r["data"]) == n_own), "本人行经工具读", f"{r['status']} {len(r.get('data') or [])} rows of {n_own}")
    return items, []


def _expected_inh(gts: dict) -> str:
    def carries(g): return any(a not in ("0", ".") for a in str(g).replace("|", "/").split("/"))
    f, m = carries(gts.get("father")), carries(gts.get("mother"))
    return "biparental" if f and m else "paternal" if f else "maternal" if m else "de_novo"


# ---------------------------------------------------------------- report
LAYERS = ("文件", "样本", "变异", "注释", "遗传来源", "家系", "影像", "表型", "诊断", "权限")


async def build_report(batch: Path, runs: list[dict], items: list[dict], seed: int = 7) -> dict:
    from mirobody_rare.roundtrip.report import KIND_ZH
    from mirobody_rare.variant import get_clinvar
    from mirobody_rare.hpo import get_adapter
    counts = {k: 0 for k in KIND_ZH}
    for it in items:
        counts[it["kind"]] += 1
    layers = []
    for L in LAYERS:
        rows = [i for i in items if i["layer"] == L]
        if not rows:
            continue
        real = sum(1 for i in rows if i["kind"] in ("transport", "version", "order", "coding"))
        layers.append({"layer": L, "n": len(rows), "same": sum(1 for i in rows if i["kind"] == "same"), "design": sum(1 for i in rows if i["kind"] in ("design", "permission")), "real": real})
    rnd = random.Random(seed)
    samples = []
    cases = [r["case"]["case_id"] for r in runs]
    for L in LAYERS:
        cs = [c for c in cases if any(i["layer"] == L and i["case"] == c for i in items)]
        for c in rnd.sample(cs, min(3, len(cs))):
            rows = [i for i in items if i["layer"] == L and i["case"] == c]
            pick = [i for i in rows if "真值" in i["before"]][:1] + rnd.sample([i for i in rows if "真值" not in i["before"]], min(2, len([i for i in rows if "真值" not in i["before"]])))
            samples.append({"layer": L, "case": c, "rows": [{"before": i["before"], "after": i["after"], "diff": KIND_ZH[i["kind"]]} for i in pick],
                            "count": {"n": len(rows), "k": len(pick), "same": sum(1 for i in rows if i["kind"] == "same"),
                                      "design": sum(1 for i in rows if i["kind"] in ("design", "permission")), "real": sum(1 for i in rows if i["kind"] in ("transport", "version", "order", "coding"))}})
    b = json.load(open(batch / "batch.json"))
    env = {"batch": batch.name, "world_sha": b.get("world_sha"), "kernel_sha256": b.get("kernel_sha256"), "clinvar": get_clinvar().version,
           "hpo_bundle": get_adapter().b.meta.get("built_at"), "schema": os.environ.get("MIROBODY_RARE_PG_SCHEMA", "mirobody_rare"),
           "reference_backend": os.environ.get("MIROBODY_RARE_REFERENCE_BACKEND", "memory"),
           "narrative": "attachment" if any((r["events"] or {}).get("narrative_source") == "attachment" for r in runs) else "rendered_from_ledger", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    gaps = ["th_disease_code 保留历史:表型排序的候选行不删,变异促升另加一行 source=nlp+variant(JD-77 因而并列 ORPHA:45448 与 ORPHA:70);读者按 source 取最新",
            "th_signal_object.file_id 现为 0(DICOM 处理器拿不到 th_files.id),影像层按 th_files.content_hash 校验字节、按行数与 deid 状态比对",
            "表型层的\"原始\"是 haenv 合成句子,不是真实病历;真实语料读数待 D9 金标",
            "样本层未比 bcftools stats(口径改为候选集,见 rare-mvp-testing 2.2)"]
    return {"batch": batch.name, "cases": cases, "env": env, "counts": counts, "layers": layers, "samples": samples, "gaps": gaps,
            "items": items, "events": [r["events"] for r in runs]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="")
    ap.add_argument("--batch", default="")
    ap.add_argument("--base", default="http://127.0.0.1:28085")
    ap.add_argument("--out", default="")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--purge-orphans", default=None, metavar="DIR", help="delete local storage objects in DIR that no th_files row references, then exit")
    a = ap.parse_args()
    if a.purge_orphans:
        print(json.dumps({"purged": await purge_orphans(a.purge_orphans)}))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    batch = (HAENV / a.batch) if not Path(a.batch).is_absolute() else Path(a.batch)
    out = Path(a.out or f"reports/roundtrip/{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(a.concurrency)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as s:
        async def one(cid):
            async with sem:
                t0 = time.time()
                r = await run_case(s, a.base, batch, cid, out)
                log.info("[%s] ingested in %.0fs: %s", cid, time.time() - t0, r["events"]["steps"])
                return r
        runs = await asyncio.gather(*(one(c) for c in a.cases.split(",")))
    items: list[dict] = []
    for r in runs:
        it, _ = await compare_case(r)
        items += it
        log.info("[%s] compared: %d items, real diffs %d", r["case"]["case_id"], len(it), sum(1 for i in it if i["kind"] in ("transport", "version", "order", "coding")))
    rep = await build_report(batch, runs, items)
    from mirobody_rare.roundtrip.report import render_html, render_samples_md
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "samples.md").write_text(render_samples_md(rep), encoding="utf-8")
    (out / "summary.html").write_text(render_html(rep), encoding="utf-8")
    print(json.dumps({"out": str(out), "counts": rep["counts"], "layers": rep["layers"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
