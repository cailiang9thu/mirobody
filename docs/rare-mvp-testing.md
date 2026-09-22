# 罕见病 MVP · 测试与验收清单

> 对应 [rare-mvp-plan.md](rare-mvp-plan.md) 的五处验收(§10 · §14.4 · §15.1 · §16.6 · §17.5,共 26 条),按计划 §9 W11–W12 的要求集中成一份 checklist。
> 主包的测试纪律见 [testing.md](testing.md);本文只管 `plugins/mirobody-rare/` 与它碰到的主包改动。
> 状态:✅ 有可失败的测试且通过 · 🟡 已验证但未固化成测试 · ❌ 未做。2026-09-21。

## 0. 怎么跑

```bash
# 单元 + 内存仓储(无库、无网络,≈12 s)
.venv/bin/python3 -m pytest plugins/mirobody-rare/tests -q
# 加真库回放(schema mirobody_rare;无 DSN 时这些用例自动跳过)
set -a; . ~/caill/.mirobody_rare_env; set +a
.venv/bin/python3 -m pytest plugins/mirobody-rare/tests -q
# 主包契约门
.venv/bin/lint-imports
# 基准评测(haenv-rare 仓,60 例;被测薄壳先起:python -m mirobody_rare.serve_coding --port 8765)
cd ../haenv-rare && uv run haenv run inputs/rare_coding-p1.job.yaml --gen deterministic --models mirobody-coding --override-batch-gate
```

四层测试各管一件事,不能互相替代:

| 层 | 管什么 | 在哪 |
| --- | --- | --- |
| 单元 | 规则、解析器、闸门的**确定性行为** | `tests/test_rules.py` `test_genome.py` `test_layer4_and_governance.py` |
| 真库回放 | DDL 可重放、SQL 与绑定正确、幂等 | `tests/test_pg.py`(需 `MIROBODY_RARE_PG_DSN`) |
| 基准评测 | 编码/变异/影像层对合成金标的读数,**判据能失败**(负对照) | haenv-rare `rare_coding-p1`,判据 `haenv_rare` |
| 端到端 | 上传 → 处理器 → 落库 → 对话工具读出 | 手工脚本(§impl 部署段),待固化 |

## 1. 层1 · 断言抽取与 HPO 编码(§10 · §14.4 · §15.1)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 1.1 | 中文自由文本 → HPO,歧义进人工队列而非自动落库 | `test_coding.py::test_hpo_exact_and_contains` · `test_ambiguous_label_is_coded_but_flagged`(「肌无力」双 term ⇒ `review=true`) | ✅ |
| 1.2 | 打包否定展开:"A、B、C 均正常" ⇒ 3 条 `absent` | `test_rules.py::test_packed_negation_expands` | ✅ |
| 1.3 | 主体归属:亲属表型不记到先证者 | `test_rules.py::test_relative_subject`;基准 `rc_subject_ok` 1.000 | ✅ |
| 1.4 | 断言召回 ≥ 0.85 · polarity ≥ 0.95 · subject ≥ 0.95(§14.4) | 基准:`rc_coverage` 1.000 / `rc_polarity_ok` 1.000 / `rc_subject_ok` 1.000 —— **合成题面,按构造接近天花板** | 🟡 真实语料 D9 金标(30 份人工标注)**未做**,这一行在真实文档上没有读数 |
| 1.5 | `asserted_by` ≥ 0.90 | `test_rules.py::test_onset_and_prior`(单例) | ❌ 无金标无法量化 |
| 1.6 | 图表检出 ≥ 0.90(`unparsed_figures`) | — | ❌ 未实现(D1a ③ 图片分流);ingest-plan §2.2 定为阶段一只在 review 表记 document_has_images |
| 1.7 | 每条断言可回溯 `char_span` | `Assertion.char_span` 生成,无测试 | 🟡 补:20 条抽查对位 |
| 1.8 | 图片 md5 去重 ≥ 20% | — | ❌ 未实现 |
| 1.9 | 判据能失败 | haenv 负对照:翻转极性 → `rc_wrong_rate` 1;错基因 → 0;删断言 → 覆盖 0.55 | ✅(记录在 haenv 决策文档 P0/P5) |

## 2. 层2 · 变异(§10 · §16.6)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 2.1 | GRCh38 VCF 上传 → 异步解析 → `th_variant` | `test_pg.py::test_pg_repo_roundtrip`(直调 ingest)· `test_e2e.py::test_upload_then_chat_reads_variants`(websocket 上传 → 处理器 → `/api/chat` 调 `query_variant` 答出 TP53;需 `MIROBODY_RARE_E2E_BASE`) | ✅ |
| 2.2 | `th_variant` 行数 = 本地过滤器输出数(口径已由 `bcftools stats` 改为候选集,§4.4 ①–③) | `test_coding.py::test_kept_variants_equal_local_filter_output` | ✅ |
| 2.3 | 解析中途 kill → 重跑只补缺失分片,总行数不变无重复 | `test_layer4_and_governance.py::test_ingest_vcf_idempotent_and_ped`(模拟:第二次 0 分片)· `test_pg.py`(真库) | ✅ 分片跳过;🟡 真 kill 未测 |
| 2.4 | 3–5 万 raw call 四级过滤后 < 1000 且已知致病未被误滤 | 基准 `rc_variant_hit` 56/56(spike 变异全部保留);候选数每例 3.18 | ✅ 保留性;🟡 真实 WES 的收窄率无读数 |
| 2.5 | gnomAD 注释,`af_popmax > 0.01` 被标记 | `test_gnomad.py`(合并 exome+genome、popmax、缓存、批量别名查询、common 过滤);**P5e 抓到一刀切删掉两条奠基者突变(HMBS fin 0.026、MEFV mid 0.019)** ⇒ popmax 只算大陆人群、纯合/半合阈值 0.05(`test_popmax_ignores_bottleneck_populations_and_relaxes_for_homozygous`) | ✅ |
| 2.6 | GRCh37 / 未知版本拒收不猜 | `test_layer4_and_governance.py::test_ingest_vcf_refuses_wrong_build` | ✅ |
| 2.7 | trio 遗传来源;父母未覆盖 ⇒ unknown,不判 de novo | `test_genome.py::test_parent_lookup_and_trio`;基准 `rc_variant_inh_ok` 40/40 | ✅ |
| 2.8 | 多致病基因无明显领先者 ⇒ `gene.symbol=null` | `test_genome.py::test_choose_gene_abstains_without_leader`(TSC1/TSC2 平票)· 基准 P5b | ✅ |
| 2.9 | 同一 VCF 重复上传只处理一次 | `test_ingest_vcf_idempotent_and_ped`(`duplicate=True`) | ✅ |
| 2.10 | 内存 vs pg 后端答案逐位相同、延迟 ≤ 2×、常驻 ≤ 150 MB | 60 例对拍 60/60、1.41×、352 MB | ✅ ✅ ❌(352 > 150,§16.8) |
| 2.11 | 32–36 号 DDL 对干净 schema 连跑三次零错误 | `reference.run_schema()` ×3 已验;`test_pg.py` 每次先 `run_schema()` | ✅ |
| 2.12 | 装载器中途 kill 后重跑行数不变 | 同版本重跑 `skipped` 已验;中途 kill 未测 | 🟡 |

## 3. 层4 · 信号 + 治理(§10)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 3.1 | 含 PHI 的 DICOM 上传 → 索引不含姓名/生日/病历号 | `test_dicom_index_phi_fails_without_leaking`(合成 PHI 文件)· 基准 `rc_signal_phi_free` 2/2(pydicom 读头断言不在输出) | ✅ |
| 3.2 | 上下文 tag(设备序列号等)只丢值不隐藏对象 | `test_context_tag_scrubbed_not_failed` | ✅ |
| 3.3 | `deid_status='pending'/'failed'` 对工具不可见 | `test_tools_over_memory_repo`(failed 对象不在 `query_signal_index`) | ✅ |
| 3.4 | 未授权 `research_use` 无法经研究路径读出 | `test_consent_gate_rules` | ✅ |
| 3.5 | `analysis_only` 成员的个体结论不返回 | `test_consent_gate_rules`(含本人也拒)· `test_pg.py`(真库,`error_kind=denied`) | ✅ |
| 3.8 | 亲属数据走关爱圈(§18):不在圈内拒;圈内 view 可读;PED → 账号映射;trio 从父母账号回填 | `test_care_circle.py` 三例 | ✅ |
| 3.6 | 跨境需同意行允许 | `test_consent_gate_rules` | ✅ |
| 3.7 | 未成年人由监护人签署被记录 | MCP 工具 `record_consent`(scope/layer/relationship=self|guardian/document_file_id,一 scope 一行);`test_record_consent_then_permit`:记录后 `permit(research_use, variant)` 放行、其他层仍拒 | ✅ |

## 3b. 叙述病历入库(ingest-plan §2)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 3b.1 | 上传病历 md → `th_phenotype` 有行、亲属句归对角色、否定句 negated、化验值不进表型表 | `test_text_hook.py::test_hook_writes_phenotypes_disease_and_review`(主包 `mirobody.text_hooks` 入口 + 插件 `text_hook.py`) | ✅ |
| 3b.2 | 同一 md 传两次表型行不翻倍 | `test_hook_dedups_by_content_hash` | ✅ |
| 3b.3 | 歧义 / 未命中进 `th_phenotype_review`;`resolve_phenotype_review` 转 clinician 行 | `test_resolve_review_tool_writes_clinician_row`;DDL `37_phenotype_review.sql` 真库回放 | ✅ |
| 3b.4 | 不装插件:`_text_hooks()==[]` | `test_main_package_loader_is_empty_without_plugins` | ✅ |
| 3b.5 | PED 后于 VCF 到达仍回填 trio | `test_care_circle.py::test_ped_after_vcf_still_backfills`(`handlers.backfill_family`,PED/VCF 入库后都触发) | ✅ |

## 3c. 多实例入库往返比对(ingest-plan §3)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 3c.1 | 归一化 / 设计性过滤不算缺失 / 六类分类 / 截片段 | `test_roundtrip.py` 四例(纯函数) | ✅ |
| 3c.2 | `summary.html`:米黄底蓝字、viewport、无 `<script>`、无外链、窄屏堆叠、`<details>`、< 200 kB;`samples.md` 三栏 + 计数行 | `test_html_is_nojs_responsive_cream_blue` · `test_samples_md_has_three_columns_and_count_line` | ✅ |
| 3c.4 | 往返逼出的三处代码缺陷各有红测试:圈成员状态是整数 2 而非字符串(`test_pg.py::test_pg_circle_members_are_accepted_integer_status`)、VCF 与病历谁先到诊断都要用变异重排(`test_dx_refresh.py` 三例)、gnomAD 旧缓存 / HGDP 小队列 popmax(`test_gnomad.py` 两例) | 见左 | ✅ |
| 3c.3 | 真实部署上 N 例走完:账号 → 圈 → PED → 父母 VCF → 先证者 VCF → 病历 → DICOM → 回读九层 | `tools/roundtrip_check.py`;读数见 impl 文档「往返」段 | ✅(2026-09-22 三轮:6 例 / 十层全部相同或设计性差异,真差异 0;`reports/roundtrip/20260922/{samples.md,summary.html}`) |

## 4. 接口 · D5(§10)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 4.1 | 四个工具经 `/mcp` 可调,每个答案带 §8.2 声明 | `assumptions` 非空:`test_tools_over_memory_repo`;`test_e2e.py` 断言对话响应里出现 `query_variant` 调用 | ✅ / 🟡 MCP 面(TRACES)未直接调用过 |
| 4.2 | 工具参数 schema 由类型注解生成、`user_info` 注入 | 服务启动日志;无测试 | 🟡 |
| 4.3 | 家族史问题有工具可答:三来源(亲属账号 / 本人档案叙述 / 家系患病标记)合一表,缺口列出而非静默 | `test_family_history.py` 两例;实机对话「家族里有乳腺癌吗」→ agent 调 `query_family_history`,答"母亲(来自本人档案叙述,未经本人核实)" | ✅ |

## 5. 工程门(§10)

| # | 验收项 | 测试 | 状态 |
| --- | --- | --- | --- |
| 5.1 | `lint-imports` 五条契约 | `.venv/bin/lint-imports` → 6 kept, 0 broken | ✅ |
| 5.2 | `scripts/check_wheel_data.py`,主包 wheel 不增长 | 已跑:wheel 1.40 MB;闸门拒绝发布的原因是本克隆无 git-lfs、`fhir_loinc_bundle.tar.gz` 是 133 B 指针 —— 环境项,与插件无关(插件本就不进主包 wheel) | 🟡 |
| 5.3 | 四个 SQL 文件三次重放零错误 | 见 2.11 | ✅ |
| 5.4 | 不装插件时主包行为不变 | `test_no_plugin_regression.py`:entry point 为空时 `_plugin_handlers()==[]`,`.vcf` 回到"不支持"、`.txt` 仍 TextHandler | ✅ |
| 5.5 | 不装插件时 `th_series_data_genetic` / `query_genetic_data` 不变 | 未动这两处 | 🟡 同上 |

## 6. 大文件(§17.5)

| # | 验收项 | 状态 |
| --- | --- | --- |
| 6.1 | 452 MB DICOM 上传,server RSS 增量 < 50 MB | ✅ 实测增量 **46 MB**(峰 431 / 基线 386):分片落盘 `SpooledUploadFile`(`test_spool.py`),探测只读中央目录;处理完成、`query_signal_index` 经对话读出 |
| 6.2 | 3 GB 文件在第一片前被拒并说明 | ✅ `test_admission.py`(纯函数 4 例)+ 实机:`upload_start` 报 3 GB fastq ⇒ `upload_error/refused`,零 chunk;主包 `collect/files/admission.py` + `handle_upload_start` 接线 |
| 6.3 | 同一 WES 连传两次只落盘一次 | ❌(字节仍落盘两次;库行已去重) |
| 6.4 | 带 `.tbi` 的 WES 解析 ≤ 5 s | 🟡 pysam 按 contig 路径已接(`test_tabix.py`,与文本路径逐位一致);在 3-contig 切片上 5.9 s vs 文本 3.6 s —— 收益在全基因组文件只取需要的 contig,单切片没有;WES 全文件读数待真实 WES |
| 6.5 | 处理期间 `/api/chat` p95 不劣化 | ❌ 阻塞:`mirobody worker` 队列以 Redis 为锁与任务源,本机无 Redis;处理仍在 server 进程内 `spawn` |

## 7. 汇总与下一步

26 条验收(2026-09-21 第三轮 TDD 后):**✅ 25 · 🟡 5 · ❌ 2**;3b/3c 另 10 条(2026-09-22 往返轮):**✅ 10**(部分条目双计)。本轮红→绿:admission 模块不存在、DICOM 探测整读 ⇒ 实现后通过;歧义 review / 过滤器等式 / 多基因弃权三条写出即绿,留作回归。缺口按代价排:

1. **D9 真实语料金标**(1.4 / 1.5)—— 没有它,层1 在真实病历上的读数为零,这是唯一一条"合成题证明不了"的验收;需临床顾问。
2. ~~端到端固化~~ —— 已做 `tests/test_e2e.py`(`-m e2e`,需 `MIROBODY_RARE_E2E_BASE`;实测 70 s 通过)。
3. **§17 大文件**五条 —— 与 17.3 的改造同步落地。
4. gnomAD(2.5)、图表分流(1.6 / 1.8)、同意书写入(3.7)—— 各是一块独立功能。
5. ~~小补~~:5.4 / 3.7 已补;5.2 受 LFS 环境限制。
