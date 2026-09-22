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
│   │                                  + D5 六个(query_phenotype[subject 过滤] / query_variant / query_signal_index / query_pedigree /
│   │                                    query_family_history[三来源合一表 + gaps] / record_consent,先过 permit,答案带 §8.2 声明)
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
| 同上,P5e 竞争病诱饵 + gnomAD 过滤(09-21) | 0.964 / 1.000 / 1.000 / 0.964 —— 掉的是奠基者突变(HMBS fin、MEFV mid)被 popmax 一刀切删掉 |
| 同上,P5f popmax 改大陆人群白名单(09-22) | **1.000 / 1.000 / 1.000 / 1.000**(56/56);第二个坑是 gnomAD v4 的 HGDP/1KG 小队列(`hgdp:maya` n≈38),黑名单排不到,改白名单 `afr/amr/eas/nfe/sas` |

**别读高**:层2 的诱饵是隐性携带(0/1),骨架是 GIAB 健康基因组、背景无其它 P/LP 命中,没有 gnomAD 频率与 VEP 后果;真实 WES 的候选集要难得多。
题面是 HPO 中文标签经模板渲染的句子,词表匹配在这种题面上按构造接近天花板。这轮证明的是链路、对齐、否定/主体、不猜策略;
真实病历上的抽取质量要靠 D9 金标(`rareDieaseCollect` 30 份人工标注)——那是换 prompt / 换模型的唯一裁判,尚未做。

## 往返比对:多实例入库 → 回读 → 与原始目录对照(2026-09-22,ingest-plan §3)

`tools/roundtrip_check.py --cases JD-50,JD-52,JD-55,JD-58,JD-72,JD-77 --batch results/joint_dx/rare_coding-p1/20260921-112815 --out reports/roundtrip/20260922 --concurrency 2`
——每例一个账号 + 亲属账号进关爱圈(access=0),顺序 圈 → PED → 父母 VCF → 先证者 VCF → 病历 md → DICOM,走 28085 的 WebSocket 上传(与 web 同入口),回读走原始 SQL + 七个查询工具两条路。
产出 `report.json` / `samples.md`(每层每例抽 ≤3 行左右并排,长内容截片段,末行计数)/ `summary.html`(米黄底蓝字、无 JS、窄屏堆叠)。

| 层 | 条数 | 相同 | 设计性差异 | 真差异 |
| --- | --- | --- | --- | --- |
| 文件(sha256 逐字节) | 31 | 31 | 0 | 0 |
| 样本 | 16 | 16 | 0 | 0 |
| 变异(真值 + 诱饵 + 家长) | 72 | 40 | 32(gnomAD 常见变异:薄壳丢、库里留且带频率) | 0 |
| 注释(ClinVar 版本 / gnomAD) | 40 | 40 | 0 | 0 |
| 遗传来源(trio) | 5 | 5 | 0 | 0 |
| 家系(PED ↔ 圈成员) | 6 | 6 | 0 | 0 |
| 影像(DICOM deid 索引) | 1 | 1 | 0 | 0 |
| 表型(金标句 ↔ th_phenotype) | 51 | 50 | 1(「便秘较明显」歧义进 review 表) | 0 |
| 诊断 | 6 | 6 | 0 | 0 |
| 权限(本人可读 / 父亲 access=0 拒) | 11 | 6 | 5(预期拒) | 0 |

合计 250 条:相同 201 · 设计性 33 · 传输 0 · 版本 0 · 顺序 0 · 权限 5 · 编码 0。三轮才归零,前两轮逼出的都是**代码缺陷**,各留了红测试:

1. `PgRepo.circle_members` 按字符串 `"accepted"` 过滤,而 `care_circle_members.status` 是 SMALLINT 2 ⇒ 圈成员永远为空,PED 三人只挂上先证者,trio 回填读不到家长样本,遗传来源全部 `unknown`。同时把 `circle_access` / `circle_members` 改到插件自己的 asyncpg 池上(不再依赖主包全局配置,测试进程也能跑真库)。
2. 病历钩子只按表型排诊断,VCF 先到/后到都不重排 ⇒ JD-77 停在 Miyoshi 肌病。加 `dx_refresh.py`(与薄壳同一条促升规则,两个入口都调)。
3. 比对脚本本身两处:权限层拿了循环末尾角色的 `got` 当本人行数;gnomAD 常见变异 EXTRA 行是"库里保留带频率"的设计,不是编码错。

**读法**:这证明的是同一份文件经 web 同款入口入库后能原样回读、多实例不串账号、亲属数据按关爱圈隔离;表型层的"原始"仍是 haenv 合成句,真实病历质量待 D9 金标。

## 读数(haenv `rare_coding-p2`,20 例全附件:病历 + VCF/PED + DICOM,2026-09-22)

批次 `20260922-053351`(haenv-rare),被测方 pg 后端薄壳。20 例全部带 VCF+PED(15 trio)与 3 个 TCIA 序列(四个集合轮配)。

| 判据 | 读数 |
| --- | --- |
| 层 1:`rc_coverage` / `rc_polarity_ok` / `rc_subject_ok` / `rc_wrong_rate` | 1.000 / 1.000 / 1.000 / 0 |
| `rc_hpo_strict` / `rc_hpo_hier` | 0.991 / 0.997(2 例各 1 条层级邻近项) |
| 层 2:`rc_orpha_top1` / `rc_hgnc_ok` / `rc_variant_hit` / `rc_variant_inh_ok` / `rc_gene_from_variant` | 1.000 / 1.000 / 1.000 / 1.000(15 trio) / 1.000 |
| 层 4:`rc_signal_index_ok` / `rc_signal_phi_free` | **1.000 / 1.000(20/20)**——p1 只有 2 例在算 |
| 单例延迟中位 | 8.3 s |

HTML 报告:`haenv-rare/reports/rare_coding-p2/20260922-053351/eval-rare_coding-p2.html`(`tools/eval_html.py`)。
出题侧发现:TCIA `STS_032` 的 `PatientName` 与 ID 不等,被发射门当 PHI 拦下,出题改为跳过该患者(haenv-rare 决策文档 P6 段)。

## 部署:前端 + 后端(2026-09-21)

| 项 | 值 |
| --- | --- |
| 后端 | `systemctl --user` 单元 `mirobody-rare-api.service`:`mirobody dev --host 0.0.0.0 --port 28085 ~/caill/mirobody-rare.deploy.yaml`,工作目录本仓,环境文件 `~/caill/.mirobody_rare_env`(PG_URL / PG_SCHEMA=mirobody_rare / JWT_KEY / LLM keys / MIROBODY_RARE_*) |
| 前端 | `mirobody-rare-web.service`:`mirobody-web`(GitLab `<internal-gitlab>/mirobody-web`,`dev` 分支 3de4a57)`next build` 后 `npm run start -p 28086`;`.env.local` 把 `NEXT_PUBLIC_BASE_URL_DATA/MCP` 指向 `http://<private-ip>:28085`(构建期烙入,换地址要重建) |
| 库 | `mirobody_test` · schema `mirobody_rare`:主包 00–90 号 DDL 与 32–36 号全在这一个 schema,与 `mirobody_ai` 隔离 |
| 登录 | `PRODUCTION: false` 的演示账号 `you@mirobody.ai` / 验证码 `111111`(`EMAIL_PREDEFINE_CODES`);JWT_KEY 固定在环境文件里,重启不失效 |
| CORS | 覆盖文件 `mirobody-rare.deploy.yaml` 的 `HTTP_HEADERS.Access-Control-Allow-Origin` = 前端源 |
| 主包改动 | `factory.py` 的 `mirobody.file_handlers` 入口(首次落地时替换文本未命中,只加了辅助函数;已补上循环并经真实上传验证) |
| 模型(2026-09-22) | 聊天默认 `gpt-5.4-mini`(`config.llm.yaml` 第一条 + 覆盖文件 `DEFAULT_MODEL`;直连 api.openai.com,工具调用与 temperature 0.1 已验);`gemini-flash` = `google/gemini-3.8-flash` 走 OpenRouter——Google 直连在本机地区答 400 "User location is not supported",直连条目改名 `gemini-flash-direct` 留给可用地区;OpenRouter 路径的多工具二轮实测可用(4 次 tool_call 后成文)。网页模型选择器的 id 改成与后端 `MODELS` 别名一致(`gpt-5.4-mini / gemini-flash / claude-sonnet / qwen / gpt`),默认选 `gpt-5.4-mini` |
| Web 端修复(2026-09-22,见 ingest-plan §3.9) | 三处 `accept` + JS 类型门放行 `.vcf .gz .ped .zip .md`;`chatSSE` 适配后端 `/api/chat` 字段白名单与 `type: text/tool_call/tool_result` 事件;主包 `static_response_headers` 去掉 uvicorn 静态头里的 `Access-Control-*`(预检曾因 `*, *` 被拒);`Allow-Headers: '*'` 覆盖前端 `X-Language` |
| Web 端已知缺口 | 对话页 `ChatInput` 上传走 REST `/api/v1/data/upload-health-report`(本后端无路由,405),基因组文件要从 `/upload` 页传;`/api/ws/file-progress` 进度路由后端没有,只影响进度条 |

**数据入库后对话能否读出**:能,已实测。

1. 经前端同一条 websocket 路由 `/ws/upload-health-report` 上传 JD-55 的 `proband.vcf.gz` + `JD-55.ped`
   → `VcfHandler` / `PedHandler` 接手(不走文本抽取与摘要模型)→ `th_sequencing_sample`(`status=ready`)、`th_variant` 3 行(ATP7B / TP53 / SMN1,均带 ClinVar 注释)、`th_pedigree(_member)`。
2. `POST /api/chat`「我上传的基因检测里有哪些致病变异…」→ agent 自行调用 `query_variant` 与 `query_pedigree`,按基因列出并说明 `inheritance='unknown'` 是因为没有父母数据;
   「我的家系结构是什么?有没有新发变异?」→ `query_pedigree`,答"单人记录、无 trio,新发变异不能判定也不能排除"——这正是 §8.2 要求随答案发出的声明起了作用。
3. 已知坑:上传管理器在**每个**文件收齐时就启动一次处理(`all_files_received` 只看已开始的文件),两个文件的上传会把第一个处理两遍;插件侧按内容 sha256 去重(`th_sequencing_sample.content_sha256`,同哈希已 ready 则跳过),主包行为未动。
   PED 里的个体 ID 不是账号 ID:`PedHandler` 把先证者映射到上传者,亲属留空(analysis_only)直到有账号。
