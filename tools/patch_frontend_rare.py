"""patch_frontend_rare.py — let the bundled client's Ask page accept rare-disease files.

The bundled web client (`frontend/`, build output; this repository never receives its source —
docs/frontend.md) checks every attachment in the browser before upload: a MIME allow-list
(text / markdown / images / PDF / Excel / CSV) and a 20 MB cap. A VCF (`application/gzip`), a
PED (the browser reports no type), a DICOM zip never leave the page: "Please select only allowed
file types". The server accepts all of them (`file_uploader.SUPPORTED_EXTENSIONS`, admission
limits per kind, 2 GB for a VCF), so the refusal is the client's alone.

The one change: a file whose NAME ends in .vcf / .vcf.gz / .ped / .zip / .gz / .dcm skips that
client-side check, and the server's admission decides. Nothing is added to the UI: the files
still go in the Ask box, attached to a question.

Assets are served `immutable`, so every file whose bytes change gets a new name (`<stem>-rare.js`):
the patched chunk, then every chunk that imports it (its content changed with the new name), to a
fixpoint; `index.html` is served no-cache and points at the new entry. A browser holding old files
keeps a consistent old set and never mixes old and new.
Idempotent. Re-run after syncing a new client build into `frontend/`; it says so if the gate's
shape changed and it could not find it.

    .venv/bin/python3 tools/patch_frontend_rare.py [--dir frontend] [--check]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARK = "/*mirobody-rare:gate*/"
RARE = r"/\.(vcf|ped|zip|gz|dcm)$/i"
# `if(!await Hm(t)){if(t.file.size>V_)throw …;if(!K_.includes(t.file.type))throw …}` — the
# WeGene-txt test `Hm` exempts a file from the size + type checks; rare files get the same exemption
GATE = re.compile(r"if\(!await (\w+)\((\w)\)\)\{if\(\2\.file\.size>")


def patch(root: Path, check: bool = False) -> int:
    assets = root / "assets"
    done = [p for p in assets.glob("*.js") if MARK in p.read_text(encoding="utf-8", errors="ignore")]
    if done:
        print(f"already patched: {', '.join(p.name for p in done)}")
        return 0
    hits = [p for p in assets.glob("*.js") if GATE.search(p.read_text(encoding="utf-8", errors="ignore"))]
    if len(hits) != 1:
        print(f"gate not found exactly once (found in {len(hits)} files): the client build changed shape; "
              "patch by hand and update GATE", file=sys.stderr)
        return 2
    if check:
        print(f"would patch {hits[0].name}")
        return 1
    src = hits[0]
    text, n = GATE.subn(lambda m: f"if({MARK}!{RARE}.test({m.group(2)}.file.name)&&!await {m.group(1)}({m.group(2)})){{if({m.group(2)}.file.size>",
                        src.read_text(encoding="utf-8"))
    assert n == 1, n
    src.write_text(text, encoding="utf-8")
    # Rename to a fixpoint: the patched chunk gets a new name, so every chunk that imports it
    # changes content, so it gets a new name too, and so on. A file whose bytes changed under
    # an unchanged name would be served stale to any browser that cached it (`immutable`).
    renames: dict[str, str] = {}
    pending = [src]
    while pending:
        f = pending.pop()
        if f.name in renames:
            continue
        new_name = f"{f.stem}-rare{f.suffix}"
        renames[f.name] = new_name
        for g in [*assets.glob("*.js"), *assets.glob("*.css")]:
            if g.name != f.name and f.name in g.read_text(encoding="utf-8", errors="ignore") and g.name not in renames:
                pending.append(g)
    for old, new in renames.items():
        (assets / old).rename(assets / new)
    pat = re.compile("|".join(re.escape(o) for o in sorted(renames, key=len, reverse=True)))
    touched = 0
    for g in [*root.glob("*.html"), *assets.glob("*.js"), *assets.glob("*.css")]:
        t = g.read_text(encoding="utf-8", errors="ignore")
        t2 = pat.sub(lambda m: renames[m.group(0)], t)
        if t2 != t:
            g.write_text(t2, encoding="utf-8")
            touched += 1
    print(f"patched {src.name}; {len(renames)} files renamed *-rare (content changed), {touched} files had references rewritten")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[1] / "frontend"))
    ap.add_argument("--check", action="store_true", help="exit 1 if a patch is needed, 0 if already patched")
    a = ap.parse_args()
    sys.exit(patch(Path(a.dir), a.check))


if __name__ == "__main__":
    main()
