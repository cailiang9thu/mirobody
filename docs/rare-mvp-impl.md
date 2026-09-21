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
│   ├── variant/clinvar.py, vcf.py     D2/D3 ClinVar GRCh38 P/LP 本地表(34.9 万条,含星级);VCF 读取、PASS/DP/GQ 过滤、合子性
│   ├── pedigree/ped.py                D7   PED 导入(th_pedigree 行形)、trio 遗传来源判定(父母未覆盖 ⇒ unknown,不判 de novo)
│   ├── genome.py                      层2 管线:指针 → sha256 校验 → 候选变异 → 由变异证据定基因/提升诊断
│   ├── signal/dicom.py                D4   DICOM 系列 zip → 去标识化索引记录(白名单 tag;PHI tag 只用于判 deid_status,值不外泄)
│   ├── consent/gate.py                D6   permit(repo, caller, subject, layer, purpose):四个查询工具的唯一执行喉咙
│   ├── repo.py                        四张表的仓储接口:PgRepo(execute_query,命名绑定)+ MemoryRepo(测试)
│   ├── ingest.py                      VCF(按染色体分片 ≤8 并发、重跑只补缺失分片)/ PED / DICOM 入库作业
│   ├── handlers.py                    上传处理器 VcfHandler / PedHandler / DicomHandler(entry point mirobody.file_handlers)
│   ├── reference/db.py, load.py, sync.py  §16:asyncpg 连接池(schema mirobody_rare)、参考表装载命令、同步桥
│   ├── coding.py                      管线;solver 合约 JSON
│   ├── serve_coding.py                OpenAI 兼容薄壳(haenv 拍板 #5)
│   ├── tools.py                       MCP 工具:层1 三个(resolve_hpo / code_phenotypes / rank_rare_diseases)
│   │                                  + D5 四个(query_phenotype / query_variant / query_signal_index / query_pedigree,先过 permit,答案带 §8.2 声明)
│   └── res/zh_curated_hpo.tsv         口语同义词种子(剪刀样步态、霍夫曼征阳性、K-F环…)
└── tests/                             9 条
mirobody/schema/32_phenotype.sql       th_phenotype / th_disease_code(主包只追加;计划里的 a6_ 按 1.5.0 两位前缀改名)
mirobody/schema/33_variant.sql         th_sequencing_sample / th_variant / th_variant_annotation(计划 a7_)
mirobody/schema/34_pedigree_consent.sql th_pedigree / th_pedigree_member / th_consent(计划 a9_,§7.1 三级同意列)
mirobody/schema/35_signal_index.sql    th_signal_object(计划 a8_)
mirobody/schema/36_rare_reference.sql  ref_clinvar(md5 主键)/ ref_hgnc(_alias)/ ref_gene_hpo / ref_orpha_disorder|name|gene|hpo(计划 §16.3)
mirobody/collect/files/handlers/factory.py  主包唯一改动:`mirobody.file_handlers` entry point(插件 (probe, Handler) 序对先于自带处理器;未装插件时为空)
mirobody/collect/files/services/file_uploader.py · utils/file_types.py  接受 .vcf / .gz / .ped(.dcm 随 .zip)
mirobody/res/EXTERNAL.tsv              两个 bundle 的登记行(不进 git / wheel)
```

与计划的三处偏差,都写明理由:

| 计划 | 实施 | 为什么 |
| --- | --- | --- |
| `HpoAdapter(DomainAdapter)`,继承 `indicator/search.py` 的 ABC | 独立类,`domain = "hpo"` 同形 | 1.5.0 已删除 `indicator/`,ABC 不复存在;接口形状保留,阶段二若恢复 ABC 直接挂 |
| `a6_phenotype.sql` | `32_phenotype.sql` | schema README:前缀两位、按字符串序重放 |
| `subject` 取 `proband|father|mother|sibling|other_relative|unknown` | `subject` = `proband|relative` + `subject_role` 取计划的细粒度 | 评测合约要粗粒度,家系分析要细粒度,两者都保留 |

主干 `engine.py` / `units/` / `lexical.py` / `kernel/` 未改;插件只 import 库层的 `lexical.normalize` 与 `zh_fold.fold_to_hans`(numpy-only 契约内)。

## 层2 · 变异 → 基因(2026-09-21)

```
prediction_context.attachments(路径 + sha256)          ← haenv 出题侧只给指针,不给坐标
   → genome.analyze:sha256 校验(不符即拒读)→ PED(性别、家系角色)
   → variant.read_candidates:PASS · DP≥10 · GQ≥20 · 非参考 GT → ClinVar P/LP 本地表命中(≥1 星)
   → 父母 VCF 在候选位点取 GT → trio_inheritance(de_novo | maternal | paternal | biparental | unknown)
   → choose_gene:① 变异基因 ∈ 首诊致病基因  ② 变异 ClinVar 疾病链接对先证者表型的 IC 相似度  ③ 基因级 HPO 注释
   → 变异的疾病链接可把鉴别诊断中的病种提升为首诊(diagnosis.method = phenotype+variant)
```

输出 `variants[]` 与 `th_variant` / `th_variant_annotation` 同形(chrom/pos/ref/alt/gt/zygosity/depth/gq/filter + clnsig/review_status/stars/conditions),
`genome.pedigree` 与 `th_pedigree_member` 同形。不猜的三处:sha256 不符不读;父母未覆盖不判 de novo;多候选无明显领先者 `gene.symbol=null`。

## 层4 · 原始信号索引与治理(2026-09-21)

* **DICOM 索引是白名单**:只有 Modality / BodyPartExamined / StudyDate / SeriesDescription / SeriesInstanceUID / 实例数 / 厂商 / 性别进记录;
  PatientName / PatientID / BirthDate / 机构 / 操作者 / 设备序列号只被读来判 `deid_status`,值不进记录、不进日志、不进错误信息(失败只报 tag 名)。
  `deid_status != done` 的对象 `query_signal_index` 不列出(计划 §5.3 闸门)。索引记录不带路径:TCIA 目录名就是假名 ID。
* **同意闸门**:`consent/gate.py::permit` 是四个查询工具在 `_run` 前的唯一入口。规则序:analysis_only 成员的个体结论一律拒(连本人也拒)→
  本人个体返回免同意 → 其余须有 scope 相符、已授、未撤销、层匹配的 `th_consent` 行 → 跨境须该行允许。拒绝返回 `error_kind=denied` 的 Envelope。
* **上传路径**:主包 `factory.py` 加一个通用扩展点 `mirobody.file_handlers`(与计划 §2 指出的"插件无 schema 注入点"同类缺口),
  插件挂 VCF / PED / DICOM 三个处理器;它们不产 `original_text`、自带 `file_abstract`,因此不触发指标抽取与摘要模型;VCF 解析在后台按染色体分片,
  重跑只补缺失分片(`written_chroms`),`uq_th_variant_call` 兜底。GRCh37 / 超 500 MB 直接拒收,不猜、不截断。
* **未做**:gnomAD 频率(计划 §4.4 ④,需外网)、VEP 后果、EDF;`PgRepo` 只做了 SQL 与命名绑定,本机无 Postgres,未对真库回放。

## 编码参考库进 Postgres(2026-09-21,计划 §16)

* 库:`mirobody_test`,schema **`mirobody_rare`**;连接串 `MIROBODY_RARE_PG_DSN`,连接池以启动参数设 `search_path`(`SET` 会被 asyncpg 归还时的 `RESET ALL` 抹掉)。
* 表:`36_rare_reference.sql` 的 `ref_clinvar`(md5 生成列主键 `vkey` + `(chrom,pos)` 索引)、`ref_hgnc(_alias)`、`ref_gene_hpo`、`ref_orpha_disorder/name/gene/hpo`;
  装载命令 `mirobody-rare-load-reference`,按 `source_version` 跳过已装版本,ClinVar 走暂存表 `COPY` + 一次合并。
* 切换:`config.reference.backend: memory | pg`(env `MIROBODY_RARE_REFERENCE_BACKEND`)。`pg` 只切换了 ClinVar 客户端:
  远端库往返 ≈500 ms,逐元组/分批 `unnest` 点查一例要 30–70 s,所以做成**键在本地(349k 个 16 B 摘要,≈35 MB,按版本落盘)、载荷在库**,每例一次 `WHERE vkey = ANY(...)`。
* `PgRepo` 改为 asyncpg(`$n` 绑定),对真库回放:VCF 摄取 → `th_variant`/`th_variant_annotation`;重跑 0 分片、行数不变;`query_variant` 从库读回 TP53 行;PED 导入后 `analysis_only` 成员经 `permit` 被拒(`tests/test_pg.py`,无 DSN 时跳过)。

| 读数 | 内存后端 | pg 后端 |
| --- | --- | --- |
| 薄壳常驻(60 例跑完) | 606 MB | 352 MB(ClinVar 351 → 67 MB;其余四张词表仍在内存 ≈190 MB,**未达 §16.6 的 ≤150 MB**,要再切 HGNC / 基因→HPO / Orphanet 名称表) |
| 60 例答案(7 个字段逐位比较) | — | **60/60 逐位相同** |
| 单例延迟中位 / 最大 | 4.72 s / 8.58 s | 6.64 s / 11.47 s(**1.41×**,在 ≤2× 线内) |



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
| `rc_orpha_top1` | 0.9833(59/60)→ **1.000**(附件可见后,SMN1 纯合变异把 SMA 提升为首诊) |
| `rc_hgnc_ok` | 0.7679(43/56,13 例按规则弃权)→ **1.000**(56/56,由变异证据定) |
| `rc_variant_hit` / `rc_variant_gt_ok` / `rc_variant_inh_ok` / `rc_gene_from_variant` | **1.000 / 1.000 / 1.000(40 trio) / 1.000**(每例 1 真值 + 2 隐性携带诱饵) |

**别读高**:层2 的诱饵是隐性携带(0/1),骨架是 GIAB 健康基因组、背景无其它 P/LP 命中,没有 gnomAD 频率与 VEP 后果;真实 WES 的候选集要难得多。
题面是 HPO 中文标签经模板渲染的句子,词表匹配在这种题面上按构造接近天花板。这轮证明的是链路、对齐、否定/主体、不猜策略;
真实病历上的抽取质量要靠 D9 金标(`rareDieaseCollect` 30 份人工标注)——那是换 prompt / 换模型的唯一裁判,尚未做。
