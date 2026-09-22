"""Post-text hooks (docs/ingest-plan.md §2.1): once a handler has turned an upload into
`original_text`, every installed `mirobody.text_hooks` entry point gets the text, beside
(not instead of) the shipped indicator extraction. The rare-disease plugin hangs its
phenotype coder here. With nothing installed the hook list is empty and nothing changes.

An entry point names a module whose `HOOKS` is a sequence of `async fn(ctx: TextHookContext)`.
"""

from __future__ import annotations

from dataclasses import dataclass

_HOOKS: list | None = None


@dataclass(frozen=True)
class TextHookContext:
    text: str
    user_id: str            # the record the text belongs to (target of a proxy upload)
    operator_id: str        # who uploaded
    file_key: str
    file_name: str
    message_id: str | None
    content_hash: str | None = None
    language: str = ""


def _text_hooks() -> list:
    global _HOOKS
    if _HOOKS is None:
        from importlib.metadata import entry_points
        found: list = []
        for ep in entry_points(group="mirobody.text_hooks"):
            found.extend(tuple(ep.load().HOOKS))     # a broken plugin raises here, never skipped silently
        _HOOKS = found
    return _HOOKS
