"""Upload handlers for VCF / PED / DICOM (plan §4.3, §5, §6.3), discovered by the main
package through the `mirobody.file_handlers` entry point.

They bypass the text/LLM path on purpose: `_process_content` returns no `original_text`
(so no indicator extraction runs) and its own `file_abstract` (so no summary model call),
then spawns the ingest job. A WES VCF has hundreds of thousands of lines; feeding it to the
extractor is expensive and meaningless."""

from __future__ import annotations

import logging
from typing import Any

from mirobody.collect.files.handlers.base import BaseFileHandler, FileProcessingContext
from mirobody.utils.tasks import spawn

from . import ingest

log = logging.getLogger(__name__)


async def _head(file, n: int = 256) -> bytes:
    await file.seek(0)
    b = await file.read(n)
    await file.seek(0)
    return b


async def is_vcf(file) -> bool:
    name = (file.filename or "").lower()
    if name.endswith((".vcf", ".vcf.gz")):
        return True
    h = await _head(file)
    if h[:2] == b"\x1f\x8b":
        import gzip, io
        try:
            h = gzip.GzipFile(fileobj=io.BytesIO(await _head(file, 4096))).read(64)
        except Exception:      # noqa: BLE001
            return False
    return h.startswith(b"##fileformat=VCF")


async def is_ped(file) -> bool:
    return (file.filename or "").lower().endswith(".ped")


async def is_dicom_zip(file) -> bool:
    name = (file.filename or "").lower()
    if not name.endswith(".zip"):
        return False
    import io, zipfile
    try:
        await file.seek(0)
        data = await file.read()
        await file.seek(0)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except Exception:          # noqa: BLE001
        return False
    return any(n.lower().endswith(".dcm") for n in names)


class _RareHandler(BaseFileHandler):
    kind = "rare"

    def get_type_name(self) -> str:
        return self.kind

    def _repo(self):
        from .repo import PgRepo
        return PgRepo()


class VcfHandler(_RareHandler):
    kind = "vcf"

    async def _process_content(self, ctx: FileProcessingContext, temp_file_path: str, unique_filename: str, full_url: str, language: str) -> dict[str, Any]:
        spawn(ingest.ingest_vcf(self._repo(), str(ctx.target_user_id), temp_file_path, file_id=None), name=f"ingest_vcf:{unique_filename}")
        return {"original_text": "", "file_abstract": "VCF (GRCh38) — variants are being parsed in the background; "
                                                       "query_variant lists the filtered calls once the sample is ready", "file_name": ctx.filename}


class PedHandler(_RareHandler):
    kind = "ped"

    async def _process_content(self, ctx, temp_file_path, unique_filename, full_url, language) -> dict[str, Any]:
        # the uploader IS the proband unless the PED says otherwise; relatives stay unmapped
        # (analysis_only) until they have accounts of their own
        from .pedigree import parse_ped
        pb = parse_ped(temp_file_path).proband()
        user_ids = {pb.individual_id: str(ctx.target_user_id)} if pb else {}
        pid = await ingest.ingest_ped(self._repo(), temp_file_path, user_ids=user_ids)
        return {"original_text": "", "file_abstract": f"PED pedigree imported (th_pedigree #{pid})", "file_name": ctx.filename}


class DicomHandler(_RareHandler):
    kind = "dicom"

    async def _process_content(self, ctx, temp_file_path, unique_filename, full_url, language) -> dict[str, Any]:
        row = await ingest.ingest_dicom(self._repo(), str(ctx.target_user_id), temp_file_path, file_id=0)
        status = row.get("deid_status")
        return {"original_text": "", "file_name": ctx.filename,
                "file_abstract": (f"DICOM series indexed ({row.get('modality')}, de-identified)" if status == "done"
                                  else f"DICOM series stored but hidden: de-identification failed on {row.get('phi_tags')}")}


#: The entry-point surface: (probe, handler class), tried in order before the shipped handlers.
HANDLERS = ((is_vcf, VcfHandler), (is_ped, PedHandler), (is_dicom_zip, DicomHandler))
