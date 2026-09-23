"""`mirobody.delete_hooks` entry points: deleting a file awaits every plugin hook with the file's
identity, inline, after the soft delete. A failing hook is reported in the result, not swallowed
and not allowed to undo the delete. With no plugin installed nothing changes."""
import mirobody.collect.files.delete_hooks as DH
import mirobody.collect.files.services.file_processing_service as FPS
from mirobody.collect.files.services.file_db_service import FileDbService


def _stub_delete_path(monkeypatch, record):
    async def get_file_by_key(file_key, user_id):
        return record
    async def soft_delete_file(file_key, user_id):
        return True
    async def storage_delete(file_key):
        return True
    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(FileDbService, "get_file_by_key", staticmethod(get_file_by_key))
    monkeypatch.setattr(FileDbService, "soft_delete_file", staticmethod(soft_delete_file))
    monkeypatch.setattr(FPS, "delete_file_from_storage", storage_delete)
    monkeypatch.setattr(FPS, "_invalidate_derived_profile", nothing)
    monkeypatch.setattr(FPS, "_start_background_cascade_delete", lambda **k: None)


async def test_delete_awaits_hooks_with_the_records_owner(monkeypatch):
    seen = []
    async def hook(ctx):
        seen.append(ctx)
    monkeypatch.setattr(DH, "_HOOKS", [hook])
    _stub_delete_path(monkeypatch, {"id": 41, "file_name": "p.vcf.gz", "file_type": "vcf", "query_user_id": "77"})
    res = await FPS.delete_files_from_message("m1", ["web_uploads/k.vcf.gz"], "5")
    assert res["success"] and "cascade_errors" not in res["deleted_files"][0]
    [ctx] = seen
    # a proxy upload's rows live under the account it was uploaded FOR, not the operator's
    assert (ctx.user_id, ctx.operator_id, ctx.file_key, ctx.file_id) == ("77", "5", "web_uploads/k.vcf.gz", 41)


async def test_a_failing_hook_is_reported_and_the_delete_still_stands(monkeypatch):
    async def boom(ctx):
        raise RuntimeError("db down")
    monkeypatch.setattr(DH, "_HOOKS", [boom])
    _stub_delete_path(monkeypatch, {"id": 1, "file_name": "a.md", "file_type": "text", "query_user_id": None})
    res = await FPS.delete_files_from_message("m1", ["web_uploads/a.md"], "5")
    [d] = res["deleted_files"]
    assert d["status"] == "deleted" and "db down" in d["cascade_errors"][0]


async def test_no_plugin_means_no_hooks(monkeypatch):
    monkeypatch.setattr(DH, "_HOOKS", None)
    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda group=None: [])
    assert DH._delete_hooks() == []
    assert await DH.run_delete_hooks(DH.FileDeleteContext("1", "1", "k", None)) == []
