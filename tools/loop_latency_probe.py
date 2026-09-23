"""loop_latency_probe.py — does background file processing starve the API? (rare-mvp-plan §17.5 item 5)

Polls a DB-free GET (`/api/health`, ≈1 ms idle) every 50 ms for a quiet baseline, then uploads a large VCF over
the upload WebSocket and keeps polling until the sample leaves `parsing`. A CPU-bound parse on the
server's event loop shows up directly as request latency here; the chat p95 would inherit it.

    .venv/bin/python3 tools/loop_latency_probe.py --vcf <big.vcf.gz> [--api http://127.0.0.1:28085]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roundtrip_check as rt                                            # noqa: E402


def _stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n": len(xs), "p50_ms": round(q(0.5) * 1000, 1), "p95_ms": round(q(0.95) * 1000, 1),
            "p99_ms": round(q(0.99) * 1000, 1), "over_1s": sum(1 for x in xs if x > 1.0), "max_ms": round(xs[-1] * 1000, 1),
            "mean_ms": round(statistics.fmean(xs) * 1000, 1)}


async def _poll(s, url: str, headers: dict, stop: asyncio.Event, out: list[float], stalls: list | None = None, t0: float = 0.0) -> None:
    while not stop.is_set():
        t = time.perf_counter()
        async with s.get(url, headers=headers) as r:
            await r.read()
        dt = time.perf_counter() - t
        out.append(dt)
        if dt > 0.3 and stalls is not None:
            stalls.append((round(time.time() - t0, 1), round(dt, 2)))
        await asyncio.sleep(0.05)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vcf", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:28085")
    ap.add_argument("--email", default="probe-loop@rare.test")
    ap.add_argument("--baseline", type=float, default=15.0)
    ap.add_argument("--path", default="/api/health", help="what to poll; a DB-bound path measures the DB too")
    ap.add_argument("--timeout", type=float, default=1800.0)
    a = ap.parse_args()
    import aiohttp
    from mirobody_rare.repo import PgRepo
    vcf = Path(a.vcf)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=a.timeout)) as s:
        tok, uid = await rt.login(s, a.api, a.email)
        await rt.cleanup([uid])
        h = {"Authorization": f"Bearer {tok}"}
        url = f"{a.api}{a.path}"
        base: list[float] = []
        stop = asyncio.Event()
        task = asyncio.create_task(_poll(s, url, h, stop, base))
        await asyncio.sleep(a.baseline)
        stop.set(); await task
        busy: list[float] = []
        stalls: list[tuple[float, float]] = []                    # (seconds since upload start, request seconds)
        stop = asyncio.Event()
        t0 = time.time()
        task = asyncio.create_task(_poll(s, url, h, stop, busy, stalls, t0))
        # the upload gets its own thread and loop: base64-encoding 150 MB on THIS loop stalls the
        # poller for ~2 s and reads as a server stall (it was, in the first version of this probe)
        async def _up():
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=a.timeout)) as s2:
                return await rt.upload(s2, a.api, tok, [(vcf.name, vcf, "application/gzip")])
        up = await asyncio.to_thread(asyncio.run, _up())
        t_up = time.time() - t0
        repo = PgRepo()
        while time.time() - t0 < a.timeout:
            smp = await repo.samples_of(uid)
            if smp and all(x.get("status") in ("ready", "failed") for x in smp):
                break
            await asyncio.sleep(2)
        t_all = time.time() - t0
        stop.set(); await task
        print(json.dumps({"file": vcf.name, "bytes": vcf.stat().st_size, "upload_s": round(t_up, 1),
                          "until_terminal_s": round(t_all, 1),
                          "samples": [(x.get("id"), x.get("status")) for x in (await repo.samples_of(uid))],
                          "baseline": _stats(base), "during": _stats(busy), "stalls_over_300ms": stalls,
                          "upload_status": (up or {}).get("status")}, ensure_ascii=False, indent=1))
        await rt.cleanup([uid])


if __name__ == "__main__":
    asyncio.run(main())
