"""An upload that never lives in a heap `bytearray` (rare-mvp-plan §17.3 item 2).

Chunks arrive in any order over the websocket. Each is written to its own temp file at
once; when the last one lands, `finalize()` streams them in index order into one
`SpooledTemporaryFile` (in memory up to `max_memory`, a real file beyond) and deletes the
parts. The result quacks like `MemoryUploadFile` / FastAPI's `UploadFile` (async read /
seek / tell, `filename`, `content_type`, `size`) so every handler downstream is unchanged.
"""

from __future__ import annotations

import os
import shutil
import tempfile

DEFAULT_MAX_MEMORY = 16 * 1024 * 1024


class SpooledUploadFile:
    def __init__(self, filename: str, content_type: str, total_chunks: int = 1, max_memory: int = DEFAULT_MAX_MEMORY):
        self.filename = filename
        self.content_type = content_type
        self.total_chunks = int(total_chunks) or 1
        self._max_memory = max_memory
        self._dir = tempfile.mkdtemp(prefix="mirobody-upload-")
        self._parts: dict[int, str] = {}
        self._spool: tempfile.SpooledTemporaryFile | None = None
        self.size = 0
        self.file = self               # `.file` is what some handlers reach for

    # ------------------------------------------------------------------ receiving
    def write_chunk(self, index: int, data: bytes) -> None:
        if index in self._parts:
            return
        path = os.path.join(self._dir, f"{index:08d}.part")
        with open(path, "wb") as fh:
            fh.write(data)
        self._parts[index] = path

    @property
    def received_chunks(self) -> int:
        return len(self._parts)

    @property
    def complete(self) -> bool:
        return len(self._parts) >= self.total_chunks

    def finalize(self) -> None:
        if self._spool is not None:
            return
        self._spool = tempfile.SpooledTemporaryFile(max_size=self._max_memory, mode="w+b")
        for i in sorted(self._parts):
            with open(self._parts[i], "rb") as fh:
                shutil.copyfileobj(fh, self._spool, 1 << 20)
            os.unlink(self._parts[i])
        self._parts.clear()
        self._spool.flush()
        self.size = self._spool.tell()
        self._spool.seek(0)

    @property
    def on_disk(self) -> bool:
        s = self._spool
        return bool(s is not None and getattr(s, "_rolled", False))

    # ------------------------------------------------------------------ UploadFile surface
    async def read(self, size: int = -1) -> bytes:
        return self._spool.read() if size == -1 else self._spool.read(size)

    async def seek(self, position: int, whence: int = 0) -> None:
        self._spool.seek(position, whence)

    def tell(self) -> int:
        return self._spool.tell()

    async def close(self) -> None:
        self.close_sync()

    def close_sync(self) -> None:
        if self._spool is not None:
            try:
                self._spool.close()
            finally:
                self._spool = None
        for p in self._parts.values():
            try:
                os.unlink(p)
            except OSError:
                pass
        self._parts.clear()
        shutil.rmtree(self._dir, ignore_errors=True)

    def close(self) -> None:          # sync alias used by session cleanup
        self.close_sync()
