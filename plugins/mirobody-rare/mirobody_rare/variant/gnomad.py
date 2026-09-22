"""gnomAD v4 population frequencies for the candidate variants (plan §4.4 ④).

Called only AFTER the local filters: a case has a handful of ClinVar P/LP candidates, so
one GraphQL POST with aliases covers them all. Results are cached on disk per variant id
and `source_version`; `not_found` is recorded as such (absent from gnomAD ≠ AF 0: the
tool caveat "af NULL = not queried" stays true for variants never sent).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .._config import load as load_cfg

log = logging.getLogger(__name__)

API = "https://gnomad.broadinstitute.org/api"
DATASET = "gnomad_r4"
VARIANT_FIELDS = "variant_id exome { ac an af populations { id ac an } } genome { ac an af populations { id ac an } }"
_SUBPOP_SUFFIX = ("_XX", "_XY")
#: bottleneck / founder populations: a variant at 2–3 % in one of these and < 0.1 % elsewhere is a
#: founder mutation, not a common variant (HMBS in fin, MEFV in mid/asj: P5e). popmax is taken
#: over the continental groups; the all-population value is reported beside it, never used to drop.
_BOTTLENECK = {"fin", "asj", "mid", "ami", "oth", "remaining"}
# popmax is taken over these only: bottleneck populations (above) and the HGDP / 1KG sub-cohorts
# (`hgdp:maya`, `1kg:gbr` … a few dozen genomes each) inflate one carrier into a "common" allele
_CONTINENTAL = {"afr", "amr", "eas", "nfe", "sas"}


def variant_id(chrom, pos, ref, alt) -> str:
    return f"{str(chrom).replace('chr', '')}-{int(pos)}-{ref}-{alt}"


def parse_variant(v: dict | None) -> dict:
    if not v:
        return {"af_global": None, "af_popmax": None, "popmax_pop": None, "allele_count": None, "not_found": True}
    ac = an = 0
    pops: dict[str, list[int]] = {}
    for part in ("exome", "genome"):
        d = v.get(part) or {}
        ac += int(d.get("ac") or 0)
        an += int(d.get("an") or 0)
        for p in d.get("populations") or []:
            pid = str(p.get("id") or "")
            if not pid or pid.endswith(_SUBPOP_SUFFIX) or pid == "remaining":
                continue
            acc = pops.setdefault(pid, [0, 0])
            acc[0] += int(p.get("ac") or 0)
            acc[1] += int(p.get("an") or 0)
    af = ac / an if an else None
    best_all = max(((a / n, pid) for pid, (a, n) in pops.items() if n), default=(None, None))
    best = max(((a / n, pid) for pid, (a, n) in pops.items() if n and pid in _CONTINENTAL), default=(None, None))
    return {"af_global": af, "af_popmax": best[0], "popmax_pop": best[1],
            "af_popmax_all": best_all[0], "popmax_pop_all": best_all[1], "allele_count": ac, "not_found": False}


class GnomadClient:
    def __init__(self, cache_dir: Path | None = None, fetch=None, timeout: float = 20.0, batch: int = 50):
        from .._config import cache_dir as _cache_root
        self.cache = Path(cache_dir or (_cache_root() / "gnomad"))
        self.cache.mkdir(parents=True, exist_ok=True)
        self._fetch = fetch or self._http
        self.timeout = timeout
        self.batch = batch
        self.version = f"{DATASET}:{time.strftime('%Y-%m')}"

    async def _http(self, query: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as s:
            async with s.post(API, json={"query": query}) as r:
                r.raise_for_status()
                return await r.json()

    def _cached(self, vid: str) -> dict | None:
        p = self.cache / f"{vid}.json"
        if p.exists():
            try:
                rec = json.loads(p.read_text())
            except ValueError:
                return None
            # entries written before the continental-popmax rule lack `af_popmax_all`
            # (their `af_popmax` may be a bottleneck-population value) -> refetch
            if not rec.get("not_found") and "af_popmax_all" not in rec:
                return None
            return rec
        return None

    async def lookup_many(self, keys: list[tuple]) -> dict[tuple, dict]:
        out: dict[tuple, dict] = {}
        todo: list[tuple] = []
        for k in keys:
            rec = self._cached(variant_id(*k))
            if rec is not None:
                out[k] = rec
            else:
                todo.append(k)
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            q = "{ " + " ".join(f'v{j}: variant(variantId: "{variant_id(*k)}", dataset: {DATASET}) {{ {VARIANT_FIELDS} }}'
                                for j, k in enumerate(chunk)) + " }"
            try:
                resp = await self._fetch(q)
            except Exception as e:                      # noqa: BLE001
                log.warning("[gnomad] batch of %d failed: %s (frequencies stay NULL = not queried)", len(chunk), e)
                continue
            data = resp.get("data") or {}
            for j, k in enumerate(chunk):
                rec = parse_variant(data.get(f"v{j}"))
                rec["source_version"] = self.version
                (self.cache / f"{variant_id(*k)}.json").write_text(json.dumps(rec))
                out[k] = rec
        return out


def get_client() -> GnomadClient | None:
    cfg = (load_cfg().get("variant", {}) or {}).get("gnomad", {}) or {}
    if not cfg.get("enabled", True):
        return None
    return GnomadClient(timeout=float(cfg.get("timeout", 20)))
