"""`mirobody.routers` entry points: a plugin module's `ROUTERS` are mounted by the server,
beside the shipped routers; with no plugin installed the list is empty and nothing changes."""
import types

from fastapi import APIRouter

import mirobody.utils.plugin_dirs as PD


def test_no_plugin_means_no_extra_routers(monkeypatch):
    monkeypatch.setattr(PD, "entry_point_modules", lambda group: [])
    assert PD.plugin_routers() == []


def test_routers_are_collected_in_entry_point_order_and_junk_is_skipped(monkeypatch):
    a, b = APIRouter(prefix="/api/a"), APIRouter(prefix="/api/b")
    m1 = types.ModuleType("p1"); m1.ROUTERS = (a,)
    m2 = types.ModuleType("p2"); m2.ROUTERS = [b, "not a router"]
    m3 = types.ModuleType("p3")                       # a plugin that registered the group but exports nothing
    seen = []

    def fake(group):
        seen.append(group)
        return [m1, m2, m3]
    monkeypatch.setattr(PD, "entry_point_modules", fake)
    assert PD.plugin_routers() == [a, b]
    assert seen == [PD.GROUP_ROUTERS] == ["mirobody.routers"]
