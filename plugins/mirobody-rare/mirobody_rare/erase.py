"""Deleting an uploaded file erases what this plugin derived from it (main package `mirobody.delete_hooks`).

The main package soft-deletes the th_files row, removes the stored object and erases the
observations extracted from the file; it has never heard of th_variant or th_phenotype. Without
this hook a deleted VCF kept answering `query_variant`, and a deleted narrative kept its HPO rows.

What goes with a file, by the column that ties it to the file:
  th_sequencing_sample + th_variant + th_variant_annotation   sample.file_key
  th_phenotype / th_phenotype_review / th_disease_code        file_id (clinician rows resolved from
                                                              the file's review items included)
  th_signal_object                                            file_key (rows from before 2026-09-23
                                                              were written with file_id=0 and no key:
                                                              they cannot be traced and stay)
  th_pedigree + th_pedigree_member                            pedigree.file_key (same caveat)

Two things are then recomputed rather than left stale:
  * trio inheritance, for everyone the loss touches: the owner, the probands a deleted parental
    sample fed, every account of a deleted pedigree. With a parent gone the loci go back to
    `unknown` (`genome.trio_backfill`), never keep the old `maternal` / `de_novo`.
  * variant-promoted diagnoses (`source='nlp+variant'`): dropped and re-derived from what is left.

If the user still has a LIVE copy of the same bytes (same content_hash — the upload manager
stores a duplicate upload as a second object and a second th_files row, and ingest reuses the
first sample), nothing is erased: the rows are re-pointed to the surviving copy. Otherwise
deleting the first of two identical uploads would orphan the sample the second one relies on.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def erase_file(repo, user_id: str, file_key: str, file_id: int | None = None, storage=None) -> dict:
    user_id = str(user_id)
    row = await repo.file_by_key(file_key)
    if file_id is None and row:
        file_id = row.get("id")
    dup = await repo.live_duplicate(user_id, (row or {}).get("content_hash"), file_key)
    if dup:
        moved = await repo.repoint_file(user_id, file_key, file_id, dup["file_key"], dup["id"])
        log.info("[erase] %s deleted but a live copy %s remains: re-pointed %s", file_key, dup["file_key"], moved)
        return {"action": "repointed", "to": dup["file_key"], **moved}

    affected = {user_id}
    if any(s.get("file_key") == file_key for s in await repo.samples_of(user_id)):
        affected |= await _probands_fed_by(repo, user_id)       # read the pedigree BEFORE anything is erased
    gone = await repo.erase_file_rows(user_id, file_key, file_id)
    affected |= set(gone.get("pedigree_users") or [])
    out = {"action": "erased", **gone}
    if gone.get("samples") or gone.get("pedigrees"):
        out["inheritance"] = await _recompute_trios(repo, affected, storage)
    if gone.get("samples") or gone.get("phenotypes"):
        from .dx_refresh import refresh_diagnosis
        await repo.drop_promoted(user_id)
        out["promoted"] = (await refresh_diagnosis(repo, user_id)).get("promoted")
    log.info("[erase] %s for user %s: %s", file_key, user_id, {k: v for k, v in out.items() if v})
    return out


async def _probands_fed_by(repo, user_id: str) -> set[str]:
    """Accounts whose trio inheritance was computed from `user_id`'s sample (they name them as a parent)."""
    fam = await repo.pedigree_of(user_id)
    if not fam:
        return set()
    me = next((m for m in fam["members"] if str(m.get("user_id")) == user_id), None)
    if not me:
        return set()
    return {str(m["user_id"]) for m in fam["members"]
            if m.get("user_id") and me["individual_id"] in (m.get("paternal_id"), m.get("maternal_id"))}


async def _recompute_trios(repo, users: set[str], storage) -> dict:
    from .genome import trio_backfill
    out = {}
    for u in sorted(users):
        try:
            out[u] = await trio_backfill(repo, u, storage=storage)
        except Exception:                                   # noqa: BLE001 — one family must not stop the rest
            log.exception("[erase] trio recompute failed for %s", u)
            out[u] = {"error": True}
    return out


async def rare_delete_hook(ctx) -> None:
    from .repo import PgRepo
    await erase_file(PgRepo(), ctx.user_id, ctx.file_key, ctx.file_id)


HOOKS = (rare_delete_hook,)
