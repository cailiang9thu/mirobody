"""MCP tools for layer 1 (plan §8): registered through the `mirobody.tools` entry point.
Every answer carries the no-guess caveat (`genetic_service._ABSENCE_NOTE` lineage)."""

from __future__ import annotations

from .coding import NO_GUESS_NOTE, code_text
from .disease import get_adapter as get_disease
from .hpo import get_adapter as get_hpo

_ABSENCE_NOTE = "未编码 ≠ 阴性:词表没命中的症状仍可能存在。否定断言(polarity=absent)是一等信息,与缺行不同。"


class RareCodingService:
    __tools__ = ("resolve_hpo", "code_phenotypes", "rank_rare_diseases")

    async def resolve_hpo(self, term: str) -> dict:
        """Map one phenotype phrase (zh or en) to an HPO term, offline.

        Returns the term id, its label, how it was matched and up to five
        candidates; `resolved=false` means no vocabulary hit, not "no phenotype".
        """
        r = get_hpo().resolve(term)
        return {"term": term, "hpo_id": r.hpo_id or None, "label": r.label or None, "resolved": r.resolved,
                "ambiguous": r.ambiguous, "method": r.method, "score": r.score,
                "candidates": [{"hpo_id": c.hpo_id, "label": c.label, "kind": c.kind, "lang": c.lang} for c in r.candidates],
                "note": _ABSENCE_NOTE}

    async def code_phenotypes(self, text: str) -> dict:
        """Extract clinical assertions from free text and code each to HPO; rank ORPHA disorders.

        `text` is a case narrative (one or more lines). Each assertion keeps subject,
        polarity, onset and who asserted it; uncoded lines are listed under `abstained`.
        """
        res = code_text(text)
        d = res.to_solver()
        d["note"] = [NO_GUESS_NOTE, _ABSENCE_NOTE]
        return d

    async def rank_rare_diseases(self, present: list[str], absent: list[str] | None = None,
                                 family: list[str] | None = None, top_k: int = 5) -> dict:
        """Rank Orphanet disorders by HPO phenotype similarity (information content).

        `present`/`absent`/`family` are HPO ids. A rank is a retrieval, not a diagnosis:
        the score is a similarity, and the caveat says so.
        """
        hits = get_disease().rank(list(present), list(absent or []), list(family or []), top_k=top_k)
        return {"ranked": [{"orpha": h.orpha, "name": h.name, "score": h.score, "matched": list(h.matched),
                            "against": list(h.against), "genes": list(h.genes)} for h in hits],
                "note": "相似度排序不是诊断;基因列表来自 Orphanet 关联,未经变异证据支持。"}


# ---------------------------------------------------------------- D5 · the four query tools (plan §8)
#
# Every call passes `consent.permit` before `_run` (plan §7.2); every answer carries its §8.2
# caveat as data (`assumptions`), not documentation. Row shapes are the DDL's.
from dataclasses import asdict as _asdict

from mirobody.kernel import tools as _kt

from .consent import permit as _permit

_PHENOTYPE_NOTE = "a term absent from this table was NOT assessed; only negated=true means excluded"
_VARIANT_COVERAGE_NOTE = ("WES/panel data do not cover deep intronic, UTR, structural or repeat-expansion variants;"
                          " a negative result does not exclude a genetic cause")
_VARIANT_AF_NOTE = "af_global/af_popmax NULL = not queried, not 'absent from the population'"
_VARIANT_FILTER_NOTE = "rows are the local filter output (PASS · DP · GQ · ClinVar P/LP ≥ 1 star), not every call in the file"
_SIGNAL_NOTE = "objects whose de-identification is not 'done' are not listed; the index carries metadata only, never pixels"
_PEDIGREE_NOTE = "is_de_novo NULL / inheritance 'unknown' = no parental data at that locus, not 'inherited'"


def _env_dict(status: str, rows: list, notes: list[str], truncated: bool = False) -> dict:
    env = _kt.Envelope(_kt.STATUS_PARTIAL if truncated else status, data=rows,
                       meta=_kt.Meta(row_count=len(rows), truncated=truncated), assumptions=tuple(notes))
    return _asdict(env)


def _denied(reason: str) -> dict:
    env = _kt.Envelope(_kt.STATUS_ERROR, data=[], error_class="unrecoverable", error_kind="denied",
                       assumptions=(f"consent gate: {reason}",))
    return _asdict(env)


class RareQueryService:
    """`query_phenotype` / `query_variant` / `query_signal_index` / `query_pedigree` (plan §8.1).
    `repo` is injected for tests; the default speaks Postgres through `mirobody.utils.execute_query`."""

    __tools__ = ("query_phenotype", "query_variant", "query_signal_index", "query_pedigree", "query_family_history", "record_consent")

    def __init__(self, repo=None) -> None:
        self._repo = repo

    def _r(self):
        if self._repo is None:
            from .repo import PgRepo
            self._repo = PgRepo()
        return self._repo

    @staticmethod
    def _caller(user_info) -> str:
        return str((user_info or {}).get("user_id") or (user_info or {}).get("id") or "")

    async def _gate(self, user_info, subject_id: str, layer: str, purpose: str):
        caller = self._caller(user_info)
        subject = subject_id or caller
        d = await _permit(self._r(), caller, subject, layer, purpose)
        return caller, subject, d

    async def query_phenotype(self, user_info: dict, subject_id: str = "", negated: bool | None = None,
                              subject: str = "", purpose: str = "individual_return") -> dict:
        """Which HPO terms are recorded for this person, and which are explicitly excluded.

        `negated=false` lists present terms, `true` the excluded ones, omitted both. `subject`
        = "proband" keeps the person's own findings, "relative" the family-history sentences
        recorded in their file (with `subject_role`), omitted both. Absence from the table
        means not assessed. Disease codes (ORPHA/OMIM) ride along under `codes`.
        """
        _, subject_uid, d = await self._gate(user_info, subject_id, "phenotype", purpose)
        if not d.allowed:
            return _denied(d.reason)
        rows = await self._r().phenotypes(subject_uid, negated, subject=subject or None)
        codes = await self._r().disease_codes(subject_uid)
        out = _env_dict(_kt.STATUS_OK, rows, [_PHENOTYPE_NOTE])
        out["codes"] = codes
        return out

    async def query_variant(self, user_info: dict, subject_id: str = "", genes: list[str] | None = None,
                            chrom: str = "", start: int | None = None, end: int | None = None, limit: int = 200,
                            purpose: str = "individual_return") -> dict:
        """Filtered sequencing variants for this person in named genes or a region, with ClinVar
        annotation, zygosity and (when a trio exists) inheritance.

        Not the consumer-array tool: that is `query_genetic_data`. Frequencies are NULL until
        the gnomAD step has run.
        """
        _, subject, d = await self._gate(user_info, subject_id, "variant", purpose)
        if not d.allowed:
            return _denied(d.reason)
        rows = await self._r().variants(subject, tuple(genes or ()), chrom or None, start, end, limit + 1)
        return _env_dict(_kt.STATUS_OK, rows[:limit], [_VARIANT_COVERAGE_NOTE, _VARIANT_AF_NOTE, _VARIANT_FILTER_NOTE, _PEDIGREE_NOTE],
                         truncated=len(rows) > limit)

    async def query_signal_index(self, user_info: dict, subject_id: str = "", modality: str = "",
                                 purpose: str = "individual_return") -> dict:
        """Which imaging / EEG objects exist for this person (de-identified metadata index).

        Lists only objects whose de-identification is done; the raw file is reachable through
        its `file_id` subject to the same consent.
        """
        _, subject, d = await self._gate(user_info, subject_id, "signal", purpose)
        if not d.allowed:
            return _denied(d.reason)
        rows = [r for r in await self._r().signals(subject, modality or None) if r.get("deid_status") == "done"]
        return _env_dict(_kt.STATUS_OK, rows, [_SIGNAL_NOTE])

    async def query_family_history(self, user_info: dict, subject_id: str = "", purpose: str = "individual_return") -> dict:
        """Family history of this person as ONE table from three sources, each row labelled:
        `relative_account` (a relative's own record, read through the care circle),
        `narrative_in_proband_record` (sentences in the person's own file attributed to a
        relative), and the pedigree's affected flag. Relatives outside the caller's care circle
        or without an account are listed under `gaps`, not silently absent: not evaluated ≠
        unaffected.
        """
        _, subject, d = await self._gate(user_info, subject_id, "pedigree", purpose)
        if not d.allowed:
            return _denied(d.reason)
        repo = self._r()
        fam = await repo.pedigree_of(subject)
        rows: list[dict] = []
        gaps: list[str] = []
        members = list((fam or {}).get("members") or [])
        me = next((m for m in members if str(m.get("user_id")) == str(subject)), None)
        role_of: dict[str, str] = {}
        if me:
            for m in members:
                iid = m["individual_id"]
                if iid == me.get("paternal_id"):
                    role_of[iid] = "father"
                elif iid == me.get("maternal_id"):
                    role_of[iid] = "mother"
                elif iid != me["individual_id"] and (m.get("paternal_id"), m.get("maternal_id")) == (me.get("paternal_id"), me.get("maternal_id")) and (m.get("paternal_id") or m.get("maternal_id")):
                    role_of[iid] = "sibling"
                elif iid != me["individual_id"]:
                    role_of[iid] = "other_relative"
        for m in members:
            iid = m["individual_id"]
            if me and iid == me["individual_id"]:
                continue
            role = role_of.get(iid, "other_relative")
            if m.get("affected") == 2:
                rows.append({"member": iid, "role": role, "source": "pedigree", "finding": "affected", "hpo_id": None, "code": None})
            uid = str(m.get("user_id") or "")
            if not uid:
                gaps.append(f"{iid} ({role}): no account; only what this person's own file says about them is known")
                continue
            acc = await repo.circle_access(subject, uid)
            if acc is None or int(acc) < 1:
                gaps.append(f"{iid} ({role}): has an account but is not in a care circle sharing with you (access={acc}); their record was not read")
                continue
            for ph in await repo.phenotypes(uid, subject="proband"):
                rows.append({"member": iid, "role": role, "source": "relative_account", "hpo_id": ph.get("hpo_id"), "label": ph.get("hpo_label"),
                             "negated": bool(ph.get("negated")), "code": None})
            for dc in await repo.disease_codes(uid):
                rows.append({"member": iid, "role": role, "source": "relative_account", "hpo_id": None, "code": dc.get("code"), "label": dc.get("label"),
                             "status": dc.get("status")})
        by_role: dict[str, list[str]] = {}
        for iid, role in role_of.items():
            by_role.setdefault(role, []).append(iid)
        for ph in await repo.phenotypes(subject, subject="relative"):
            role = ph.get("subject_role") or "other_relative"
            cands = by_role.get(role, [])
            member = cands[0] if len(cands) == 1 else f"{role} (unresolved)"
            rows.append({"member": member, "role": role, "source": "narrative_in_proband_record", "hpo_id": ph.get("hpo_id"),
                         "label": ph.get("hpo_label"), "negated": bool(ph.get("negated")), "source_text": ph.get("source_text"), "code": None})
        notes = ["a relative absent from this table was NOT evaluated; only an explicit negated finding means excluded",
                 "narrative_in_proband_record rows are what this person's file says about a relative, unconfirmed by that relative",
                 "relative_account rows come from the relative's own record, read only through care-circle access"]
        out = _env_dict(_kt.STATUS_OK, rows, notes)
        out["family_id"] = (fam or {}).get("family_id")
        out["gaps"] = gaps
        return out

    async def record_consent(self, user_info: dict, scope: str, granted: bool = True, layer: str = "",
                             relationship: str = "self", residency: str = "", cross_border_allowed: bool = False,
                             document_file_id: int | None = None) -> dict:
        """Record one consent decision for the caller (plan §7.1): scope is individual_return |
        research_use | commercial_use, layer optionally narrows it to phenotype | variant |
        signal | pedigree. A guardian signing for a minor sets relationship='guardian'; the
        signed document's th_files id goes in document_file_id. Consents are never bundled:
        one call per scope.
        """
        from .consent.gate import LAYERS, PURPOSES
        caller = self._caller(user_info)
        if scope not in PURPOSES or (layer and layer not in LAYERS) or relationship not in ("self", "guardian"):
            return _asdict(_kt.Envelope(_kt.STATUS_ERROR, data=[], error_class="recoverable", error_kind="invalid_arguments",
                                        assumptions=(f"scope ∈ {PURPOSES}, layer ∈ {LAYERS} or empty, relationship self|guardian",)))
        cid = await self._r().add_consent({"user_id": caller, "scope": scope, "granted": bool(granted), "signed_by_user_id": caller,
                                           "relationship": relationship, "layer": layer or None, "residency": residency or None,
                                           "cross_border_allowed": bool(cross_border_allowed), "document_file_id": document_file_id})
        return _env_dict(_kt.STATUS_OK, [{"id": cid, "user_id": caller, "scope": scope, "granted": bool(granted), "layer": layer or None}],
                         ["a consent row is per scope and per layer; revoke by recording granted=false"])

    async def query_pedigree(self, user_info: dict, subject_id: str = "", purpose: str = "individual_return") -> dict:
        """Family structure this person belongs to, and which of their variants are de novo.

        Members flagged `analysis_only` are listed structurally but their own data is never
        returned through this tool.
        """
        _, subject, d = await self._gate(user_info, subject_id, "pedigree", purpose)
        if not d.allowed:
            return _denied(d.reason)
        fam = await self._r().pedigree_of(subject)
        if not fam:
            return _env_dict(_kt.STATUS_OK, [], [_PEDIGREE_NOTE, "no pedigree recorded for this person"])
        members = [{k: m.get(k) for k in ("individual_id", "paternal_id", "maternal_id", "sex", "affected", "is_proband", "analysis_only")}
                   for m in fam.get("members", [])]
        de_novo = [v for v in await self._r().variants(subject, limit=1000) if v.get("inheritance") == "de_novo" or v.get("is_de_novo")]
        out = _env_dict(_kt.STATUS_OK, members, [_PEDIGREE_NOTE])
        out["family_id"] = fam.get("family_id")
        out["de_novo_variants"] = [{k: v.get(k) for k in ("chrom", "pos", "ref", "alt", "gene_symbol", "genotype")} for v in de_novo]
        return out
