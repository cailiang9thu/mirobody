"""HGNC symbol table: canonical symbol, aliases, previous symbols (plan D8 gene layer)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from .._config import ontology_dir

log = logging.getLogger(__name__)


class Hgnc:
    def __init__(self, path: Path | None = None):
        path = path or (ontology_dir() / "hgnc_complete_set.txt")
        self.symbol: dict[str, dict] = {}
        self.by_alias: dict[str, str] = {}
        with path.open(encoding="utf-8") as fh:
            head = {h: i for i, h in enumerate(fh.readline().rstrip("\n").split("\t"))}
            for line in fh:
                c = line.rstrip("\n").split("\t")
                if len(c) < len(head) or c[head["status"]] != "Approved":
                    continue
                sym = c[head["symbol"]]
                alias = [a for a in c[head["alias_symbol"]].split("|") if a]
                prev = [a for a in c[head["prev_symbol"]].split("|") if a]
                self.symbol[sym] = {"hgnc_id": c[head["hgnc_id"]], "name": c[head["name"]],
                                    "alias": alias, "prev": prev, "location": c[head["location"]]}
                for a in alias + prev:
                    self.by_alias.setdefault(a.upper(), sym)
        log.info("[hgnc] %d approved symbols", len(self.symbol))

    def canonical(self, sym: str) -> str | None:
        s = (sym or "").strip()
        if s in self.symbol:
            return s
        return self.by_alias.get(s.upper())

    def get(self, sym: str) -> dict | None:
        c = self.canonical(sym)
        return dict(self.symbol[c], symbol=c) if c else None


@lru_cache(maxsize=1)
def get_hgnc() -> Hgnc:
    return Hgnc()
