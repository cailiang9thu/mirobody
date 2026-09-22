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
    async def phenotypes(self, user_id: str, negated: bool | None = None, subject: str | None = None) -> list[dict]: ...
    async def disease_codes(self, user_id: str) -> list[dict]: ...
    async def variants(self, user_id: str, genes: Sequence[str] = (), chrom: str | None = None,
                       start: int | None = None, end: int | None = None, limit: int = 200) -> list[dict]: ...
    async def signals(self, user_id: str, modality: str | None = None) -> list[dict]: ...
    async def pedigree_of(self, user_id: str) -> dict | None: ...
    async def is_analysis_only(self, user_id: str) -> bool: ...
    async def consents(self, user_id: str) -> list[dict]: ...
    async def circle_access(self, caller_id: str, subject_id: str) -> int | None: ...   # None = no shared circle
    async def circle_members(self, user_id: str) -> list[dict]: ...
    async def samples_of(self, user_id: str) -> list[dict]: ...
    async def set_inheritance(self, variant_id: int, inheritance: str, is_de_novo: bool | None, parent_gt: dict) -> None: ...
    # writers (ingest)
    async def add_phenotypes(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_sample(self, row: Mapping[str, Any]) -> int: ...
    async def set_sample_status(self, sample_id: int, status: str, variant_count: int | None = None) -> None: ...
    async def written_chroms(self, sample_id: int) -> set[str]: ...
    async def add_variants(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_annotations(self, rows: Sequence[Mapping[str, Any]]) -> int: ...
    async def add_signal(self, row: Mapping[str, Any]) -> int: ...
    async def upsert_pedigree(self, family: Mapping[str, Any], members: Sequence[Mapping[str, Any]], owner_id: str | None = None) -> int: ...


class MemoryRepo:
    def __init__(self) -> None:
        self.t: dict[str, list[dict]] = {k: [] for k in ("th_phenotype", "th_disease_code", "th_sequencing_sample",
                                                         "th_variant", "th_variant_annotation", "th_signal_object",
                                                         "th_pedigree", "th_pedigree_member", "th_consent",
                                                         "th_phenotype_review", "th_files",
                                                         "care_circle")}   # care_circle: {operator, subject, access}

    def _ins(self, table: str, row: Mapping[str, Any]) -> int:
        r = dict(row)
        r.setdefault("id", len(self.t[table]) + 1)
        self.t[table].append(r)
        return r["id"]

    async def phenotypes(self, user_id, negated=None, subject=None):
        return [r for r in self.t["th_phenotype"] if r["user_id"] == user_id and not r.get("deleted")
                and (negated is None or bool(r.get("negated")) == negated)
                and (subject is None or (r.get("subject") or "proband") == subject)]

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

    async def add_consent(self, row):
        return self._ins("th_consent", {"revoked_at": None, "cross_border_allowed": False, **row})

    async def circle_access(self, caller_id, subject_id):
        acc = [r["access"] for r in self.t["care_circle"] if r["operator"] == caller_id and r["subject"] == subject_id]
        return max(acc) if acc else None

    async def add_review(self, row):
        return self._ins("th_phenotype_review", {"resolved_hpo": None, "resolved_at": None, **row})

    async def resolve_review(self, review_id, hpo_id, by):
        for r in self.t["th_phenotype_review"]:
            if r["id"] == review_id:
                r["resolved_hpo"], r["resolved_by"], r["resolved_at"] = hpo_id, by, "now"
                return r
        return None

    async def add_disease_code(self, row):
        return self._ins("th_disease_code", row)

    async def file_id_by_key(self, user_id, file_key):
        for r in self.t["th_files"]:
            if r["user_id"] == user_id and r["file_key"] == file_key:
                return r["id"]
        return None

    async def phenotypes_exist_for_hash(self, user_id, content_hash):
        return any(r["user_id"] == user_id and r.get("content_hash") == content_hash for r in self.t["th_phenotype"])

    async def circle_members(self, user_id):
        return [{"user_id": r["subject"], "nickname": r.get("nickname"), "name": r.get("name"), "email": r.get("email")}
                for r in self.t["care_circle"] if r["operator"] == user_id]

    async def samples_of(self, user_id):
        return [r for r in self.t["th_sequencing_sample"] if r["user_id"] == user_id]

    async def set_inheritance(self, variant_id, inheritance, is_de_novo, parent_gt):
        for v in self.t["th_variant"]:
            if v["id"] == variant_id:
                v["inheritance"] = inheritance
                v["is_de_novo"] = is_de_novo
                v["parent_gt"] = parent_gt

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

    async def upsert_pedigree(self, family, members, owner_id=None):
        fam = next((f for f in self.t["th_pedigree"] if f["family_id"] == family["family_id"] and f.get("owner_user_id") == owner_id), None)
        pid = fam["id"] if fam else self._ins("th_pedigree", {**family, "owner_user_id": owner_id})
        have = {m["individual_id"]: m for m in self.t["th_pedigree_member"] if m["pedigree_id"] == pid}
        for m in members:
            if m["individual_id"] not in have:
                self._ins("th_pedigree_member", {**m, "pedigree_id": pid})
            elif m.get("user_id") and not have[m["individual_id"]].get("user_id"):
                have[m["individual_id"]]["user_id"] = m["user_id"]          # a newly linked account
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

    async def phenotypes(self, user_id, negated=None, subject=None):
        sql = ("SELECT id, hpo_id, hpo_label, onset_hpo_id, severity_hpo_id, frequency_hpo_id, negated, subject, subject_role,"
               " source, source_text, confidence, asserted_at, file_id, section FROM th_phenotype"
               " WHERE user_id = :user_id AND NOT deleted")
        if negated is not None:
            sql += " AND negated = :negated"
        if subject is not None:
            sql += " AND subject = :subject"
        return await self._q(sql + " ORDER BY asserted_at NULLS LAST, id", {"user_id": user_id, "negated": negated, "subject": subject})

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

    async def add_review(self, row):
        rows = await self._q("INSERT INTO th_phenotype_review (user_id, file_id, kind, source_text, candidates, section, subject, negated)"
                             " VALUES (:user_id, :file_id, :kind, :source_text, :candidates, :section, :subject, :negated) RETURNING id",
                             {"file_id": None, "candidates": [], "section": None, "subject": None, "negated": None, **row})
        return int(rows[0]["id"])

    async def resolve_review(self, review_id, hpo_id, by):
        rows = await self._q("UPDATE th_phenotype_review SET resolved_hpo = :h, resolved_by = :b, resolved_at = now()"
                             " WHERE id = :id RETURNING id, user_id, file_id, source_text, section, subject, negated", {"h": hpo_id, "b": by, "id": review_id})
        return dict(rows[0]) if rows else None

    async def add_disease_code(self, row):
        rows = await self._q("INSERT INTO th_disease_code (user_id, system, code, label, status, source, source_text, confidence, file_id)"
                             " VALUES (:user_id, :system, :code, :label, :status, :source, :source_text, :confidence, :file_id) RETURNING id",
                             {"source_text": None, "confidence": None, "file_id": None, **row})
        return int(rows[0]["id"])

    async def file_id_by_key(self, user_id, file_key):
        rows = await self._q("SELECT id FROM th_files WHERE user_id = :u AND file_key = :k AND is_del = false ORDER BY id DESC LIMIT 1",
                             {"u": user_id, "k": file_key})
        return int(rows[0]["id"]) if rows else None

    async def phenotypes_exist_for_hash(self, user_id, content_hash):
        rows = await self._q("SELECT 1 FROM th_phenotype p JOIN th_files f ON f.id = p.file_id"
                             " WHERE p.user_id = :u AND f.content_hash = :h AND NOT p.deleted LIMIT 1", {"u": user_id, "h": content_hash})
        return bool(rows)

    async def circle_access(self, caller_id, subject_id):
        """The subject's `health_access` toward the caller in any shared, accepted circle —
        the same MAX/GROUP BY rule as `care_circle.accepted_membership`, run on this
        repo's own pool (the host app's global config is not required)."""
        from mirobody.user.care_circle import STATUS_ACCEPTED
        try:
            op, sub = int(caller_id), int(subject_id)
        except (TypeError, ValueError):
            return None
        rows = await self._q(
            "SELECT MAX(subject.health_access) AS health_access"
            "  FROM care_circle_members me"
            "  JOIN care_circle_members subject ON subject.care_circle_id = me.care_circle_id"
            " WHERE me.user_id = :op AND subject.user_id = :sub"
            "   AND me.status = :acc AND subject.status = :acc"
            "   AND me.deleted_at IS NULL AND subject.deleted_at IS NULL"
            " GROUP BY subject.user_id", {"op": op, "sub": sub, "acc": STATUS_ACCEPTED})
        return None if not rows else int(rows[0]["health_access"] or 0)

    async def circle_members(self, user_id):
        """Accepted members of every circle the user belongs to (same SQL as
        mirobody.user.care_circle.circle_members, on this repo's own pool so it
        does not depend on the host app's global config)."""
        from mirobody.user.care_circle import STATUS_ACCEPTED
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return []
        rows = await self._q(
            "SELECT m.user_id AS user_id, m.status AS status, m.nickname AS nickname, u.name AS name, u.email AS email"
            "  FROM care_circle_members mine"
            "  JOIN care_circles c ON c.id = mine.care_circle_id AND c.deleted_at IS NULL"
            "  JOIN care_circle_members m ON m.care_circle_id = c.id AND m.deleted_at IS NULL"
            "  LEFT JOIN health_app_user u ON u.id = m.user_id"
            " WHERE mine.user_id = :u AND mine.deleted_at IS NULL AND mine.status = :acc AND m.status = :acc",
            {"u": uid, "acc": STATUS_ACCEPTED})
        return [{"user_id": r["user_id"], "nickname": r["nickname"], "name": r["name"], "email": r["email"]} for r in rows]

    async def samples_of(self, user_id):
        return await self._q("SELECT id, user_id, file_id, file_key, assay, reference, sample_label, status, variant_count, content_sha256"
                             " FROM th_sequencing_sample WHERE user_id = :u ORDER BY id DESC", {"u": user_id})

    async def set_inheritance(self, variant_id, inheritance, is_de_novo, parent_gt):
        await self._q("UPDATE th_variant SET inheritance = :i, is_de_novo = :d WHERE id = :v RETURNING id",
                      {"i": inheritance, "d": is_de_novo, "v": variant_id})

    async def add_consent(self, row):
        rows = await self._q("INSERT INTO th_consent (user_id, scope, granted, signed_by_user_id, relationship, layer, residency, cross_border_allowed, document_file_id)"
                             " VALUES (:user_id, :scope, :granted, :signed_by_user_id, :relationship, :layer, :residency, :cross_border_allowed, :document_file_id) RETURNING id",
                             {"relationship": "self", "layer": None, "residency": None, "cross_border_allowed": False, "document_file_id": None,
                              **{k: v for k, v in row.items() if k in ("user_id", "scope", "granted", "signed_by_user_id", "relationship", "layer", "residency", "cross_border_allowed", "document_file_id")}})
        return int(rows[0]["id"])

    async def add_phenotypes(self, rows):
        n = 0
        for r in rows:
            await self._q("INSERT INTO th_phenotype (user_id, hpo_id, hpo_label, negated, subject, subject_role, source, source_text, confidence, asserted_at, file_id, section)"
                          " VALUES (:user_id, :hpo_id, :hpo_label, :negated, :subject, :subject_role, :source, :source_text, :confidence, :asserted_at, :file_id, :section) RETURNING id",
                          {"negated": False, "subject": "proband", "subject_role": None, "section": r.get("section"),
                           **{k: r.get(k) for k in ("user_id", "hpo_id", "hpo_label", "source", "source_text", "confidence", "asserted_at", "file_id")},
                           **({"negated": r["negated"]} if "negated" in r else {}), **({"subject": r["subject"]} if r.get("subject") else {}),
                           **({"subject_role": r["subject_role"]} if r.get("subject_role") else {})})
            n += 1
        return n

    async def add_sample(self, row):
        rows = await self._q("INSERT INTO th_sequencing_sample (user_id, file_id, file_key, assay, reference, sample_label, caller, status, content_sha256)"
                             " VALUES (:user_id, :file_id, :file_key, :assay, :reference, :sample_label, :caller, 'pending', :content_sha256) RETURNING id",
                             {k: row.get(k) for k in ("user_id", "file_id", "file_key", "assay", "reference", "sample_label", "caller", "content_sha256")})
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

    async def upsert_pedigree(self, family, members, owner_id=None):
        rows = await self._q("INSERT INTO th_pedigree (family_id, label, owner_user_id) VALUES (:family_id, :label, :owner)"
                             " ON CONFLICT (COALESCE(owner_user_id, ''), family_id) DO UPDATE SET label = EXCLUDED.label RETURNING id",
                             {"family_id": family["family_id"], "label": family.get("label"), "owner": owner_id})
        pid = int(rows[0]["id"])
        for m in members:
            await self._q("INSERT INTO th_pedigree_member (pedigree_id, user_id, individual_id, paternal_id, maternal_id, sex, affected, is_proband, analysis_only)"
                          " VALUES (:pid, :user_id, :individual_id, :paternal_id, :maternal_id, :sex, :affected, :is_proband, :analysis_only)"
                          " ON CONFLICT (pedigree_id, individual_id) DO UPDATE SET user_id = COALESCE(th_pedigree_member.user_id, EXCLUDED.user_id) RETURNING id",
                          {"pid": pid, "is_proband": False, "analysis_only": False, **{k: m.get(k) for k in ("user_id", "individual_id", "paternal_id", "maternal_id", "sex", "affected")},
                           **{k: m[k] for k in ("is_proband", "analysis_only") if k in m}})
        return pid
