"""samples.md and summary.html renderers (ingest-plan §3.6 / §3.7). Pure string building, no
template engine, no JavaScript, no external asset: the HTML must read on a phone with scripts off."""

from __future__ import annotations

import html as _h

KIND_ZH = {"same": "相同", "design": "设计性差异", "transport": "传输损坏", "version": "版本漂移", "order": "顺序问题",
           "permission": "权限", "coding": "编码差异"}


def render_samples_md(rep: dict) -> str:
    out = [f"# 入库往返抽样对照 · {rep.get('batch', '')}", "",
           f"case:{', '.join(rep.get('cases', []))}", ""]
    for s in rep.get("samples", []):
        out += [f"## {s['layer']} · {s['case']}", "", "| 入库前 | 入库后 | 差异 |", "|---|---|---|"]
        for r in s["rows"]:
            out.append(f"| {r['before']} | {r['after']} | {r['diff']} |")
        c = s["count"]
        out += ["", f"共 {c['n']} 条,抽样 {c['k']} 条:相同 {c['same']} · 设计性差异 {c['design']} · 真差异 {c['real']}", ""]
    if rep.get("gaps"):
        out += ["## 缺口", ""] + [f"- {g}" for g in rep["gaps"]]
    return "\n".join(out) + "\n"


_CSS = """
:root{--bg:#fbf7ec;--card:#fffdf7;--ink:#1e3a8a;--accent:#1d4ed8;--muted:#4b5f9e;--line:#e6dcc3;--ok:#166534;--bad:#991b1b}
*{box-sizing:border-box}html{font-size:16px}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.55}
main{max-width:64rem;margin:0 auto;padding:1.25rem 1rem 4rem}
h1{font-size:2rem;margin:.5rem 0 .25rem;color:var(--accent)}h2{font-size:1.35rem;margin:2rem 0 .75rem;color:var(--accent)}
h3{font-size:1.05rem;margin:1.25rem 0 .5rem}p{margin:.5rem 0}
.lead{font-size:1.15rem;color:var(--ink)}.muted{color:var(--muted);font-size:.9rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem;margin:1rem 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:.75rem;padding:.9rem 1rem}
.card .n{font-size:1.9rem;font-weight:700;color:var(--accent)}.card .k{color:var(--muted);font-size:.9rem}
.card.bad .n{color:var(--bad)}.card.ok .n{color:var(--ok)}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:.75rem}
table{border-collapse:collapse;width:100%;min-width:28rem}th,td{text-align:left;padding:.5rem .7rem;border-bottom:1px solid var(--line);vertical-align:top;font-size:.95rem}
th{color:var(--muted);font-weight:600}
.pair{display:grid;grid-template-columns:1fr 1fr auto;gap:.75rem;padding:.6rem 0;border-bottom:1px solid var(--line)}
.pair>div{min-width:0;overflow-wrap:anywhere}.pair .lab{color:var(--muted);font-size:.8rem;display:block}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:1rem;font-size:.8rem;background:#e8eefc;color:var(--accent);white-space:nowrap}
.tag.bad{background:#fde8e8;color:var(--bad)}.tag.design{background:#f3ecd8;color:#7c5a00}
details{background:var(--card);border:1px solid var(--line);border-radius:.75rem;padding:.5rem .9rem;margin:.6rem 0}
summary{cursor:pointer;color:var(--accent);font-weight:600}
nav a{color:var(--accent);margin-right:1rem}
@media (max-width:40rem){html{font-size:15px}h1{font-size:1.6rem}.pair{grid-template-columns:1fr}.pair>div:last-child{justify-self:start}}
"""


def _tag(kind: str) -> str:
    cls = "tag" + (" bad" if kind in ("transport", "version", "order", "coding") else " design" if kind == "design" else "")
    return f'<span class="{cls}">{_h.escape(KIND_ZH.get(kind, kind))}</span>'


def render_html(rep: dict) -> str:
    e = _h.escape
    counts = rep.get("counts", {})
    real = sum(counts.get(k, 0) for k in ("transport", "version", "order", "coding"))
    n_cases = len(rep.get("cases", []))
    verdict = (f"{n_cases} 例走完入库往返,九层比对,真差异 {real} 条" + (",设计性差异 " + str(counts.get("design", 0)) + " 条" if counts.get("design") else "")
               + (",权限拒读 " + str(counts.get("permission", 0)) + " 处(预期)" if counts.get("permission") else "") + "。")
    cards = "".join(f'<div class="card {"bad" if (k in ("transport","version","order","coding") and counts.get(k,0)) else "ok" if k=="same" else ""}">'
                    f'<div class="n">{counts.get(k, 0)}</div><div class="k">{e(KIND_ZH[k])}</div></div>' for k in KIND_ZH)
    layers = "".join(f"<tr><td>{e(l['layer'])}</td><td>{l['n']}</td><td>{l['same']}</td><td>{l['design']}</td>"
                     f"<td>{l['real']}</td><td>{'✅' if l['real']==0 else '❌'}</td></tr>" for l in rep.get("layers", []))
    samples = []
    for s in rep.get("samples", []):
        rows = "".join(f'<div class="pair"><div><span class="lab">入库前</span>{e(str(r["before"]))}</div>'
                       f'<div><span class="lab">入库后</span>{e(str(r["after"]))}</div><div>{_tag(_kind_of(r["diff"]))}</div></div>' for r in s["rows"])
        c = s["count"]
        samples.append(f'<details open><summary>{e(s["layer"])} · {e(s["case"])}</summary>{rows}'
                       f'<p class="muted">共 {c["n"]} 条,抽样 {c["k"]} 条:相同 {c["same"]} · 设计性差异 {c["design"]} · 真差异 {c["real"]}</p></details>')
    env = "".join(f"<tr><th>{e(str(k))}</th><td>{e(str(v))}</td></tr>" for k, v in (rep.get("env") or {}).items())
    gaps = "".join(f"<li>{e(g)}</li>" for g in rep.get("gaps", []))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>入库往返总结 · {e(str(rep.get('batch', '')))}</title><style>{_CSS}</style></head>
<body><main>
<nav class="muted"><a href="#verdict">结论</a><a href="#counts">计数</a><a href="#layers">分层</a><a href="#samples">抽样</a><a href="#env">版本</a><a href="#gaps">缺口</a></nav>
<h1 id="verdict">入库往返总结</h1>
<p class="lead">{e(verdict)}</p>
<p class="muted">批次 {e(str(rep.get('batch', '')))} · case:{e(', '.join(rep.get('cases', [])))}</p>
<h2 id="counts">差异计数</h2><div class="cards">{cards}</div>
<h2 id="layers">每层通过率</h2><div class="tbl"><table><thead><tr><th>层</th><th>比对项</th><th>相同</th><th>设计性差异</th><th>真差异</th><th></th></tr></thead><tbody>{layers}</tbody></table></div>
<h2 id="samples">抽样对照</h2>{''.join(samples)}
<h2 id="env">环境与版本</h2><div class="tbl"><table><tbody>{env}</tbody></table></div>
<h2 id="gaps">缺口与下一步</h2><ul>{gaps or '<li>无</li>'}</ul>
<p class="muted">本页无脚本、无外链,内容与 samples.md 同源。</p>
</main></body></html>
"""


def _kind_of(diff_label: str) -> str:
    for k, zh in KIND_ZH.items():
        if diff_label == zh or diff_label == k:
            return k
    return "coding" if diff_label else "same"
