"""File-delete hooks: when a user deletes an uploaded file, every installed `mirobody.delete_hooks`
entry point is awaited with the file's identity, after the th_files row is soft-deleted. The
shipped cascade erases the observations extracted from the file; a plugin that derived rows of
its own from it (the rare-disease plugin: variants, phenotypes, DICOM index, pedigree) erases
those here. With nothing installed the hook list is empty and nothing changes.

Awaited inline, not spawned, for the same reason the derived profile is invalidated inline: a
background failure leaves deleted data queryable. A hook that raises is logged and reported in
the delete result; it does not undo the delete.

An entry point names a module whose `HOOKS` is a sequence of `async fn(ctx: FileDeleteContext)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_HOOKS: list | None = None


@dataclass(frozen=True)
class FileDeleteContext:
    user_id: str            # whose record the file is (query_user_id of a proxy upload, else the owner)
    operator_id: str        # who asked for the delete
    file_key: str
    file_id: int | None


def _delete_hooks() -> list:
    global _HOOKS
    if _HOOKS is None:
        from importlib.metadata import entry_points
        found: list = []
        for ep in entry_points(group="mirobody.delete_hooks"):
            found.extend(tuple(ep.load().HOOKS))     # a broken plugin raises here, never skipped silently
        _HOOKS = found
    return _HOOKS


async def run_delete_hooks(ctx: FileDeleteContext) -> list[str]:
    """Run every hook; → one message per hook that failed (empty when all succeeded)."""
    errors: list[str] = []
    for hook in _delete_hooks():
        try:
            await hook(ctx)
        except Exception as e:                        # noqa: BLE001 — reported, not swallowed
            logger.exception(f"delete hook {getattr(hook, '__qualname__', hook)} failed for {ctx.file_key}")
            errors.append(f"{getattr(hook, '__module__', '?')}: {e}")
    return errors
