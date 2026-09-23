"""Attaching files in chat on someone else's record is a WRITE: it needs care-circle edit access,
the same rule the upload WebSocket applies. Before, `_may_chat` (view access) was the only check,
so a view-only member could file documents and genomes into a relative's record from the chat box."""
import mirobody.agent.chat.turn as T
from mirobody.agent.chat.model import ChatStreamRequest
from mirobody.user.care_circle import CareCircleDenied, Subject


def _req(**kw):
    return ChatStreamRequest(user_id="5", question="看看这个", **kw)


def _grant(monkeypatch, access_for_write: bool):
    calls = []

    async def fake(operator, subject=None, *, require_write=False):
        calls.append(require_write)
        if require_write and not access_for_write:
            raise CareCircleDenied("view only")
        return Subject(operator_id=int(operator), subject_id=int(subject), access=2 if access_for_write else 1)
    monkeypatch.setattr(T, "resolve_subject", fake)
    return calls


async def test_view_only_member_may_chat_but_not_attach(monkeypatch):
    calls = _grant(monkeypatch, access_for_write=False)
    r = _req(query_user_id="7", file_list=[{"file_key": "uploads/a.vcf.gz", "file_name": "a.vcf.gz"}])
    assert await T._may_chat(r) is True
    assert await T._may_attach(r) is False
    assert calls == [False, True]                          # the attach check asked for write access


async def test_edit_member_may_attach(monkeypatch):
    _grant(monkeypatch, access_for_write=True)
    r = _req(query_user_id="7", file_list=[{"file_key": "uploads/a.md", "file_name": "a.md"}])
    assert await T._may_attach(r) is True


async def test_own_record_and_text_only_turns_need_no_extra_check(monkeypatch):
    calls = _grant(monkeypatch, access_for_write=False)
    assert await T._may_attach(_req(query_user_id="5", file_list=[{"file_key": "k", "file_name": "n"}]))
    assert await T._may_attach(_req(file_list=[{"file_key": "k", "file_name": "n"}]))
    assert await T._may_attach(_req(query_user_id="7"))
    assert calls == []
