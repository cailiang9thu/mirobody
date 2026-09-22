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


def test_mcp_tool_set_is_the_shipped_six_without_the_plugin_and_grows_only_by_rare_tools(monkeypatch):
    """5.5: the engine's own tool surface (`query_genetic_data` included) is untouched by the
    plugin; the plugin only ADDS its `mirobody.tools` entry point."""
    import mirobody.mcp.tool as T
    six = {"resolve_indicator", "convert_unit", "normalize_unit", "query_health_indicators", "query_medications", "query_genetic_data"}
    monkeypatch.setattr(T, "entry_point_modules", lambda group: [])
    tools, _ = T.load_tools_from_directories(["mirobody/agent/tools"])
    assert set(tools) == six, set(tools) ^ six
    monkeypatch.undo()
    tools, _ = T.load_tools_from_directories(["mirobody/agent/tools"])
    assert six <= set(tools) and {"query_variant", "query_family_history", "record_consent"} <= set(tools)
    assert tools["query_genetic_data"]["description"]["inputSchema"] == T.load_tools_from_directories(["mirobody/agent/tools"])[0]["query_genetic_data"]["description"]["inputSchema"]
