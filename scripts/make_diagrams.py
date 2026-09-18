"""Draw the README's diagrams: two languages, two themes, one description each.

    python scripts/make_diagrams.py            # write all eight files
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
    "deny_fill": "#fdf1ef", "deny_stroke": "#c0453a", "deny_title": "#9c2f26",
}
DARK = {
    "bg": "#0d1117",
    "raw_fill": "#161b22", "raw_stroke": "#30363d", "raw_title": "#c9d1d9", "raw_icon": "#8b949e",
    "std_fill": "#0e2a19", "std_stroke": "#3fb96f", "std_title": "#7ee2a8",
    "chip_fill": "#3a2a10", "chip_stroke": "#d9a24b", "chip_text": "#f0c98a",
    "ai_fill": "#241832", "ai_stroke": "#b47ee0", "ai_title": "#dcb8f5",
    "muted": "#8b949e", "arrow": "#7a8aa0", "accent": "#3fb96f", "body": "#c9d1d9",
    "deny_fill": "#2d1512", "deny_stroke": "#e0665a", "deny_title": "#f2a79d",
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


# ─────────────────────────────────────────────────────────────────────────────
# The care-circle diagram: one request, one throat, two exits.
#
# It draws `user/care_circle.py:resolve_subject`, not a product flow. Every
# label names something the module has: the two `status` values that must both
# be accepted, the `health_access` column that lives on the SUBJECT's own row
# and defaults to 0, the trim to what was asked, and the raise `server.py` maps
# to 403. The diagram this replaces claimed "unshare a thread anytime", which
# no endpoint implements; drawing the authorization path instead means a
# reviewer can check the picture against the code.

CARE_W, CARE_H = 1120, 400
CARE_X = (40, 400, 820)               # panel x
CARE_CW = (300, 360, 260)             # widths; 60px gutters, 40px margins
CARE_Y, CARE_PH = 84, 240
CARE_MID = CARE_Y + CARE_PH // 2
CARE_EXIT_H = 90

CARE_STRINGS = {
    "": {
        "head": ("Joining a circle is not ", "being seen."),
        "req": ("A request", "you → her record",
                "“how has her cholesterol moved?”"),
        "gate": ("resolve_subject", "one authorization throat"),
        # Each check reads as a sentence, then as the code that decides it.
        # Mixing the two — "writing needs edit" — puts an identifier where a
        # word belongs and reads as neither.
        "checks": (("Both of you accepted", "status = accepted, on both rows"),
                   ("The switch is hers", "health_access starts at 0"),
                   ("Seeing is not editing", "health_access 1 reads, 2 writes")),
        "allow": ("Subject(access=view)", "trimmed to what was asked"),
        "deny": ("CareCircleDenied", "raised, not returned → 403"),
        "foot": ("Your own record never takes this path: ",
                 "acting on your own data is not proxy access."),
        "alt": ("How one person reaches another's health record: a request passes "
                "resolve_subject, which requires both memberships accepted and the "
                "subject's own health_access switch, and either returns access "
                "trimmed to the request or raises a 403"),
    },
    "zh-CN": {
        "head": ("加入圈子，", "不等于被看见。"),
        "req": ("一个请求", "你 → 她的记录", "“她的胆固醇怎么变的”"),
        "gate": ("resolve_subject", "唯一的授权闸门"),
        # 上一行是人话，下一行是判据。写成「要写就得是 edit」这种，
        # 是把标识符当中文词用，两头都不像。
        "checks": (("双方都接受了邀请", "status = accepted，两行都要"),
                   ("开关在她自己手上", "health_access 初始为 0"),
                   ("能看不等于能改", "health_access = 1 只读，= 2 可写")),
        "allow": ("Subject(access=view)", "只给请求要的那么多"),
        "deny": ("CareCircleDenied", "抛出，不是返回 → 403"),
        "foot": ("你自己的记录不走这条路：", "操作自己的数据不是代理访问。"),
        "alt": ("一个人如何读到另一个人的健康记录：请求经过 resolve_subject，"
                "它要求双方成员关系都已接受、且对方自己打开了 health_access 开关，"
                "然后要么返回按请求裁剪过的权限，要么抛出 403"),
    },
}


def _care_panel(i, fill, stroke, width=1.5):
    return (f'  <rect x="{CARE_X[i]}" y="{CARE_Y}" width="{CARE_CW[i]}" height="{CARE_PH}" '
            f'rx="14" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>\n')


def _care_request(c, t):
    """Two people and an arrow between them."""
    title, line, quote = t["req"]
    cx = CARE_X[0] + CARE_CW[0] // 2
    out = _care_panel(0, c["raw_fill"], c["raw_stroke"])
    out += _text(cx, 118, title, size=19, weight="bold", fill=c["raw_title"])
    out += _text(cx, 140, line, size=12.5, fill=c["muted"])
    # Head and shoulders, filled: an outlined shoulder arc under an outlined
    # head reads as a cup, not a person. The arc is wider than tall and its
    # top meets the head's underside.
    out += f'  <g fill="{c["raw_icon"]}" stroke="none">\n'
    for dx in (-72, 72):
        out += (f'    <circle cx="{cx+dx}" cy="184" r="13"/>\n'
                f'    <path d="M{cx+dx-23} 218 a23 17 0 0 1 46 0 z"/>\n')
    out += '  </g>\n'
    out += (f'  <path d="M{cx-34} 196 H{cx+30}" stroke="{c["arrow"]}" stroke-width="2" '
            f'fill="none" marker-end="url(#careq)"/>\n')
    # Labelled with the parameter names, which is the point: the check this
    # module replaced named its first parameter `user_id` and received the
    # target there at all eleven call sites.
    out += _text(cx - 72, 244, "operator_id", size=10.5, fill=c["muted"])
    out += _text(cx + 72, 244, "subject_id", size=10.5, fill=c["muted"])
    out += _text(cx, 282, quote, size=12, fill=c["body"])
    return out


def _care_gate(c, t):
    """Three checks, each with the column or value that decides it."""
    title, sub = t["gate"]
    x, cx = CARE_X[1], CARE_X[1] + CARE_CW[1] // 2
    out = _care_panel(1, c["chip_fill"], c["chip_stroke"], width=2.5)
    out += _text(cx, 118, title, size=19, weight="bold", fill=c["chip_text"])
    out += _text(cx, 140, sub, size=12.5, fill=c["muted"])
    for n, (claim, basis) in enumerate(t["checks"]):
        y = 178 + n * 46
        out += f'  <circle cx="{x+34}" cy="{y-4}" r="9" fill="{c["chip_stroke"]}"/>\n'
        out += _text(x + 34, y, str(n + 1), size=11, weight="bold", fill=c["chip_fill"])
        out += _text(x + 54, y - 7, claim, size=12.5, weight="bold",
                     fill=c["body"], anchor="start")
        out += _text(x + 54, y + 9, basis, size=10.5, fill=c["muted"], anchor="start")
    return out


def _care_exits(c, t):
    """Two cards, because the throat has exactly two ways out."""
    x, cx = CARE_X[2], CARE_X[2] + CARE_CW[2] // 2
    out = ""
    pairs = ((t["allow"], CARE_Y + 14, c["std_fill"], c["std_stroke"], c["std_title"]),
             (t["deny"], CARE_Y + 136, c["deny_fill"], c["deny_stroke"], c["deny_title"]))
    for (head, note), yy, fill, stroke, title_fill in pairs:
        out += (f'  <rect x="{x}" y="{yy}" width="{CARE_CW[2]}" height="{CARE_EXIT_H}" '
                f'rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2"/>\n')
        out += _text(cx, yy + 38, head, size=14, weight="bold", fill=title_fill)
        out += _text(cx, yy + 62, note, size=11, fill=c["muted"])
    return out


def render_care(c: dict, lang: str) -> str:
    t = CARE_STRINGS[lang]
    lead, bold = t["head"]
    out = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CARE_W} {CARE_H}" '
           f'width="{CARE_W}" height="{CARE_H}" font-family=\'{FONTS[lang]}\' '
           f'role="img" aria-label="{t["alt"]}">\n')
    out += f'  <rect width="{CARE_W}" height="{CARE_H}" fill="{c["bg"]}"/>\n'
    out += '  <defs>\n'
    for name, colour in (("careq", c["arrow"]), ("caok", c["accent"]),
                         ("cano", c["deny_stroke"])):
        out += (f'    <marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
                f'      <path d="M0 0 L10 5 L0 10 z" fill="{colour}"/>\n    </marker>\n')
    out += '  </defs>\n'
    out += (f'  <text x="{CARE_W//2}" y="48" text-anchor="middle" font-size="17" '
            f'fill="{c["body"]}">{lead}'
            f'<tspan font-weight="bold" fill="{c["chip_text"]}">{bold}</tspan></text>\n')
    out += _care_request(c, t) + _care_gate(c, t) + _care_exits(c, t)
    out += (f'  <path d="M{CARE_X[0]+CARE_CW[0]+8} {CARE_MID} H{CARE_X[1]-10}" '
            f'stroke="{c["arrow"]}" stroke-width="2.5" fill="none" '
            f'marker-end="url(#careq)"/>\n')
    gx = CARE_X[1] + CARE_CW[1] + 8
    ok_y = CARE_Y + 14 + CARE_EXIT_H // 2
    no_y = CARE_Y + 136 + CARE_EXIT_H // 2
    # Straight, from one point: the gutter is 60px and a curve inside it turns
    # so sharply that the two exits look tangled rather than forked.
    for y, colour, marker in ((ok_y, c["accent"], "caok"), (no_y, c["deny_stroke"], "cano")):
        out += (f'  <path d="M{gx} {CARE_MID} L{CARE_X[2]-11} {y}" stroke="{colour}" '
                f'stroke-width="2.5" fill="none" marker-end="url(#{marker})"/>\n')
    flead, fbold = t["foot"]
    out += (f'  <text x="{CARE_W//2}" y="372" text-anchor="middle" font-size="13" '
            f'fill="{c["muted"]}">{flead}'
            f'<tspan fill="{c["body"]}">{fbold}</tspan></text>\n')
    return out + '</svg>\n'


def _care_name(lang: str, dark: bool) -> str:
    stem = "your-care-circle" + ("-dark" if dark else "")
    return f"{stem}.svg" if not lang else f"{stem}.{lang}.svg"


def main() -> int:
    wanted = {_name(lang, dark): render(palette, lang)
              for lang in STRINGS
              for dark, palette in ((False, LIGHT), (True, DARK))}
    wanted |= {_care_name(lang, dark): render_care(palette, lang)
               for lang in CARE_STRINGS
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
