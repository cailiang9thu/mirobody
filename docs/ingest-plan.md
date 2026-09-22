# 罕见病入库方案:叙述病历跑编码器 + 多实例往返比对

> 2026-09-22。补 [rare-mvp-plan.md](rare-mvp-plan.md) §14/§3.3 的最后一段缺口:文档上传后**只跑化验指标抽取,不跑罕见病编码器**,
> `th_phenotype` / `th_disease_code` 至今只能靠脚本灌;以及多个 haenv 实例走入库再回读比对时要守的规矩。
> 实测入口与协议见 [rare-mvp-impl.md](rare-mvp-impl.md) 部署段;验收进 [rare-mvp-testing.md](rare-mvp-testing.md)。

## 1. 现状(代码路径,已核对)

```
ws /ws/upload-health-report → file_upload_manager(分片落盘 spool)
  → FileProcessor.process_single_file → factory.get_handler
      ├─ 插件处理器 VcfHandler / PedHandler / DicomHandler   ← 已入 th_variant / th_pedigree / th_signal_object
      └─ 主包 Text / PDF / Image / Document / Excel 处理器
           → handlers/base.py::process
              ④ _process_content 产出 original_text
              ④.5 若有 original_text:_start_background_indicator_extraction(original_text, user_id, file_name, file_key, message_id)
                   → prompts/file_indicator_extract.py:STEP 1 只认 "medical examination report" → 化验值 → th_observation
  → 处理器返回后,上传管理器 insert_files_batch → th_files(original_text, content_hash)   ← file_id 到这一步才存在
```

三个事实决定方案形状:
1. 叙述病历(出院小结、病例报告)在 ④.5 被判为"非检验报告"或只抽走化验值,**表型句子全部丢弃**(rare-mvp-plan §14.1 预判,部署后实测印证)。
2. 处理器阶段没有 `file_id`,只有 `file_key`;`th_phenotype.file_id` 溯源要在 `th_files` 写入后再解析。
3. 主包只有一个"文本产出后"的钩子且写死为指标抽取;插件没有注入点(与 §2 的 schema 注入缺口同类)。

## 2. 方案:主包加一个"文本后处理" entry point,插件挂罕见病编码器

### 2.1 主包改动(一处,与 `mirobody.file_handlers` 同形)

`handlers/base.py::process` 的 ④.5 之后加:

```python
for hook in _text_hooks():                      # entry point 组 mirobody.text_hooks:module.HOOKS = [async fn(ctx: TextHookContext)]
    spawn(hook(TextHookContext(text=original_text, user_id=ctx.target_user_id, operator_id=ctx.user_id,
                               file_key=unique_filename, file_name=ctx.filename, message_id=ctx.message_id,
                               content_hash=result_data.get("content_hash"))), name=f"text_hook:{unique_filename}")
```

- 与指标抽取**并列**,不替换:化验值仍走 `th_observation`,表型走 `th_phenotype`(§14.1 的"两条链路并列")。
- 不装插件时 `_text_hooks()` 为空,主包行为不变(回归测试同 `test_no_plugin_regression.py`)。
- `ENABLE_INDICATOR_EXTRACTION=0` 不影响钩子;钩子自己看 `ENABLE_RARE_CODING`(config,默认 1)。

### 2.2 插件侧:`mirobody_rare/text_hook.py`

```
TextHookContext
  → 章节路由(§14.3 ②):按标题正则切 病史/查体/辅助检查/家族史/诊断;PDF/Word 已由主包转成文本
  → 语料过滤(§14.3 ①):去教学选择题(A./B./C. 行密度)、HTML 残留、团队介绍段
  → assertion.extract 逐句(规则版;config.llm.enabled=true 时换 assertion/llm.py,同 schema)
  → coding.code_assertions → CodedAssertion[](HPO)+ diagnosis(ORPHA)+ gene
  → 落库(经 repo):
       th_phenotype      每条编码成功的断言一行:hpo_id/hpo_label 快照/negated/subject/subject_role/source='nlp'/
                         source_text=原句/confidence/asserted_at=文档日期(probe_report_date 的结果,没有则 NULL)/file_id
       th_disease_code   diagnosis.codes.orpha 一行 status='candidate'(有病名直接命中时 confidence 0.9,表型排序时取相似度)
       人工队列          review=true(同标签多 term)与 abstained(未命中)写 th_phenotype_review(新表,§2.4)
  → 图片(§14.3 ③):主包 PDF 路径已把扫描页 OCR 成文本;图内表格/时间轴无法判别 ⇒ 阶段一不做检出,只在 review 表记 "document_has_images=n"
```

`file_id` 的解析:钩子启动时 `th_files` 行可能还没写。做法:先按 `file_key` 轮询 `th_files`(最多 10 s,间隔 0.5 s),拿到 `id` 再写行;超时则 `file_id=NULL` 并记日志——溯源丢失是可见的,不是静默的。
若同一 `content_hash` 已有该用户的 `th_phenotype` 行(同一份文件重传),跳过(与 VCF 的哈希去重同口径)。

### 2.3 输入要求

| 项 | 要求 |
| --- | --- |
| 文件类型 | `.md .txt .pdf .docx .json`(主包能出 `original_text` 的都行);扫描 PDF 依赖主包视觉 OCR(需模型 key) |
| 语言 | 中文为主;英文靠 HPO 英文标签/同义词,覆盖低于中文口语(无 curated 表) |
| 结构 | 有章节标题最好(病史/查体/家族史/诊断);无标题也能跑,但 `section` 为空、`asserted_by` 判定变差 |
| 主体 | 亲属句子要含称谓(父亲/母亲/姐姐…)才归 `relative`;"家族史:阴性"作为 `negated` 家族史落一行 `subject=relative, subject_role=other_relative` |
| 大小 | 走 §17 准入门;文本本身 <1 MB 即可,大于则截断到前 200 k 字符并在 review 表记 truncated |
| 归属 | 数据主人 = `target_user_id`(代传时先过关爱圈写权限);亲属的病历传到亲属账号下,先证者档案里"母亲乳腺癌"这种句子留在先证者账号(`subject=relative`),两者由 `query_family_history` 合并 |

### 2.4 新表 `37_phenotype_review.sql`(主包只追加)

```sql
CREATE TABLE IF NOT EXISTS th_phenotype_review (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       VARCHAR(200) NOT NULL,
    file_id       BIGINT,
    kind          VARCHAR(16) NOT NULL,        -- ambiguous | abstained | figure | truncated
    source_text   TEXT NOT NULL,
    candidates    TEXT[],                      -- ambiguous: 候选 HP:;abstained: 空
    section       VARCHAR(64),
    resolved_hpo  VARCHAR(16),                 -- 人工确认后填,并同步写 th_phenotype(source='clinician')
    resolved_by   VARCHAR(200),
    resolved_at   TIMESTAMPTZ,
    create_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_th_phenotype_review_open ON th_phenotype_review (user_id) WHERE resolved_at IS NULL;
```

配套工具 `resolve_phenotype_review(review_id, hpo_id)`(D5 第七个工具):把队列项转成 `source='clinician'` 的 `th_phenotype` 行;这是 §3.3 "歧义进人工队列而非自动落库"的落点。

### 2.5 不做
- 不改主包 `file_indicator_extract.py` 的 prompt(它的 STEP 1 是有意为之);
- 不在钩子里调 LLM 做视觉表格抽取(§0.2);
- 不把 `original_text` 再存一份(`th_files.original_text` 已有,`source_text` 只存句子)。

## 3. 多 haenv 实例走入库再回读比对

### 3.1 前置:身份与顺序(不做就全错)

| 步 | 做法 |
| --- | --- |
| 账号 | 每个 case 一个账号 `haenv-<case_id>`(`password/register`,PRODUCTION=false);trio 再建 `haenv-<case_id>-F/-M` |
| 关爱圈 | 先证者建圈,邀请父母账号并接受,`health_access=0`(参与计算、不返回个体结论 = analysis_only) |
| 顺序 | 账号 → 圈 → **PED** → 父母 VCF → 先证者 VCF → 叙述病历(渲染或 ledger 拼句)→ DICOM;PED 必须先于 VCF,否则 trio 回填不触发(**待修**:PED 后到也应触发一次回填) |
| 并发 | ≤4 个实例同时传;等 `th_sequencing_sample.status='ready'` 与 `th_files` 行出现再回读,不按固定时间等 |
| 清理 | 按账号前缀级联删 `th_*`、`th_files`、对象存储对象;共享测试库,只动本前缀 |

### 3.2 每层比什么(口径)

| 层 | 库里 | 原始 | 比法 | 预期差异(设计性,不算错) |
| --- | --- | --- | --- | --- |
| 文件 | `th_files.content_hash`、`storage.get(file_key)` | haenv 指针 `sha256` | 逐字节 sha256 相等 | 无 |
| 样本 | `reference`、`sample_label`、`status` | VCF 头 | 相等 / ready | 无 |
| 变异 | 五元组 + GT + 合子性(归一化 chr 前缀、`|`→`/`) | `read_candidates(原文件)` | 集合相等 | 库 < 原始 VCF 行数;gnomAD popmax>0.01 被剔 |
| 注释 | `clnsig` / 星级 / `source_version` | ClinVar 表 | 相等;版本串必须一致(pkl 与 `ref_clinvar` 版本来源不同,先钉死) | 无 |
| 遗传来源 | `inheritance` / `is_de_novo` | haenv `genotypes` 推出的期望 | 只对 trio、回填后比 | 单样本全 `unknown` |
| 家系 | 成员、父母指向、`analysis_only`、账号映射 | PED + 账号表 | 相等 | 无账号亲属留空 |
| 影像 | series sha256、`deid_status`、modality | 指针 series | 集合相等且全 done | 路径不存;`study_date` 已移位 |
| 表型 | hpo/negated/subject/subject_role | haenv `hpo_gold` | 按 `source_text` 对齐后比 | 同标签多 term 进 review 表 |
| 诊断 | `th_disease_code.code` | `rare_orpha` | 相等 | 无 |

`th_files.file_name` / `file_content` 是 Fernet 密文,裸 SQL 不可比;经应用解密或跳过。

### 3.3 回读两条路都跑
- 裸 SQL(`PgRepo`):完整性,父母账号的行只有这条路看得到;
- MCP 工具(经 `permit`):用户可见面,父母行被拒是**预期**。

### 3.4 复用 haenv 判据
把每个账号回读的数据拼回 solver JSON(`assertions / diagnosis / gene / variants / signals`),喂 `haenv_rare.judge_rare_coding` 得 `rc_*`——"库里对不对"与"薄壳答得对不对"同一把尺,负对照现成。

### 3.5 差异分类(报告必须分栏)
传输损坏(sha256 不符)/ 设计性过滤(§3.2 末列)/ 版本漂移(注释串)/ 顺序问题(回填缺失)/ 权限(工具拒读)/ 编码差异(表型层真正的错)。

### 3.6 测试完成后的抽样展示(人读的报告,不只是通过/失败)

比对脚本除了汇总表,还要产出一份**抽样对照报告**(`reports/roundtrip/<batch>/samples.md`),让人不用开库就能看到入库前后长什么样:

- **抽样**:每层随机抽 3 个 case(固定种子,可复现),trio 与单样本各至少 1;每个 case 每层抽 2–3 条记录。
- **分段并排**:按 §3.2 的九层分段,每段左栏"入库前"(原始文件/指针/金标里的内容),右栏"入库后"(库行或工具返回),第三栏"差异类型"(§3.5 六类之一,或"相同")。
- **过长内容截取片段**,不整份贴:
  - 文件:只列 sha256、大小、头部 3 行(VCF 的 `#CHROM` 行与首条记录;PED 全文本来就短);
  - 叙述病历:原文取命中断言所在句的前后各 40 字作上下文,库侧取 `source_text` + 编码结果;
  - 变异:每 case 最多 5 条(真值变异必含,诱饵 1 条,其余随机);
  - 影像:每 series 只列 sha256 前 12 位、modality、`deid_status`、`scrubbed_tags`;
  - 工具返回:JSON 截到 600 字符并标注 "…(+N chars)"。
- **每段末尾**一行计数:该层该 case 共 N 条,抽样 k 条,相同 / 设计性差异 / 真差异各多少。
- 密文字段(`th_files.file_name` 等)显示 "encrypted,未比对",不显示密文。

示例(变异层,JD-50,trio):

```
| 入库前(VCF / 金标)                         | 入库后(th_variant + annotation)                     | 差异 |
| chr14:50269318 G>A  GT 1/1  真值 L2HGDH      | 14:50269318:G:A  1/1 hom  L2HGDH  P  ★2  biparental  | 相同 |
| chr13:51958477 … GT 0/1  诱饵 ATP7B          | 13:51958477 … 0/1 het  ATP7B  P/LP ★2  paternal     | 相同 |
| (原始 131,549 条 PASS 记录)                  | 3 行                                                | 设计性:只存 P/LP 候选 |
```

## 4. 验收(进 rare-mvp-testing.md)

- [ ] 传一份 `rareDieaseCollect` 病例 md:`th_phenotype` 有行,`subject=relative` 的句子归对角色,歧义项进 `th_phenotype_review`,化验值仍进 `th_observation`
- [ ] 同一 md 传两次:表型行不翻倍
- [ ] 不装插件:`_text_hooks()==[]`,主包 `mirobody/tests` 不变
- [ ] 6 个 haenv case(2 trio + 4 单样本)按 §3.1 顺序入库 → §3.2 九层全部"相等或设计性差异",无传输损坏、无版本漂移
- [ ] PED 后于 VCF 到达时 trio 仍被回填
- [ ] `rc_*` 由回读数据算出,与薄壳直答逐位一致
- [ ] 抽样对照报告生成:九层分段、左右并排、长内容按 §3.6 截片段、每段有计数行、密文不显示

## 5. 工作量
主包钩子 + 插件 `text_hook.py` + review 表与工具:1 天;往返比对脚本 `tools/roundtrip_check.py`(账号/圈/顺序/清理/九层比对/分类报告/抽样对照报告):1.5 天;PED 后到回填:2 小时。
