"""asyncpg pool for the rare-disease schema. DSN and schema come from `config.reference`
(env `MIROBODY_RARE_PG_DSN` / `MIROBODY_RARE_PG_SCHEMA` override); every connection sets
`search_path` to that schema, so the same DDL files replay into `mirobody_rare` without
touching the main schema (the deployment note in plan §2 about PGOPTIONS, done in code)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import asyncpg

from .._config import load as load_cfg

log = logging.getLogger(__name__)
_POOLS: dict[int, asyncpg.Pool] = {}      # one pool per event loop: the sync bridge has its own loop


def settings() -> tuple[str, str]:
    cfg = load_cfg().get("reference", {})
    dsn = os.environ.get("MIROBODY_RARE_PG_DSN") or cfg.get("pg_dsn") or ""
    schema = os.environ.get("MIROBODY_RARE_PG_SCHEMA") or cfg.get("pg_schema") or "mirobody_rare"
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError(f"bad schema name {schema!r}")
    return dsn, schema


async def get_pool() -> asyncpg.Pool:
    import asyncio
    key = id(asyncio.get_running_loop())
    if key not in _POOLS:
        dsn, schema = settings()
        if not dsn:
            raise RuntimeError("MIROBODY_RARE_PG_DSN (or config.reference.pg_dsn) is not set")
        # 🔴 search_path as a STARTUP parameter, not `SET` in `init`: asyncpg runs `RESET ALL` when a
        # connection goes back to the pool, which undoes a SET and silently sends every later
        # statement to `public` (that is exactly what happened on 2026-09-21: five DDL files and
        # a ClinVar load landed in public.* on the shared test database).
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8, timeout=30,
                                         server_settings={"search_path": f"{schema},public"})
        async with pool.acquire() as c:
            await c.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        _POOLS[key] = pool
        log.info("[pg] pool ready, schema=%s", schema)
    return _POOLS[key]


async def close_pool() -> None:
    import asyncio
    pool = _POOLS.pop(id(asyncio.get_running_loop()), None)
    if pool is not None:
        await pool.close()


SCHEMA_FILES = ("32_phenotype.sql", "33_variant.sql", "34_pedigree_consent.sql", "35_signal_index.sql", "36_rare_reference.sql")


def schema_dir() -> Path:
    import mirobody
    return Path(mirobody.__file__).resolve().parent / "schema"


async def run_schema(files: tuple[str, ...] = SCHEMA_FILES) -> list[str]:
    """Replay the rare-disease DDL files into the configured schema. Idempotent by contract."""
    pool = await get_pool()
    done = []
    async with pool.acquire() as c:
        for f in files:
            sql = (schema_dir() / f).read_text(encoding="utf-8")
            await c.execute(sql)
            done.append(f)
            log.info("[pg] replayed %s", f)
    return done
