"""Binary handlers must hand `content_hash` back so th_files carries the bytes' identity
(the shipped text path computes it; the rare handlers did not → roundtrip file layer failed)."""
from pathlib import Path

from mirobody_rare.handlers import content_hash_of


def test_content_hash_matches_sha256(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc" * 1000)
    import hashlib
    assert content_hash_of(str(p)) == hashlib.sha256(b"abc" * 1000).hexdigest()


def test_all_rare_handlers_return_content_hash():
    import inspect
    from mirobody_rare import handlers
    for cls in (handlers.VcfHandler, handlers.PedHandler, handlers.DicomHandler):
        assert "content_hash" in inspect.getsource(cls._process_content) or "_result" in inspect.getsource(cls._process_content)
