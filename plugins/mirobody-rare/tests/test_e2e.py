"""End to end against a RUNNING deployment (upload → handler → rows → chat tool call).
Enabled by MIROBODY_RARE_E2E_BASE=http://host:port; logs in with the demo account."""
import asyncio
import base64
import json
import os
import time
import uuid

import pytest

BASE = os.environ.get("MIROBODY_RARE_E2E_BASE", "")
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not BASE, reason="MIROBODY_RARE_E2E_BASE not set")]
VCF = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/proband.vcf.gz"
PED = "/data/xfs_recovery/caill/haenv-rare/derived/rare_attachments/JD-55/JD-55.ped"


async def _login(s):
    await s.post(f"{BASE}/email/login", json={"email": "you@mirobody.ai"})
    async with s.post(f"{BASE}/email/verify", json={"email": "you@mirobody.ai", "code": "111111"}) as r:
        d = await r.json()
    assert d["code"] == 0, d
    return d["data"]["access_token"]


async def _upload(s, token):
    ws_url = BASE.replace("http", "ws", 1) + f"/ws/upload-health-report?token={token}"
    files = [("proband.vcf.gz", VCF, "application/gzip"), ("JD-55.ped", PED, "text/plain")]
    async with s.ws_connect(ws_url, max_msg_size=64 * 1024 * 1024) as ws:
        await ws.receive(timeout=20)
        mid = str(uuid.uuid4())
        await ws.send_json({"type": "upload_start", "messageId": mid, "sessionId": str(uuid.uuid4()), "query": "e2e",
                            "files": [{"filename": n, "size": os.path.getsize(p), "type": ct} for n, p, ct in files]})
        for n, p, ct in files:
            data = open(p, "rb").read()
            ch = 2 * 1024 * 1024
            total = (len(data) + ch - 1) // ch
            for i in range(total):
                await ws.send_json({"type": "upload_chunk", "messageId": mid, "filename": n, "chunkIndex": i, "totalChunks": total,
                                    "chunk": base64.b64encode(data[i * ch:(i + 1) * ch]).decode()})
        t0 = time.time()
        while time.time() - t0 < 300:
            m = await ws.receive(timeout=60)
            d = json.loads(m.data)
            if d.get("type") == "upload_completed" and d.get("status") != "uploading":
                return d
    raise AssertionError("upload did not complete")


async def test_upload_then_chat_reads_variants():
    import aiohttp
    async with aiohttp.ClientSession() as s:
        token = await _login(s)
        done = await _upload(s, token)
        assert done["status"] == "completed", done
        assert any(f.get("type") in ("vcf", "ped") for f in done["results"].get("files", [])), done["results"]
        q = "我上传的基因检测里有哪些致病变异？请用工具查询后按基因列出。"
        async with s.post(f"{BASE}/api/chat", json={"question": q, "session_id": f"e2e-{uuid.uuid4()}"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=aiohttp.ClientTimeout(total=300)) as r:
            body = await r.text()
        assert "query_variant" in body, body[:500]
        assert "TP53" in body
