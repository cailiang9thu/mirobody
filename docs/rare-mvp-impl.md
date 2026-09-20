# 阶段一 MVP 实施记录 · 编码层(D1a / D1b / D8)

> 对应 [rare-mvp-plan.md](rare-mvp-plan.md) §2 / §3 / §14。2026-09-20,基线 `0d532c8`(1.5.0)。
> 评测在 haenv(`haenv-rare` 分支 `rare-code-bench`,`rare_coding-p1` 60 例)上跑通;读数见文末。

## 落点

```
plugins/mirobody-rare/                 独立发行包(不进主包 wheel)
├── pyproject.toml                     entry point  rare = "mirobody_rare.tools"
├── mirobody_rare/
│   ├── config.yaml                    本体目录 / 缓存目录 / 阈值 / 薄壳端口 / 可选 LLM
│   ├── assertion/rules.py             D1a  文本 → 断言(§14.2 schema:subject / polarity / asserted_by / onset_text / char_span)
│   ├── assertion/llm.py               D1a  可选 Gemini 抽取,同 schema,默认关
│   ├── hpo/bundle.py, hpo/adapter.py  D1b  hpo_bundle.tar.gz + HpoAdapter.resolve / resolve_many
│   ├── disease/                       D8   Orphanet product1/6 + phenotype.hpoa;病名解析 + IC 表型相似度排序
│   ├── gene/                          D8   HGNC 表;genes_to_phenotype 给多基因病排候选
│   ├── coding.py                      管线;solver 合约 JSON
│   ├── serve_coding.py                OpenAI 兼容薄壳(haenv 拍板 #5)
│   ├── tools.py                       MCP 工具 resolve_hpo / code_phenotypes / rank_rare_diseases
│   └── res/zh_curated_hpo.tsv         口语同义词种子(剪刀样步态、霍夫曼征阳性、K-F环…)
└── tests/                             9 条
mirobody/schema/32_phenotype.sql       th_phenotype / th_disease_code(主包只追加;计划里的 a6_ 按 1.5.0 两位前缀改名)
mirobody/res/EXTERNAL.tsv              两个 bundle 的登记行(不进 git / wheel)
```

与计划的三处偏差,都写明理由:

| 计划 | 实施 | 为什么 |
| --- | --- | --- |
| `HpoAdapter(DomainAdapter)`,继承 `indicator/search.py` 的 ABC | 独立类,`domain = "hpo"` 同形 | 1.5.0 已删除 `indicator/`,ABC 不复存在;接口形状保留,阶段二若恢复 ABC 直接挂 |
| `a6_phenotype.sql` | `32_phenotype.sql` | schema README:前缀两位、按字符串序重放 |
| `subject` 取 `proband|father|mother|sibling|other_relative|unknown` | `subject` = `proband|relative` + `subject_role` 取计划的细粒度 | 评测合约要粗粒度,家系分析要细粒度,两者都保留 |

主干 `engine.py` / `units/` / `lexical.py` / `kernel/` 未改;插件只 import 库层的 `lexical.normalize` 与 `zh_fold.fold_to_hans`(numpy-only 契约内)。

## 「拒绝猜测」在编码层的三个形态

1. 词表没命中 → `abstained[]`(不是阴性,是没编上);`kind=lab_value` 转交现有指标链路。
2. 同一标签对应多个 HP:(babelon 里「肌无力」「面具样面容」各两条)→ 按 hpoa 使用频次取排序第一,`review=true`,进人工队列。
3. 病种有多个致病基因(TSC1/TSC2、FHLH、ALS、Dravet、HHT、NPC)→ 按基因自身的 HPO 注释排,没有明显领先者就 `gene.symbol=null` 并列候选;要定基因得靠层2 的变异。

## 跑法

```bash
uv venv --python 3.14 .venv && uv pip install -e . -e plugins/mirobody-rare
.venv/bin/python3 -m pytest plugins/mirobody-rare/tests -q
.venv/bin/python3 -m mirobody_rare.serve_coding --port 8765        # 首次启动会从本体目录建 bundle(~8s)
# haenv 侧(haenv-rare 仓):
uv run haenv run inputs/rare_coding-p1.job.yaml --models mirobody-coding --override-batch-gate
```

## 读数(haenv `rare_coding-p1`,60 例 / 647 条 gold 术语,2026-09-20)

| 判据 | 读数 |
| --- | --- |
| `rc_coverage` | 1.000 |
| `rc_hpo_strict` / `rc_hpo_hier` | 0.9957 / 0.9990 |
| `rc_polarity_ok` / `rc_subject_ok` / `rc_negation_trap` | 1.000 / 1.000 / 0 |
| `rc_orpha_top1` | 0.9833(59/60) |
| `rc_hgnc_ok` | 0.7679(43/56,13 例按规则弃权) |

**别读高**:题面是 HPO 中文标签经模板渲染的句子,词表匹配在这种题面上按构造接近天花板。这轮证明的是链路、对齐、否定/主体、不猜策略;
真实病历上的抽取质量要靠 D9 金标(`rareDieaseCollect` 30 份人工标注)——那是换 prompt / 换模型的唯一裁判,尚未做。
