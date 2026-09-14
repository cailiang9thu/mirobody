"""A real PDF through the real upload path, with no API key and no database.

This is the gate two independent reviewers asked for after 1.4.1's validation
round, and it exists because of what got through without it: every PDF uploaded
via the web client extracted ZERO indicators, on every provider key, and the
upload still reported success. Two defects, both of which a test at this level
would have caught the day they appeared:

* the WebSocket upload accumulates chunks into a ``bytearray``
  (``file_upload_manager``, "content": bytearray() then ``.extend(chunk)``) and
  pypdfium2 answers ``TypeError: Invalid input type 'bytearray'``. Every model
  call after that point was fed an empty string.
* the handler's catch-all then logged a warning and wrote the file row anyway,
  so the API reported ``upload_status: "complete"``, ``indicators_count: 0``,
  ``error: ""`` — a green row with a plausible summary under it.

So the two things asserted here are: the bytes a PDF arrives as do not decide
whether it can be read, and an extraction that failed is never filed as one
that succeeded. Neither needs a provider, which is the point — this runs on
every PR.

Evidence for the CHANGELOG's "Zero indicators now says why … the first two now
fail the upload, or mark the file failed, with the sentence that fixes it."
"""

from __future__ import annotations

import pytest

#: One page, one text line, hand-written — a real PDF pypdfium2 parses, small
#: enough to live in the file. A fixture on disk would be a second thing to
#: keep, and this repository has just spent a release taking bytes OUT of the
#: checkout.
TEXT_LAYER_PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 62 >> stream
BT /F1 12 Tf 72 720 Td (Fasting glucose 5.4 mmol/L on 2026-07-20) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
"""


def _accumulate_like_the_websocket_does(payload: bytes, chunk_size: int = 64):
    """The exact shape `file_upload_manager` builds a finished upload in.

    Copied as a shape, not imported, because the assertion is about the TYPE
    that path produces. If the manager ever stops producing a `bytearray`,
    `test_the_upload_path_still_produces_what_this_module_assumes` fails and
    says so, rather than this module quietly testing a case nobody hits.
    """
    content = bytearray()
    for start in range(0, len(payload), chunk_size):
        content.extend(payload[start:start + chunk_size])
    return content


def test_the_upload_path_still_produces_what_this_module_assumes():
    """The manager buffers into a `bytearray`. The rest of this module is only
    interesting while that is true."""
    import inspect

    from mirobody.pulse.file_parser import file_upload_manager

    source = inspect.getsource(file_upload_manager)
    assert '"content": bytearray()' in source, (
        "the WebSocket upload no longer accumulates into a bytearray — re-read "
        "what it does produce and update this module's premise"
    )


async def test_a_pdf_reads_the_same_from_every_container_the_upload_path_uses():
    """bytes, bytearray, memoryview — one document, one answer.

    The bug was not that `bytearray` is exotic: it is what the ONLY upload path
    the web client uses hands over. `extract_text` normalises at the door.
    """
    pytest.importorskip("pypdfium2", reason="PDF text extraction is the [parse] extra")

    from mirobody.documents import extract

    reference = await extract.extract_text("report.pdf", "application/pdf", TEXT_LAYER_PDF)
    assert "5.4" in reference, "the fixture PDF's text layer did not come back at all"

    from_websocket = await extract.extract_text(
        "report.pdf", "application/pdf", _accumulate_like_the_websocket_does(TEXT_LAYER_PDF)
    )
    from_memoryview = await extract.extract_text(
        "report.pdf", "application/pdf", memoryview(TEXT_LAYER_PDF)
    )
    assert from_websocket == reference
    assert from_memoryview == reference


async def test_a_failed_extraction_is_never_filed_as_a_completed_upload():
    """A `failed_reason` must reach the row as `status: failed` AND an `error`.

    `file_db_service` maps `status` to the `upload_status` the Files page
    renders; a row that says `completed` with zero indicators is
    indistinguishable from a document that genuinely had none.
    """
    from mirobody.pulse.file_parser.handlers.base import BaseFileHandler

    captured: dict[str, dict] = {}

    class _Db:
        @staticmethod
        async def update_file_content(*, file_key, updates):
            captured[file_key] = updates

    import mirobody.pulse.file_parser.services.file_db_service as db_module

    original = db_module.FileDbService
    db_module.FileDbService = _Db
    try:
        class _Handler(BaseFileHandler):
            """The two abstract members, so the shared method under test can run.
            Every real handler inherits `_update_file_indicators` unchanged."""

            def get_type_name(self) -> str:
                return "pdf"

            async def _process_content(self, *a, **k):
                raise AssertionError("not reached")

        handler = _Handler()
        await handler._update_file_indicators(
            file_key="k-failed", formatted_raw="", indicators_count=0,
            failed_reason="indicator extraction failed: TypeError: Invalid input type 'bytearray'",
        )
        await handler._update_file_indicators(
            file_key="k-empty", formatted_raw="", indicators_count=0, failed_reason="",
        )
    finally:
        db_module.FileDbService = original

    failed = captured["k-failed"]
    assert failed["status"] == "failed"
    assert failed["error"], "a failed extraction was written with no reason to show the user"

    # The third state stays a success: a document that really has no indicators
    # is a normal result, and must not be dressed up as an error.
    assert captured["k-empty"]["status"] == "completed"
    assert "error" not in captured["k-empty"]


def test_the_placeholder_abstract_does_not_claim_success():
    """When summarisation fails the reader is told so.

    These templates used to read "uploaded successfully, contains {page_count}
    pages" and were filled with "unknown pages" — rendering "contains unknown
    pages pages" under a green row, over a document nothing had read.
    """
    from mirobody.pulse.file_parser.services.prompts.file_abstract_prompt import (
        FALLBACK_ABSTRACT_TEMPLATES,
    )

    for kind, template in FALLBACK_ABSTRACT_TEMPLATES.items():
        rendered = template.format(
            filename="report.pdf", file_type="PDF", page_count="page count unknown",
            resolution="resolution unknown", sheet_count="sheet count unknown",
            file_size="size unknown", word_count="length unknown",
        )
        assert "successfully" not in rendered, f"{kind}: a placeholder still claims success"
        words = rendered.split()
        assert not any(a == b for a, b in zip(words, words[1:], strict=False)), f"{kind}: doubled word in {rendered!r}"
