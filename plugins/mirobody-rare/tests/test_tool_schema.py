"""D5 tool surface as the main package's loader sees it: parameter schema from the type
annotations, `user_info` injected (never a model-visible parameter), every tool present."""
from mirobody.mcp.tool import load_tools_from_class
from mirobody_rare.tools import RareQueryService

EXPECTED = {"query_phenotype", "query_variant", "query_signal_index", "query_pedigree",
            "query_family_history", "record_consent", "resolve_phenotype_review"}


def test_loader_exposes_every_tool_with_injected_user_info():
    tools = load_tools_from_class(RareQueryService, RareQueryService.__module__)
    assert EXPECTED <= set(tools), set(tools) ^ EXPECTED
    for name in EXPECTED:
        t = tools[name]
        assert t["auth"] is True, f"{name}: user_info must be injected, not asked from the model"
        schema = t["description"]["inputSchema"]                    # what the model sees (MCP list_tools)
        assert "user_info" not in schema.get("properties", {}), name
        assert t["description"]["description"].strip(), name
    props = tools["query_variant"]["description"]["inputSchema"]["properties"]
    assert {"subject_id", "genes", "purpose"} <= set(props), props.keys()
    assert props["genes"].get("anyOf") or props["genes"].get("type") in ("array", None)
    assert "document_file_id" in tools["record_consent"]["description"]["inputSchema"]["properties"]
