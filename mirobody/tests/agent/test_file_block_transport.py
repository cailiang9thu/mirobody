"""A PDF block goes on the wire only if the WIRE carries one.

This is the gate for a 400 that reached users twice.

`read_file` delivers a PDF inside a `ToolMessage`. Whether that is accepted is
a property of the TRANSPORT, and the shipped default — `claude-sonnet` over
OpenRouter, the chat model the recommended key selects — does not accept it:

    400 tool messages must include a non-empty string tool_call_id
    400 Invalid value: 'file'. Supported values are: 'text', 'refusal',
        'image_url', and 'input_audio'                      (gpt-5.6-terra)

Both models genuinely read PDFs, over APIs that are not the endpoint the
client is talking to. The first fix asked `model.profile["pdf_tool_message"]`
and treated any non-None answer as authoritative — and the 400 came straight
back, through two separate doors:

  1. `_profile_override` wrote that field from the entry's `supports_pdf`
     boolean, which is a statement about the MODEL;
  2. LangChain's own bundled profile writes it too — `gpt-5.6-terra` carries
     `pdf_tool_message: True` there and still 400s on Chat Completions.

So the rule is: the transport decides, and a profile may only NARROW. These
tests pin both doors shut. No API key, no network.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core", reason="the agent layer is the [agents] extra")

from mirobody.agent.agent import MirobodyAgent
from mirobody.agent.models.clients import _profile_override

_TOOL_MESSAGE_FIELDS = ("pdf_tool_message", "image_tool_message")


def _client(module: str, profile: dict | None = None):
    """A stand-in whose CLASS names the transport, which is all the decision
    reads. Building a real one needs a key and a network."""
    namespace: dict = {"__module__": module}
    if profile is not None:
        namespace["profile"] = profile
    return type("FakeClient", (), namespace)()


def _decide(client) -> bool:
    return MirobodyAgent.__new__(MirobodyAgent)._supports_file_block(client)


@pytest.mark.parametrize("declaration", [{"supports_pdf": True}, {"supports_pdf": False},
                                         {"supports_image": True}, {"supports_image": False}])
def test_a_capability_boolean_never_writes_a_tool_message_field(declaration):
    """`supports_pdf` / `supports_image` say what the MODEL takes.

    Writing them into a `*_tool_message` field states something about the wire
    that the entry never claimed, and that is door #1. Dropping them changes
    nothing downstream: in deepagents' `_multimodal_block_supported` a `True`
    was already a no-op ("Only an explicit `False` rejects a block type") and a
    `False` still vetoes through the `*_inputs` field these do set.
    """
    override = _profile_override(declaration)
    assert override, "the boolean must still reach the profile"
    for field in _TOOL_MESSAGE_FIELDS:
        assert field not in override, f"{declaration} wrote {field}, a claim about the wire"
    assert any(key.endswith("_inputs") for key in override), "the veto has to survive somewhere"


def test_an_explicit_profile_dict_still_wins_over_the_booleans():
    """The advanced escape hatch is untouched — it is only stopped from
    GRANTING a file block on a transport that cannot carry one (below)."""
    override = _profile_override({"supports_pdf": True, "profile": {"pdf_inputs": False}})
    assert override["pdf_inputs"] is False


@pytest.mark.parametrize(("module", "profile", "expected"), [
    # The regression itself: an OpenAI-compatible transport, told `True` by
    # whichever of the three sources — it must still refuse.
    ("langchain_openai.chat_models.base", {"pdf_tool_message": True}, False),
    ("langchain_openai.chat_models.base", None, False),
    ("langchain_anthropic.chat_models", None, True),
    ("langchain_google_genai.chat_models", None, True),
    # A profile may narrow: a model that cannot read PDFs at all.
    ("langchain_anthropic.chat_models", {"pdf_tool_message": False}, False),
    ("langchain_google_genai.chat_models", {"pdf_tool_message": False}, False),
    # An unrecognised gateway serves extracted text, which works.
    ("some_gateway.chat_models", {"pdf_tool_message": True}, False),
])
def test_the_transport_decides_and_a_profile_only_narrows(module, profile, expected):
    assert _decide(_client(module, profile)) is expected


def test_the_shipped_chat_entries_land_where_they_should():
    """Read from config.llm.yaml, so a new entry on the wrong transport fails
    here rather than in a user's chat window."""
    import pathlib

    import yaml

    import mirobody

    config = pathlib.Path(mirobody.__file__).parent.parent / "config.llm.yaml"
    if not config.exists():
        pytest.skip("config.llm.yaml is not beside the package (an installed wheel)")
    models = (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get("MODELS") or {}
    # `llm_type` chooses the client class in `build_chat_model`; this maps the
    # same choice onto the module that class comes from, without a key.
    module_for = {
        "openai": "langchain_openai.chat_models.base",
        "openrouter": "langchain_openai.chat_models.base",
        "anthropic": "langchain_anthropic.chat_models",
        "google-genai": "langchain_google_genai.chat_models",
    }
    checked = 0
    for alias, entry in models.items():
        entry = entry or {}
        if str(entry.get("chat", "")).strip().lower() in ("false", "0", "no", "off") or entry.get("embedding"):
            continue
        module = module_for.get(str(entry.get("llm_type") or "openai").strip().lower())
        if module is None:
            continue
        expected = module != "langchain_openai.chat_models.base"
        client = _client(module, _profile_override(entry) or None)
        assert _decide(client) is expected, (
            f"{alias} ({entry.get('llm_type', 'openai')}) would "
            f"{'send' if not expected else 'withhold'} a PDF file block"
        )
        checked += 1
    assert checked >= 4, "the shipped chat entries were not found — did MODELS move?"
