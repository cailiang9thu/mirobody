"""§17.3 item 2: upload chunks never accumulate in a heap bytearray."""
import pytest

from mirobody.collect.files.spooled_upload_file import SpooledUploadFile

MB = 1 << 20


async def test_out_of_order_chunks_assemble_and_spill_to_disk():
    f = SpooledUploadFile("big.bin", "application/octet-stream", total_chunks=3)
    parts = [b"a" * (8 * MB), b"b" * (8 * MB), b"c" * 5]
    f.write_chunk(2, parts[2])
    f.write_chunk(0, parts[0])
    f.write_chunk(1, parts[1])
    assert f.complete
    f.finalize()
    assert f.size == 16 * MB + 5
    assert f.on_disk                                  # > 16 MB threshold: rolled to a real file
    await f.seek(8 * MB - 1)
    assert await f.read(2) == b"ab"
    await f.seek(0, 2)
    assert f.tell() == f.size
    await f.seek(0)
    head = await f.read(4)
    assert head == b"aaaa"
    f.close()
    assert not f.on_disk                              # temp file removed


async def test_small_upload_stays_in_memory_and_reads_whole():
    f = SpooledUploadFile("small.txt", "text/plain", total_chunks=1)
    f.write_chunk(0, b"hello")
    f.finalize()
    assert f.size == 5 and not f.on_disk
    assert await f.read() == b"hello"
    f.close()
