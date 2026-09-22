"""D6 (plan §7.2): the single choke point every rare-disease tool passes before `_run`.

`permit(repo, caller_id, subject_id, layer, purpose)`. Modelled on
`user/care_circle.py::accepted_membership`: one function, one answer, no side path.
Rules, in order (2026-09-21 ruling: 亲属数据走关爱圈):
  1. purpose `individual_return` for a subject who is an `analysis_only` pedigree member is
     refused — their data computes, their conclusion is never returned;
  2. yourself: individual return needs no consent row;
  3. someone else: they must share with the caller through a care circle
     (`repo.circle_access(caller, subject)` = their `health_access`), otherwise denied whatever
     `th_consent` says — the circle IS the person's consent to be read by this caller;
     individual return then needs access ≥ view; research / commercial additionally need a
     live `th_consent` row (scope == purpose, granted, not revoked, layer NULL or equal);
  4. `cross_border` only when that row says so.
Never raises; the tools turn a refusal into an error envelope."""

from __future__ import annotations

from dataclasses import dataclass

PURPOSES = ("individual_return", "research_use", "commercial_use")
LAYERS = ("phenotype", "variant", "signal", "pedigree")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    cross_border: bool = False


async def permit(repo, caller_id: str, subject_id: str, layer: str, purpose: str = "individual_return",
                 cross_border: bool = False) -> Decision:
    if purpose not in PURPOSES:
        return Decision(False, f"unknown purpose {purpose!r}")
    if layer not in LAYERS:
        return Decision(False, f"unknown layer {layer!r}")
    if purpose == "individual_return" and await repo.is_analysis_only(subject_id):
        return Decision(False, "analysis_only: this pedigree member's data takes part in computation and yields no individual conclusion")
    if purpose == "individual_return" and caller_id == subject_id and not cross_border:
        return Decision(True, "self")
    if caller_id != subject_id:
        access = await repo.circle_access(caller_id, subject_id)
        if access is None:
            return Decision(False, "subject is not in a care circle shared with the caller")
        if purpose == "individual_return":
            if int(access) >= 1 and not cross_border:
                return Decision(True, f"care_circle:access={access}")
            if int(access) < 1:
                return Decision(False, f"care_circle: subject grants no health access (access={access})")
    # The NEWEST row for this scope (layer-wide or this layer) decides: consents are
    # append-only, so `granted=false` recorded later is the revocation (record_consent's
    # contract), and an older `granted=true` must not outlive it.
    rows = sorted(await repo.consents(subject_id), key=lambda r: int(r.get("id") or 0), reverse=True)
    for r in rows:
        if r.get("scope") != purpose or r.get("layer") not in (None, "", layer):
            continue
        if not r.get("granted") or r.get("revoked_at"):
            return Decision(False, f"consent#{r.get('id', '?')} scope={purpose} revoked")
        if cross_border and not r.get("cross_border_allowed"):
            return Decision(False, f"consent#{r.get('id', '?')} scope={purpose} does not allow cross-border")
        return Decision(True, f"consent#{r.get('id', '?')} scope={purpose} layer={r.get('layer') or '*'}",
                        cross_border=bool(r.get("cross_border_allowed")))
    return Decision(False, f"no live consent for scope={purpose} layer={layer}" + (" cross_border" if cross_border else ""))
