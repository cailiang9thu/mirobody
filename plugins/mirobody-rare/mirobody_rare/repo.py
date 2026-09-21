"""Storage for the four rare-disease tables, behind one small interface.

`PgRepo` speaks the main package's `execute_query` (named binds, the way
`agent/tools/genetic_service.py` reads); `MemoryRepo` is the same interface over dicts so
the gate, the tools and the ingest jobs are testable without Postgres. Rows are
dict-shaped exactly like the DDL in `mirobody/schema/32_…35_*.sql`."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

log = logging.getLogger(__name__)


class RareRepo(Protocol):
    async def phenotypes(self, user_id: str, negated: bool | None = None) -> list[dict]: ...
    async def disease_codes(self, user_id: str) -> list[dict]: ...
    async def variants(self, user_id: str, genes: Sequence[str] = (), chrom: str | None = None,
                       start: int | None = None, end: int | None = None, limit: int = 200) -> list[dict]: ...
    async def signals(self, user_id: str, modality: str | None = None) -> list[dict]: ...
    async def pedigree_of(self, user_id: str) -> dict | None: ...
    async def is_analysis_only(self, user_id: str) -> bool: ...
    async def consents(self, user_id: str) -> list[dict]: ...
    # writers (ingest)
    async def add_phenotypes(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_sample(self, row: Mapping[str, Any]) -> int: ...
    async def set_sample_status(self, sample_id: int, status: str, variant_count: int | None = None) -> None: ...
    async def written_chroms(self, sample_id: int) -> set[str]: ...
    async def add_variants(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_annotations(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_signal(self, row: Mapping[str, Any]) -> int: ...
    async def upsert_pedigree(self, family: Mapping[str, Any], members: Sequence[Mapping[str, Any]]) -> int: ...


class MemoryRepo:
    def __init__(self) -> None:
        self.t: dict[str, list[dict]] = {k: [] for k in ("th_phenotype", "th_disease_code", "th_sequencing_sample",
                                                         "th_variant", "th_variant_annotation", "th_signal_object",
                                                         "th_pedigree", "th_pedigree_member", "th_consent")}

    def _ins(self, table: str, row: Mapping[str, Any]) -> int:
        r = dict(row)
        r.setdefault("id", len(self.t[table]) + 1)
        self.t[table].append(r)
        return r["id"]

    async def phenotypes(self, user_id, negated=None):
        return [r for r in self.t["th_phenotype"] if r["user_id"] == user_id and not r.get("deleted")
                and (negated is None or bool(r.get("negated")) == negated)]

    async def disease_codes(self, user_id):
        return [r for r in self.t["th_disease_code"] if r["user_id"] == user_id and not r.get("deleted")]

    async def variants(self, user_id, genes=(), chrom=None, start=None, end=None, limit=200):
        ann = {}
        for a in self.t["th_variant_annotation"]:
            ann.setdefault(a["variant_id"], []).append(a)
        out = []
        for v in self.t["th_variant"]:
            if v["user_id"] != user_id:
                continue
            if genes and v.get("gene_symbol") not in genes:
                continue
            if chrom and v["chrom"] != chrom:
                continue
            if start is not None and v["pos"] < start or end is not None and v["pos"] > end:
                continue
            out.append({**v, "annotations": ann.get(v["id"], [])})
        return out[:limit]

    async def signals(self, user_id, modality=None):
        return [r for r in self.t["th_signal_object"] if r["user_id"] == user_id
                and (modality is None or r["modality"] == modality)]

    async def pedigree_of(self, user_id):
        for m in self.t["th_pedigree_member"]:
            if m.get("user_id") == user_id:
                fam = next(f for f in self.t["th_pedigree"] if f["id"] == m["pedigree_id"])
                return {**fam, "members": [x for x in self.t["th_pedigree_member"] if x["pedigree_id"] == fam["id"]]}
        return None

    async def is_analysis_only(self, user_id):
        return any(m.get("user_id") == user_id and m.get("analysis_only") for m in self.t["th_pedigree_member"])

    async def consents(self, user_id):
        return [r for r in self.t["th_consent"] if r["user_id"] == user_id]

    async def add_phenotypes(self, rows):
        return sum(1 for r in rows if self._ins("th_phenotype", r))

    async def add_sample(self, row):
        return self._ins("th_sequencing_sample", row)

    async def sample_by_hash(self, user_id, sha256):
        for r in self.t["th_sequencing_sample"]:
            if r["user_id"] == user_id and r.get("content_sha256") == sha256 and r.get("status") == "ready":
                return r
        return None

    async def set_sample_status(self, sample_id, status, variant_count=None):
        for r in self.t["th_sequencing_sample"]:
            if r["id"] == sample_id:
                r["status"] = status
                if variant_count is not None:
                    r["variant_count"] = variant_count

    async def written_chroms(self, sample_id):
        return {v["chrom"] for v in self.t["th_variant"] if v["sample_id"] == sample_id}

    async def add_variants(self, rows):
        n = 0
        seen = {(v["sample_id"], v["chrom"], v["pos"], v["ref"], v["alt"]) for v in self.t["th_variant"]}
        for r in rows:
            k = (r["sample_id"], r["chrom"], r["pos"], r["ref"], r["alt"])
            if k in seen:
                continue                                   # uq_th_variant_call: ON CONFLICT DO NOTHING
            seen.add(k)
            self._ins("th_variant", r)
            n += 1
        return n

    async def variant_id(self, sample_id, chrom, pos, ref, alt):
        for v in self.t["th_variant"]:
            if (v["sample_id"], v["chrom"], v["pos"], v["ref"], v["alt"]) == (sample_id, chrom, pos, ref, alt):
                return v["id"]
        return None

    async def add_annotations(self, rows):
        n = 0
        seen = {(a["variant_id"], a["source"], a["source_version"]) for a in self.t["th_variant_annotation"]}
        for r in rows:
            k = (r["variant_id"], r["source"], r["source_version"])
            if k in seen:
                continue
            seen.add(k)
            self._ins("th_variant_annotation", r)
            n += 1
        return n

    async def add_signal(self, row):
        return self._ins("th_signal_object", row)

    async def upsert_pedigree(self, family, members):
        fam = next((f for f in self.t["th_pedigree"] if f["family_id"] == family["family_id"]), None)
        pid = fam["id"] if fam else self._ins("th_pedigree", family)
        have = {m["individual_id"] for m in self.t["th_pedigree_member"] if m["pedigree_id"] == pid}
        for m in members:
            if m["individual_id"] not in have:
                self._ins("th_pedigree_member", {**m, "pedigree_id": pid})
        return pid


def _bind(sql: str, params: Mapping[str, Any]) -> tuple[str, list]:
    """`:name` binds → asyncpg `$n` (names are ours, from literals; values are always bound)."""
    import re
    order: list[str] = []

    def sub(m):
        n = m.group(1)
        if n not in order:
            order.append(n)
        return f"${order.index(n) + 1}"
    q = re.sub(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)", sub, sql)
    return q, [params.get(n) for n in order]


class PgRepo:
    """The same interface over Postgres through the plugin's asyncpg pool (`reference.db`),
    schema `mirobody_rare`. Every write is replay-safe (`ON CONFLICT DO NOTHING`)."""

    def __init__(self, pool=None) -> None:
        self._pool = pool

    async def _p(self):
        if self._pool is None:
            from .reference.db import get_pool
            self._pool = await get_pool()
        return self._pool

    async def _q(self, sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        q, args = _bind(sql, params)
        pool = await self._p()
        return [dict(r) for r in await pool.fetch(q, *args)]

    async def phenotypes(self, user_id, negated=None):
        sql = ("SELECT id, hpo_id, hpo_label, onset_hpo_id, severity_hpo_id, frequency_hpo_id, negated, subject,"
               " source, source_text, confidence, asserted_at, file_id FROM th_phenotype"
               " WHERE user_id = :user_id AND NOT deleted")
        if negated is not None:
            sql += " AND negated = :negated"
        return await self._q(sql + " ORDER BY asserted_at NULLS LAST, id", {"user_id": user_id, "negated": negated})

    async def disease_codes(self, user_id):
        return await self._q("SELECT id, system, code, label, status, source, confidence FROM th_disease_code"
                             " WHERE user_id = :user_id AND NOT deleted ORDER BY id", {"user_id": user_id})

    async def variants(self, user_id, genes=(), chrom=None, start=None, end=None, limit=200):
        sql = ("SELECT v.id, v.sample_id, v.chrom, v.pos, v.ref, v.alt, v.genotype, v.zygosity, v.depth, v.gq, v.filter,"
               " v.gene_symbol, v.hgvs_c, v.hgvs_p, v.consequence, v.is_de_novo, v.inheritance"
               " FROM th_variant v WHERE v.user_id = :user_id")
        p: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if genes:
            sql += " AND v.gene_symbol = ANY(:genes)"
            p["genes"] = list(genes)
        if chrom:
            sql += " AND v.chrom = :chrom"
            p["chrom"] = chrom
        if start is not None:
            sql += " AND v.pos >= :start"
            p["start"] = start
        if end is not None:
            sql += " AND v.pos <= :end"
            p["end"] = end
        rows = await self._q(sql + " ORDER BY v.chrom, v.pos LIMIT :limit", p)
        if not rows:
            return []
        ann = await self._q("SELECT variant_id, source, source_version, clinical_significance, review_status, condition_names,"
                            " af_global, af_popmax, acmg_class FROM th_variant_annotation WHERE variant_id = ANY(:ids)",
                            {"ids": [r["id"] for r in rows]})
        by: dict[int, list] = {}
        for a in ann:
            by.setdefault(a.pop("variant_id"), []).append(a)
        for r in rows:
            r["annotations"] = by.get(r["id"], [])
        return rows

    async def signals(self, user_id, modality=None):
        sql = ("SELECT id, file_id, modality, format, body_part, study_date, series_desc, instance_count, duration_sec,"
               " channel_count, report_summary, residency, exportable, deid_status FROM th_signal_object WHERE user_id = :user_id")
        if modality:
            sql += " AND modality = :modality"
        return await self._q(sql + " ORDER BY study_date NULLS LAST, id", {"user_id": user_id, "modality": modality})

    async def pedigree_of(self, user_id):
        rows = await self._q("SELECT p.id, p.family_id, p.label FROM th_pedigree p JOIN th_pedigree_member m ON m.pedigree_id = p.id"
                             " WHERE m.user_id = :user_id LIMIT 1", {"user_id": user_id})
        if not rows:
            return None
        fam = dict(rows[0])
        fam["members"] = await self._q("SELECT id, pedigree_id, user_id, individual_id, paternal_id, maternal_id, sex, affected,"
                                       " is_proband, analysis_only FROM th_pedigree_member WHERE pedigree_id = :pid ORDER BY id", {"pid": fam["id"]})
        return fam

    async def is_analysis_only(self, user_id):
        return bool(await self._q("SELECT 1 FROM th_pedigree_member WHERE user_id = :user_id AND analysis_only LIMIT 1", {"user_id": user_id}))

    async def consents(self, user_id):
        return await self._q("SELECT id, scope, granted, layer, residency, cross_border_allowed, effective_at, revoked_at"
                             " FROM th_consent WHERE user_id = :user_id ORDER BY effective_at DESC", {"user_id": user_id})

    async def add_phenotypes(self, rows):
        n = 0
        for r in rows:
            await self._q("INSERT INTO th_phenotype (user_id, hpo_id, hpo_label, negated, subject, source, source_text, confidence, asserted_at, file_id)"
                          " VALUES (:user_id, :hpo_id, :hpo_label, :negated, :subject, :source, :source_text, :confidence, :asserted_at, :file_id) RETURNING id",
                          {"negated": False, "subject": "proband", **{k: r.get(k) for k in ("user_id", "hpo_id", "hpo_label", "source", "source_text", "confidence", "asserted_at", "file_id")},
                           **({"negated": r["negated"]} if "negated" in r else {}), **({"subject": r["subject"]} if r.get("subject") else {})})
            n += 1
        return n

    async def add_sample(self, row):
        rows = await self._q("INSERT INTO th_sequencing_sample (user_id, file_id, assay, reference, sample_label, caller, status, content_sha256)"
                             " VALUES (:user_id, :file_id, :assay, :reference, :sample_label, :caller, 'pending', :content_sha256) RETURNING id",
                             {k: row.get(k) for k in ("user_id", "file_id", "assay", "reference", "sample_label", "caller", "content_sha256")})
        return int(rows[0]["id"])

    async def sample_by_hash(self, user_id, sha256):
        rows = await self._q("SELECT id, status, variant_count FROM th_sequencing_sample WHERE user_id = :u AND content_sha256 = :h AND status = 'ready' LIMIT 1",
                             {"u": user_id, "h": sha256})
        return dict(rows[0]) if rows else None

    async def set_sample_status(self, sample_id, status, variant_count=None):
        await self._q("UPDATE th_sequencing_sample SET status = :status, variant_count = COALESCE(:n, variant_count), update_time = now()"
                      " WHERE id = :id RETURNING id", {"status": status, "n": variant_count, "id": sample_id})

    async def written_chroms(self, sample_id):
        return {r["chrom"] for r in await self._q("SELECT DISTINCT chrom FROM th_variant WHERE sample_id = :id", {"id": sample_id})}

    async def add_variants(self, rows):
        n = 0
        for r in rows:
            got = await self._q("INSERT INTO th_variant (sample_id, user_id, chrom, pos, ref, alt, genotype, zygosity, depth, gq, filter,"
                                " gene_symbol, consequence, is_de_novo, inheritance)"
                                " VALUES (:sample_id, :user_id, :chrom, :pos, :ref, :alt, :genotype, :zygosity, :depth, :gq, :filter,"
                                " :gene_symbol, :consequence, :is_de_novo, :inheritance) ON CONFLICT DO NOTHING RETURNING id",
                                {k: r.get(k) for k in ("sample_id", "user_id", "chrom", "pos", "ref", "alt", "genotype", "zygosity", "depth", "gq",
                                                       "filter", "gene_symbol", "consequence", "is_de_novo", "inheritance")})
            n += len(got)
        return n

    async def variant_id(self, sample_id, chrom, pos, ref, alt):
        rows = await self._q("SELECT id FROM th_variant WHERE sample_id = :s AND chrom = :c AND pos = :p AND ref = :r AND alt = :a",
                             {"s": sample_id, "c": chrom, "p": pos, "r": ref, "a": alt})
        return int(rows[0]["id"]) if rows else None

    async def add_annotations(self, rows):
        n = 0
        for r in rows:
            got = await self._q("INSERT INTO th_variant_annotation (variant_id, source, source_version, clinical_significance, review_status, condition_names,"
                                " af_global, af_popmax, popmax_pop, allele_count)"
                                " VALUES (:variant_id, :source, :source_version, :clinical_significance, :review_status, :condition_names,"
                                " :af_global, :af_popmax, :popmax_pop, :allele_count)"
                                " ON CONFLICT DO NOTHING RETURNING id",
                                {k: r.get(k) for k in ("variant_id", "source", "source_version", "clinical_significance", "review_status", "condition_names",
                                                       "af_global", "af_popmax", "popmax_pop", "allele_count")})
            n += len(got)
        return n

    async def add_signal(self, row):
        from datetime import date
        row = dict(row)
        if isinstance(row.get("study_date"), str):                # asyncpg wants a date object for a DATE column
            row["study_date"] = date.fromisoformat(row["study_date"])
        rows = await self._q("INSERT INTO th_signal_object (user_id, file_id, modality, format, body_part, study_date, series_desc, instance_count,"
                             " residency, exportable, deid_status) VALUES (:user_id, :file_id, :modality, :format, :body_part, :study_date,"
                             " :series_desc, :instance_count, :residency, :exportable, :deid_status) RETURNING id",
                             {"residency": "CN", "exportable": False, **{k: row.get(k) for k in ("user_id", "file_id", "modality", "format", "body_part", "study_date",
                                                                                                    "series_desc", "instance_count", "deid_status")}})
        return int(rows[0]["id"])

    async def upsert_pedigree(self, family, members):
        rows = await self._q("INSERT INTO th_pedigree (family_id, label) VALUES (:family_id, :label) ON CONFLICT (family_id) DO UPDATE SET label = EXCLUDED.label RETURNING id",
                             {"family_id": family["family_id"], "label": family.get("label")})
        pid = int(rows[0]["id"])
        for m in members:
            await self._q("INSERT INTO th_pedigree_member (pedigree_id, user_id, individual_id, paternal_id, maternal_id, sex, affected, is_proband, analysis_only)"
                          " VALUES (:pid, :user_id, :individual_id, :paternal_id, :maternal_id, :sex, :affected, :is_proband, :analysis_only)"
                          " ON CONFLICT (pedigree_id, individual_id) DO NOTHING RETURNING id",
                          {"pid": pid, "is_proband": False, "analysis_only": False, **{k: m.get(k) for k in ("user_id", "individual_id", "paternal_id", "maternal_id", "sex", "affected")},
                           **{k: m[k] for k in ("is_proband", "analysis_only") if k in m}})
        return pid
