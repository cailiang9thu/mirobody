"""`hpo_bundle.tar.gz`: HPO terms + synonyms + is_a + zh labels, built from the
official `hp.obo` and `hp-zh.babelon.tsv` (plan §3.1 / §3.4).

The bundle is the runtime artifact; the two source files are not read at
runtime. `load_bundle()` builds it on first use when the tarball is missing,
into `cache_dir`, so a fresh checkout works from the ontology drop alone.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .._config import cache_dir, ontology_dir

log = logging.getLogger(__name__)

BUNDLE_NAME = "hpo_bundle.tar.gz"
_SYN_RE = re.compile(r'^synonym:\s+"(.*)"\s+(EXACT|BROAD|NARROW|RELATED)')


@dataclass
class HpoBundle:
    name: dict[str, str] = field(default_factory=dict)          # id -> English label
    zh: dict[str, str] = field(default_factory=dict)            # id -> zh label (babelon rdfs:label)
    syn_en: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # id -> [(text, scope)]
    syn_zh: dict[str, list[str]] = field(default_factory=dict)  # id -> zh synonyms (babelon non-label rows)
    parents: dict[str, list[str]] = field(default_factory=dict)  # id -> is_a
    alt: dict[str, str] = field(default_factory=dict)           # alt_id / obsolete -> canonical
    meta: dict = field(default_factory=dict)
    _anc: dict = field(default_factory=dict, repr=False)

    def canon(self, hp: str) -> str | None:
        hp = (hp or "").strip()
        if hp in self.name:
            return hp
        return self.alt.get(hp)

    def ancestors(self, hp: str, _memo: dict | None = None) -> frozenset[str]:
        memo = _memo if _memo is not None else self._anc
        if hp in memo:
            return memo[hp]
        out: set[str] = set()
        for p in self.parents.get(hp, ()):
            out.add(p)
            out |= self.ancestors(p, memo)
        fs = frozenset(out)
        memo[hp] = fs
        return fs

    def label(self, hp: str) -> str:
        return self.zh.get(hp) or self.name.get(hp, hp)


def _parse_obo(path: Path, b: HpoBundle) -> None:
    cur: dict | None = None
    obsolete: list[tuple[str, str | None]] = []

    def flush() -> None:
        if not cur or "id" not in cur:
            return
        hp = cur["id"]
        if cur.get("obsolete"):
            obsolete.append((hp, cur.get("replaced_by")))
            return
        b.name[hp] = cur.get("name", "")
        b.syn_en[hp] = cur.get("syn", [])
        b.parents[hp] = cur.get("is_a", [])
        for a in cur.get("alt", []):
            b.alt[a] = hp

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line == "[Term]":
                flush()
                cur = {}
                continue
            if line.startswith("[") or cur is None:
                if line.startswith("[") and line != "[Term]":
                    flush()
                    cur = None
                continue
            if line.startswith("id: "):
                cur["id"] = line[4:].strip()
            elif line.startswith("name: "):
                cur["name"] = line[6:].strip()
            elif line.startswith("is_a: "):
                cur.setdefault("is_a", []).append(line[6:].split("!")[0].strip())
            elif line.startswith("alt_id: "):
                cur.setdefault("alt", []).append(line[8:].strip())
            elif line.startswith("is_obsolete: true"):
                cur["obsolete"] = True
            elif line.startswith("replaced_by: "):
                cur["replaced_by"] = line[13:].strip()
            elif line.startswith("synonym: "):
                m = _SYN_RE.match(line)
                if m:
                    cur.setdefault("syn", []).append((m.group(1), m.group(2)))
    flush()
    for hp, rep in obsolete:
        if rep and rep in b.name:
            b.alt[hp] = rep


def _parse_babelon(path: Path, b: HpoBundle) -> None:
    with path.open(encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        idx = {h: i for i, h in enumerate(head)}
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < len(head):
                continue
            hp, pred, val = cols[idx["subject_id"]], cols[idx["predicate_id"]], cols[idx["translation_value"]].strip()
            if not val or hp not in b.name:
                continue
            if pred == "rdfs:label":
                b.zh[hp] = val
            else:
                b.syn_zh.setdefault(hp, []).append(val)


def _parse_zh_synonyms(path: Path, b: HpoBundle) -> None:
    """Curated zh synonyms (`hp-zh.synonyms.tsv`: hpo_id<TAB>synonym[<TAB>...]). Header-only today."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 2 and cols[0] in b.name and cols[1].strip():
                b.syn_zh.setdefault(cols[0], []).append(cols[1].strip())


def _parse_curated(path: Path, b: HpoBundle) -> None:
    """Plugin-shipped clinical colloquialisms (`res/zh_curated_hpo.tsv`): key<TAB>zh synonym,
    where key is an HPO id or the exact English label. Rows whose key is unknown are skipped
    and logged, never guessed."""
    if not path.exists():
        return
    by_name = {v.casefold(): k for k, v in b.name.items()}
    n = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 2:
                continue
            key, syn = cols[0].strip(), cols[1].strip()
            hp = key if key in b.name else by_name.get(key.casefold())
            if not hp or not syn:
                log.warning("[hpo] curated row skipped (unknown key): %r", key)
                continue
            b.syn_zh.setdefault(hp, []).append(syn)
            n += 1
    log.info("[hpo] curated zh synonyms: %d rows", n)


def build_bundle(src: Path | None = None, out: Path | None = None) -> Path:
    src = src or ontology_dir()
    out = out or (cache_dir() / BUNDLE_NAME)
    t0 = time.time()
    b = HpoBundle()
    _parse_obo(src / "hp.obo", b)
    _parse_babelon(src / "hp-zh.babelon.tsv", b)
    _parse_zh_synonyms(src / "hp-zh.synonyms.tsv", b)
    _parse_curated(Path(__file__).resolve().parent.parent / "res" / "zh_curated_hpo.tsv", b)
    b.meta = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "n_terms": len(b.name), "n_zh": len(b.zh),
              "sources": ["hp.obo", "hp-zh.babelon.tsv", "hp-zh.synonyms.tsv", "res/zh_curated_hpo.tsv"]}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, payload: bytes) -> None:
            ti = tarfile.TarInfo(name)
            ti.size = len(payload)
            ti.mtime = 0
            tar.addfile(ti, io.BytesIO(payload))
        rows = []
        for hp in sorted(b.name):
            rows.append(json.dumps({"id": hp, "name": b.name[hp], "zh": b.zh.get(hp, ""),
                                    "syn_en": b.syn_en.get(hp, []), "syn_zh": b.syn_zh.get(hp, []),
                                    "is_a": b.parents.get(hp, [])}, ensure_ascii=False))
        add("terms.jsonl", ("\n".join(rows) + "\n").encode("utf-8"))
        add("alt.json", json.dumps(b.alt, ensure_ascii=False).encode("utf-8"))
        add("META.json", json.dumps(b.meta, ensure_ascii=False, indent=1).encode("utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    log.info("[hpo] bundle built: %d terms (%d zh) -> %s in %.1fs", len(b.name), len(b.zh), out, time.time() - t0)
    return out


def load_bundle(path: Path | None = None) -> HpoBundle:
    path = path or (cache_dir() / BUNDLE_NAME)
    if not path.exists():
        build_bundle(out=path)
    b = HpoBundle()
    with tarfile.open(path, "r:gz") as tar:
        b.alt = json.loads(tar.extractfile("alt.json").read().decode("utf-8"))
        b.meta = json.loads(tar.extractfile("META.json").read().decode("utf-8"))
        for line in tar.extractfile("terms.jsonl").read().decode("utf-8").splitlines():
            if not line:
                continue
            r = json.loads(line)
            hp = r["id"]
            b.name[hp] = r["name"]
            if r.get("zh"):
                b.zh[hp] = r["zh"]
            b.syn_en[hp] = [tuple(x) for x in r.get("syn_en", [])]
            if r.get("syn_zh"):
                b.syn_zh[hp] = r["syn_zh"]
            b.parents[hp] = r.get("is_a", [])
    return b
