"""§10 engineering: with no `mirobody.file_handlers` distribution, the factory is the shipped
factory — the plugin loop contributes nothing and the shipped order decides."""
import asyncio

from mirobody.collect.files.handlers import factory as F
from mirobody.collect.files.memory_upload_file import MemoryUploadFile


def test_factory_without_plugins(monkeypatch):
    monkeypatch.setattr(F, "_PLUGIN_HANDLERS", None)
    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda **kw: [])
    assert F._plugin_handlers() == []
    fac = F.FileHandlerFactory(None, None, None, None, None)
    vcf = MemoryUploadFile(b"##fileformat=VCFv4.2\n#CHROM\n", "x.vcf.gz", "application/octet-stream")
    assert asyncio.run(fac.get_handler(vcf)) is None          # shipped behaviour: unknown binary → not supported
    txt = MemoryUploadFile(b"hello", "n.txt", "text/plain")
    assert type(asyncio.run(fac.get_handler(txt))).__name__ == "TextHandler"
