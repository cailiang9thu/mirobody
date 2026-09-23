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


async def backfill_family(repo, user_id: str, storage=None) -> dict:
    """Trio backfill for `user_id` and for every proband whose pedigree names `user_id` as a
    parent — run after a VCF lands AND after a PED is imported (ingest-plan §3.1: order-free)."""
    from .genome import trio_backfill
    targets = {str(user_id)}
    fam = await repo.pedigree_of(str(user_id))
    if fam:
        me = next((m for m in fam["members"] if str(m.get("user_id")) == str(user_id)), None)
        if me:
            for m in fam["members"]:
                if m.get("user_id") and me["individual_id"] in (m.get("paternal_id"), m.get("maternal_id")):
                    targets.add(str(m["user_id"]))
        for m in fam["members"]:                       # PED just arrived: every member with an account is a candidate proband
            if m.get("user_id"):
                targets.add(str(m["user_id"]))
    out = {}
    for t in sorted(targets):
        try:
            out[t] = await trio_backfill(repo, t, storage=storage)
        except Exception:                                   # noqa: BLE001
            log.exception("[trio] backfill failed for %s", t)
            out[t] = {"updated": 0, "skipped": ["error"]}
    return out


def content_hash_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _RareHandler(BaseFileHandler):
    kind = "rare"

    def get_type_name(self) -> str:
        return self.kind

    @staticmethod
    def _result(temp_file_path: str, **fields) -> dict[str, Any]:
        """Every rare handler answers with `content_hash` so th_files carries the bytes' identity
        (the shipped text path computes it; a binary path that does not leaves the dedup key and
        the roundtrip file check empty)."""
        return {"original_text": "", "content_hash": content_hash_of(temp_file_path), **fields}

    def _repo(self):
        from .repo import PgRepo
        return PgRepo()


class VcfHandler(_RareHandler):
    kind = "vcf"

    async def _process_content(self, ctx: FileProcessingContext, temp_file_path: str, unique_filename: str, full_url: str, language: str) -> dict[str, Any]:
        # `ctx.target_user_id` is the account the file belongs to: the uploader, or — for a proxy
        # upload (`query_user_id`) — a relative whose care circle granted the uploader write access
        # (the upload manager already ran `resolve_subject(..., require_write=True)`).
        async def _job():
            repo = self._repo()
            from ._config import load as _cfg
            retries = int((_cfg().get("variant") or {}).get("ingest_attempts", 3))
            await ingest.ingest_vcf_retrying(repo, str(ctx.target_user_id), temp_file_path, attempts=retries,
                                             file_id=None, file_key=unique_filename)
            log.info("[trio] backfill after VCF: %s", await backfill_family(repo, str(ctx.target_user_id)))
            from .dx_refresh import refresh_diagnosis
            log.info("[dx_refresh] after VCF: %s", await refresh_diagnosis(repo, str(ctx.target_user_id)))
        spawn(_job(), name=f"ingest_vcf:{unique_filename}")
        return self._result(temp_file_path, file_name=ctx.filename,
                            file_abstract="VCF (GRCh38) — variants are being parsed in the background; query_variant lists the filtered calls once the sample is ready")


class PedHandler(_RareHandler):
    kind = "ped"

    async def _process_content(self, ctx, temp_file_path, unique_filename, full_url, language) -> dict[str, Any]:
        # the uploader IS the proband unless the PED says otherwise; relatives stay unmapped
        # (analysis_only) until they have accounts of their own
        from .pedigree import map_ped_to_circle, parse_ped
        repo = self._repo()
        pg = parse_ped(temp_file_path)
        members = await repo.circle_members(str(ctx.user_id))
        user_ids = map_ped_to_circle(pg, members, uploader_id=str(ctx.target_user_id))
        pid = await ingest.ingest_ped(repo, temp_file_path, user_ids=user_ids, owner_id=str(ctx.target_user_id))
        spawn(backfill_family(repo, str(ctx.target_user_id)), name=f"trio_backfill:{unique_filename}")
        unmapped = [m.individual_id for m in pg.members if m.individual_id not in user_ids]
        return self._result(temp_file_path, file_name=ctx.filename,
                            file_abstract=f"PED pedigree imported (th_pedigree #{pid}); {len(user_ids)} members linked to care-circle accounts"
                                          + (f", unlinked: {', '.join(unmapped)} (no account in your care circle yet)" if unmapped else ""))


class DicomHandler(_RareHandler):
    kind = "dicom"

    async def _process_content(self, ctx, temp_file_path, unique_filename, full_url, language) -> dict[str, Any]:
        row = await ingest.ingest_dicom(self._repo(), str(ctx.target_user_id), temp_file_path, file_id=0)
        status = row.get("deid_status")
        return self._result(temp_file_path, file_name=ctx.filename,
                            file_abstract=(f"DICOM series indexed ({row.get('modality')}, de-identified)" if status == "done"
                                           else f"DICOM series stored but hidden: de-identification failed on {row.get('phi_tags')}"))


#: The entry-point surface: (probe, handler class), tried in order before the shipped handlers.
HANDLERS = ((is_vcf, VcfHandler), (is_ped, PedHandler), (is_dicom_zip, DicomHandler))
