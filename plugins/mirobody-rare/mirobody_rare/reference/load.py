"""`mirobody-rare-load-reference [clinvar|hgnc|gene_hpo|orpha|all]` (plan §16.4).

Batch loads, version-aware, resumable: a table already holding `source_version` for this
source is skipped; a partially loaded one is completed by `ON CONFLICT DO UPDATE` (rows
already present are cheap upserts, missing rows are added). ClinVar: 349k P/LP rows in
10k-row batches through `copy_records_to_table` into a staging table, then one merge."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from tqdm import tqdm

from .._config import load as load_cfg, ontology_dir, setup_logging
from ..variant.clinvar import _STARS, _info, _source_vcf
from .db import get_pool, run_schema

log = logging.getLogger(__name__)
BATCH = 10_000


def _version(name: str, src: Path) -> str:
    return f"{name}:{time.strftime('%Y-%m-%d', time.gmtime(src.stat().st_mtime))}"


async def _have_version(conn, table: str, version: str) -> bool:
    return bool(await conn.fetchval(f"SELECT 1 FROM {table} WHERE source_version = $1 LIMIT 1", version))


async def load_clinvar(force: bool = False) -> dict:
    import gzip
    src = _source_vcf()
    ver = _version("clinvar_grch38_plp", src)
    pool = await get_pool()
    async with pool.acquire() as c:
        if not force and await _have_version(c, "ref_clinvar", ver):
            n = await c.fetchval("SELECT count(*) FROM ref_clinvar WHERE source_version=$1", ver)
            log.info("[load] ref_clinvar already at %s (%d rows); skip", ver, n)
            return {"table": "ref_clinvar", "version": ver, "rows": n, "skipped": True}
        await c.execute("CREATE TEMP TABLE stage_clinvar (LIKE ref_clinvar INCLUDING DEFAULTS EXCLUDING GENERATED) ON COMMIT PRESERVE ROWS")
        await c.execute("ALTER TABLE stage_clinvar DROP COLUMN IF EXISTS vkey")
        batch, n, t0 = [], 0, time.time()

        async def flush():
            nonlocal batch, n
            if batch:
                await c.copy_records_to_table("stage_clinvar", records=batch,
                                              columns=("chrom", "pos", "ref", "alt", "variation_id", "clnsig", "review_status", "stars",
                                                       "gene_symbol", "consequence", "condition_names", "disease_db", "source_version"))
                n += len(batch)
                batch = []
        with gzip.open(src, "rt") as fh:
            for line in tqdm(fh, desc="clinvar", unit="rec", mininterval=5):
                if line.startswith("#"):
                    continue
                col = line.rstrip("\n").split("\t", 8)
                info = _info(col[7])
                sig = info.get("CLNSIG", "")
                if not (sig.startswith("Pathogenic") or sig.startswith("Likely_pathogenic")):
                    continue
                rev = info.get("CLNREVSTAT", "")
                batch.append((col[0].replace("chr", ""), int(col[1]), col[3], col[4], col[2], sig[:64], rev[:80], _STARS.get(rev, 0),
                              (info.get("GENEINFO", "").split("|")[0].split(":")[0] or None),
                              (info.get("MC", "").split("|")[-1] or None)[:64] if info.get("MC") else None,
                              info.get("CLNDN", "").replace("_", " ").split("|")[:4], info.get("CLNDISDB", ""), ver))
                if len(batch) >= BATCH:
                    await flush()
            await flush()
        async with c.transaction():
            await c.execute("INSERT INTO ref_clinvar (chrom,pos,ref,alt,variation_id,clnsig,review_status,stars,gene_symbol,consequence,condition_names,disease_db,source_version)"
                            " SELECT DISTINCT ON (chrom,pos,ref,alt) chrom,pos,ref,alt,variation_id,clnsig,review_status,stars,gene_symbol,consequence,condition_names,disease_db,source_version FROM stage_clinvar"
                            " ON CONFLICT (vkey) DO UPDATE SET variation_id=EXCLUDED.variation_id, clnsig=EXCLUDED.clnsig,"
                            " review_status=EXCLUDED.review_status, stars=EXCLUDED.stars, gene_symbol=EXCLUDED.gene_symbol,"
                            " consequence=EXCLUDED.consequence, condition_names=EXCLUDED.condition_names, disease_db=EXCLUDED.disease_db,"
                            " source_version=EXCLUDED.source_version")
            await c.execute("DELETE FROM ref_clinvar WHERE source_version <> $1", ver)
        await c.execute("DROP TABLE stage_clinvar")
        total = await c.fetchval("SELECT count(*) FROM ref_clinvar")
        log.info("[load] ref_clinvar %s: %d staged, %d rows in %.0fs", ver, n, total, time.time() - t0)
        return {"table": "ref_clinvar", "version": ver, "rows": total, "skipped": False}


async def load_hgnc(force: bool = False) -> dict:
    from ..gene.hgnc import Hgnc
    src = ontology_dir() / "hgnc_complete_set.txt"
    ver = _version("hgnc", src)
    pool = await get_pool()
    async with pool.acquire() as c:
        if not force and await _have_version(c, "ref_hgnc", ver):
            return {"table": "ref_hgnc", "version": ver, "skipped": True}
        h = Hgnc(src)
        rows = [(s, d["hgnc_id"], d["name"], d["location"], ver) for s, d in h.symbol.items()]
        aliases = [(a, s, k) for s, d in h.symbol.items() for k, lst in (("alias", d["alias"]), ("prev", d["prev"])) for a in lst]
        async with c.transaction():
            await c.executemany("INSERT INTO ref_hgnc (symbol,hgnc_id,name,location,source_version) VALUES ($1,$2,$3,$4,$5)"
                                " ON CONFLICT (symbol) DO UPDATE SET hgnc_id=EXCLUDED.hgnc_id, name=EXCLUDED.name, location=EXCLUDED.location, source_version=EXCLUDED.source_version", rows)
            await c.executemany("INSERT INTO ref_hgnc_alias (alias,symbol,kind) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", aliases)
        return {"table": "ref_hgnc", "version": ver, "rows": len(rows), "aliases": len(aliases), "skipped": False}


async def load_gene_hpo(force: bool = False) -> dict:
    src = ontology_dir() / "genes_to_phenotype.txt"
    ver = _version("genes_to_phenotype", src)
    pool = await get_pool()
    async with pool.acquire() as c:
        if not force and await _have_version(c, "ref_gene_hpo", ver):
            return {"table": "ref_gene_hpo", "version": ver, "skipped": True}
        rows = set()
        with src.open(encoding="utf-8") as fh:
            head = {h: i for i, h in enumerate(fh.readline().rstrip("\n").split("\t"))}
            for line in fh:
                col = line.rstrip("\n").split("\t")
                if len(col) >= len(head):
                    rows.add((col[head["gene_symbol"]], col[head["hpo_id"]], col[head["disease_id"]] or "", ver))
        rows = sorted(rows)
        async with c.transaction():
            for i in range(0, len(rows), BATCH):
                await c.executemany("INSERT INTO ref_gene_hpo (gene_symbol,hpo_id,disease_id,source_version) VALUES ($1,$2,$3,$4)"
                                    " ON CONFLICT (gene_symbol,hpo_id,disease_id) DO UPDATE SET source_version=EXCLUDED.source_version", rows[i:i + BATCH])
        return {"table": "ref_gene_hpo", "version": ver, "rows": len(rows), "skipped": False}


async def load_orpha(force: bool = False) -> dict:
    from mirobody.lexical import normalize
    from ..disease.orphanet import load_orpha as _bundle
    src = ontology_dir() / "en_product1.xml"
    ver = _version("orphanet", src)
    pool = await get_pool()
    async with pool.acquire() as c:
        if not force and await _have_version(c, "ref_orpha_disorder", ver):
            return {"table": "ref_orpha_disorder", "version": ver, "skipped": True}
        b = _bundle()
        dis = [(code, b.name[code], b.dtype.get(code), b.icd10.get(code, []), b.omim.get(code, []), ver) for code in b.name]
        names = {(normalize(b.name[code]), code, "label") for code in b.name}
        names |= {(normalize(s), code, "synonym") for code, ss in b.synonyms.items() for s in ss if s}
        genes = [(code, g["symbol"], g.get("hgnc") or None, (g.get("assoc") or "")[:80]) for code, gs in b.genes.items() for g in gs]
        hpos = [(code, h, f) for code, rows in b.annots.items() for h, f in rows]
        async with c.transaction():
            await c.executemany("INSERT INTO ref_orpha_disorder (orpha_code,name,disorder_type,icd10,omim,source_version) VALUES ($1,$2,$3,$4,$5,$6)"
                                " ON CONFLICT (orpha_code) DO UPDATE SET name=EXCLUDED.name, disorder_type=EXCLUDED.disorder_type, icd10=EXCLUDED.icd10, omim=EXCLUDED.omim, source_version=EXCLUDED.source_version", dis)
            await c.executemany("INSERT INTO ref_orpha_name (name_key,orpha_code,kind) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", sorted(names))
            await c.executemany("INSERT INTO ref_orpha_gene (orpha_code,gene_symbol,hgnc_id,association) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING", genes)
            await c.executemany("INSERT INTO ref_orpha_hpo (orpha_code,hpo_id,frequency) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", hpos)
        return {"table": "ref_orpha_*", "version": ver, "disorders": len(dis), "names": len(names), "genes": len(genes), "hpo": len(hpos), "skipped": False}


LOADERS = {"clinvar": load_clinvar, "hgnc": load_hgnc, "gene_hpo": load_gene_hpo, "orpha": load_orpha}


async def load(which: list[str], force: bool = False, schema: bool = True) -> list[dict]:
    if schema:
        await run_schema()
    out = []
    for w in (LOADERS if "all" in which else which):
        out.append(await LOADERS[w](force=force))
        log.info("[load] %s", out[-1])
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="load rare-disease reference tables into Postgres")
    ap.add_argument("which", nargs="*", default=["all"], choices=[*LOADERS, "all"])
    ap.add_argument("--force", action="store_true", help="reload even if this source_version is present")
    ap.add_argument("--no-schema", action="store_true", help="do not replay the DDL files first")
    ap.add_argument("-v", "--verbose", type=int, default=None)
    a = ap.parse_args(argv)
    setup_logging(a.verbose)
    for r in asyncio.run(load(a.which, force=a.force, schema=not a.no_schema)):
        print(r)


if __name__ == "__main__":
    main()
