"""Draw the README's C·T·A diagram: two languages, two themes, one description.

    python scripts/make_diagrams.py            # write all four files
    python scripts/make_diagrams.py --check    # fail if any is stale

Two files rather than one with a `prefers-color-scheme` <style> block, because
the README is also the PyPI long description (`pyproject.toml: readme`) and
`<picture>` is the only theme mechanism that survives both renderers: measured
against readme_renderer, a ```mermaid fence comes out as a literal
`<pre lang="mermaid">` text block on PyPI, while `<source>` is dropped and the
`<img>` fallback is kept. So GitHub gets the dark variant and PyPI gets light.

The Chinese edition gets a Chinese diagram. An English diagram in a translated
README is the first thing a reader sees and the one part they may not be able
to read, which is a rule `tests/test_readme_links.py` already enforced for the
two diagrams this one replaced.

Generated rather than hand-written so the four cannot drift: the geometry is
declared once, and only the palette and the strings differ.
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"

W, H = 1120, 380
PANEL_Y, PANEL_H, PANEL_W = 76, 240, 300
COLUMNS = (40, 410, 780)          # x of each panel; 70px gutters, 40px margins
MID = PANEL_Y + PANEL_H // 2      # arrows sit on the panels' centre line

#: CJK needs its own stack: Helvetica has no Chinese glyphs, and GitHub renders
#: this SVG in the reader's browser with the reader's own fonts.
FONTS = {
    "": "Helvetica, Arial, sans-serif",
    "zh-CN": '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
}

#: Everything that is words. LOINC and UCUM stay as they are in both: they are
#: the names of the standards, not English for something.
STRINGS = {
    "": {
        "collect": ("① Collect", "devices · files · exports",
                    "the file is kept, and a", "reading points back to it"),
        "translate": ("② Translate", "one name, one code, one unit",
                      "offline and deterministic,", "against a bundle in the package;",
                      "abstains rather than guesses"),
        "agent": ("③ Agent", "reads the original documents",
                  "names the file behind", "a number that came from one"),
        "tail": ("Whatever the lab called it, in — ", "one coded record an agent can cite, out."),
        "alt": "Collect, Translate, Agent: three stages, left to right",
    },
    "zh-CN": {
        "collect": ("① 收集 Collect", "设备 · 文件 · 导出",
                    "源文件留下来，", "读数能指回它"),
        "translate": ("② 转译 Translate", "一个名字，一个码，一个单位",
                      "离线、确定，查的是", "随包发布的词表；",
                      "查不到就弃答，不猜"),
        "agent": ("③ 智能体 Agent", "读的是原始文件",
                  "来自文件的数字，", "会说明出自哪一份"),
        "tail": ("化验所怎么写都行，进来 —— ", "出去是一条带码、可溯源的记录。"),
        "alt": "收集、转译、智能体：三个阶段，从左到右",
    },
}

LIGHT = {
    "bg": "#ffffff",
    "raw_fill": "#f7f9fc", "raw_stroke": "#9bb1c9", "raw_title": "#22303f", "raw_icon": "#4a5f74",
    "std_fill": "#f3faf5", "std_stroke": "#2f7d4a", "std_title": "#1c5233",
    "chip_fill": "#fff3e0", "chip_stroke": "#c08a3e", "chip_text": "#9a6a1e",
    "ai_fill": "#f6edfa", "ai_stroke": "#8a4fb0", "ai_title": "#5e2e80",
    "muted": "#5a6b7a", "arrow": "#7a8aa0", "accent": "#2f7d4a", "body": "#22303f",
}
DARK = {
    "bg": "#0d1117",
    "raw_fill": "#161b22", "raw_stroke": "#30363d", "raw_title": "#c9d1d9", "raw_icon": "#8b949e",
    "std_fill": "#0e2a19", "std_stroke": "#3fb96f", "std_title": "#7ee2a8",
    "chip_fill": "#3a2a10", "chip_stroke": "#d9a24b", "chip_text": "#f0c98a",
    "ai_fill": "#241832", "ai_stroke": "#b47ee0", "ai_title": "#dcb8f5",
    "muted": "#8b949e", "arrow": "#7a8aa0", "accent": "#3fb96f", "body": "#c9d1d9",
}


def _text(x, y, body, *, size=12, fill="#000", weight="normal", anchor="middle"):
    return (f'  <text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}">{body}</text>\n')


def _card(x, fill, stroke, width=1.5):
    return (f'  <rect x="{x}" y="{PANEL_Y}" width="{PANEL_W}" height="{PANEL_H}" rx="14" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>\n')


def _collect(c, t):
    """Three sources, unlabelled: the subtitle already names them together."""
    title, sub, cap1, cap2 = t["collect"]
    x = COLUMNS[0]
    cx = x + PANEL_W // 2
    out = _card(x, c["raw_fill"], c["raw_stroke"])
    out += _text(cx, 122, title, size=23, weight="bold", fill=c["raw_title"])
    out += _text(cx, 146, sub, size=12.5, fill=c["muted"])
    s, i = c["raw_stroke"], c["raw_icon"]
    # a watch, a sample tube, a page: the three ways a reading arrives
    out += (f'  <g stroke="{i}" stroke-width="2" fill="none">\n'
            f'    <rect x="{cx-96}" y="186" width="30" height="40" rx="7"/>\n'
            f'    <path d="M{cx-88} 186 v-8 h14 v8 M{cx-88} 226 v8 h14 v-8"/>\n'
            f'    <circle cx="{cx-81}" cy="206" r="5" fill="{i}" stroke="none"/>\n'
            f'    <rect x="{cx-15}" y="180" width="26" height="52" rx="12"/>\n'
            f'    <path d="M{cx-15} 210 h26" />\n'
            f'    <rect x="{cx-11}" y="214" width="18" height="14" rx="6" fill="{i}" stroke="none"/>\n'
            f'    <rect x="{cx+66}" y="180" width="34" height="46" rx="4"/>\n'
            f'    <path d="M{cx+74} 194 h18 M{cx+74} 204 h18 M{cx+74} 214 h11"/>\n'
            f'  </g>\n')
    out += _text(cx, 266, cap1, size=11.5, fill=c["muted"])
    out += _text(cx, 284, cap2, size=11.5, fill=c["muted"])
    return out


def _translate(c, t):
    title, sub, cap1, cap2, cap3 = t["translate"]
    x = COLUMNS[1]
    cx = x + PANEL_W // 2
    out = _card(x, c["std_fill"], c["std_stroke"], width=2.5)
    out += _text(cx, 122, title, size=23, weight="bold", fill=c["std_title"])
    out += _text(cx, 146, sub, size=12.5, fill=c["muted"])
    out += (f'  <rect x="{cx-86}" y="176" width="172" height="38" rx="10" '
            f'fill="{c["chip_fill"]}" stroke="{c["chip_stroke"]}" stroke-width="1.5"/>\n')
    out += _text(cx, 201, "LOINC · UCUM", size=15, weight="bold", fill=c["chip_text"])
    out += _text(cx, 244, cap1, size=11.5, fill=c["muted"])
    out += _text(cx, 262, cap2, size=11.5, fill=c["muted"])
    out += _text(cx, 284, cap3, size=11.5, fill=c["muted"])
    return out


def _agent(c, t):
    title, sub, cap1, cap2 = t["agent"]
    x = COLUMNS[2]
    cx = x + PANEL_W // 2
    out = _card(x, c["ai_fill"], c["ai_stroke"], width=2.5)
    out += _text(cx, 122, title, size=23, weight="bold", fill=c["ai_title"])
    out += _text(cx, 146, sub, size=12.5, fill=c["muted"])
    # a question, and a trend drawn from more than one document
    out += (f'  <g stroke="{c["ai_stroke"]}" stroke-width="2" fill="none">\n'
            f'    <path d="M{cx-92} 176 h64 a8 8 0 0 1 8 8 v26 a8 8 0 0 1 -8 8 h-40 l-14 12 v-12 '
            f'h-10 a8 8 0 0 1 -8 -8 v-26 a8 8 0 0 1 8 -8 z"/>\n'
            f'    <path d="M{cx+6} 222 l26 -14 l22 8 l30 -30" stroke="{c["accent"]}" stroke-width="2.5"/>\n'
            f'  </g>\n')
    for i, dx in enumerate((6, 32, 54, 84)):
        dy = (222, 208, 216, 186)[i]
        out += f'  <circle cx="{cx+dx}" cy="{dy}" r="3.5" fill="{c["accent"]}"/>\n'
    out += _text(cx, 266, cap1, size=11.5, fill=c["muted"])
    out += _text(cx, 284, cap2, size=11.5, fill=c["muted"])
    return out


def _arrow(x0, x1, colour, marker):
    return (f'  <path d="M{x0} {MID} H{x1}" stroke="{colour}" stroke-width="2.5" '
            f'fill="none" marker-end="url(#{marker})"/>\n')


def render(c: dict, lang: str) -> str:
    t = STRINGS[lang]
    out = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="{W}" height="{H}" font-family=\'{FONTS[lang]}\' '
           f'role="img" aria-label="{t["alt"]}">\n')
    out += f'  <rect width="{W}" height="{H}" fill="{c["bg"]}"/>\n'
    out += '  <defs>\n'
    for name, colour in (("raw", c["arrow"]), ("std", c["accent"])):
        out += (f'    <marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                f'markerHeight="7" orient="auto-start-reverse">\n'
                f'      <path d="M0 0 L10 5 L0 10 z" fill="{colour}"/>\n    </marker>\n')
    out += '  </defs>\n'
    out += _collect(c, t) + _translate(c, t) + _agent(c, t)
    # Grey into Translate, green out of it: the arrow says where a reading stops
    # being whatever the source called it.
    out += _arrow(COLUMNS[0] + PANEL_W + 8, COLUMNS[1] - 10, c["arrow"], "raw")
    out += _arrow(COLUMNS[1] + PANEL_W + 8, COLUMNS[2] - 10, c["accent"], "std")
    lead, bold = t["tail"]
    out += (f'  <text x="{W//2}" y="356" text-anchor="middle" font-size="14" fill="{c["body"]}">'
            f'{lead}<tspan font-weight="bold" fill="{c["accent"]}">{bold}</tspan></text>\n')
    return out + '</svg>\n'


def _name(lang: str, dark: bool) -> str:
    stem = "collect-translate-agent" + ("-dark" if dark else "")
    return f"{stem}.svg" if not lang else f"{stem}.{lang}.svg"


def main() -> int:
    wanted = {_name(lang, dark): render(palette, lang)
              for lang in STRINGS
              for dark, palette in ((False, LIGHT), (True, DARK))}
    check = "--check" in sys.argv
    stale = []
    for name, body in sorted(wanted.items()):
        path = OUT / name
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != body:
                stale.append(name)
        else:
            path.write_text(body, encoding="utf-8")
            print(f"{name:42} {len(body):,} bytes")
    if check:
        if stale:
            print("STALE " + ", ".join(stale) + " — run scripts/make_diagrams.py")
            return 1
        print(f"OK   {len(wanted)} diagrams match scripts/make_diagrams.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
