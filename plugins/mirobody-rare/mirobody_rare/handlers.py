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
    """A zip holding .dcm files, decided from the END of the archive: the zip central
    directory sits in the last ~64 kB (+ its own size), so a 2 GB series costs the same
    read as a 2 MB one. The old probe read the whole body into memory (§17.2)."""
    name = (file.filename or "").lower()
    if not name.endswith(".zip"):
        return False
    import io, struct, zipfile
    size = getattr(file, "size", None)
    if not isinstance(size, int) or size <= 0:
        try:
            await file.seek(0, 2)
            size = file.tell()
        except Exception:                                   # noqa: BLE001
            return False
    tail_len = min(size, 64 * 1024 + 22 + 4096)
    try:
        await file.seek(size - tail_len)
        tail = await file.read(tail_len)
        await file.seek(0)
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            return False
        cd_size, cd_off = struct.unpack("<II", tail[eocd + 12:eocd + 20])
        if cd_off + cd_size > size:
            return False
        if cd_off >= size - tail_len:
            cd = tail[cd_off - (size - tail_len):cd_off - (size - tail_len) + cd_size]
        else:
            await file.seek(cd_off)
            cd = await file.read(cd_size)
            await file.seek(0)
        i = 0
        while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02":
            n_len, e_len, c_len = struct.unpack("<HHH", cd[i + 28:i + 34])
            fname = cd[i + 46:i + 46 + n_len].decode("utf-8", "ignore").lower()
            if fname.endswith(".dcm"):
                return True
            i += 46 + n_len + e_len + c_len
        return False
    except Exception:                                       # noqa: BLE001
        return False


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
