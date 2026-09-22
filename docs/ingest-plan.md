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

### 2.6 VCF 与病历谁先到都要一致:`dx_refresh.py`(2026-09-22 往返比对逼出来的)

第一轮往返 JD-77(SMA)诊断层不等:薄壳答 ORPHA:70,库里 `th_disease_code` 只有表型排序的 ORPHA:45448(Miyoshi)。
原因是文本钩子只按表型排,薄壳里 `coding.apply_genome` 的"鉴别诊断里第一个其致病基因带 P/LP 变异的病种优先"没有落库版本。
修法:`mirobody_rare/dx_refresh.py::refresh_diagnosis(repo, user_id)`——读该用户 `th_variant`(+ClinVar/gnomAD 注释)与 `th_phenotype`,
用同一条促升规则(gnomAD 常见变异按 `filter_common` 同口径不算证据;组病种如 ORPHA:70 无 Orphanet 基因时经其 OMIM 号取基因),
命中则追加一行 `source='nlp+variant'` 的 candidate(`source_text=promoted_by=<基因>`),幂等。
**两个入口都调**:病历钩子写完表型后(VCF 可能已在)、VCF 入库 + trio 回填后(病历可能已在)。测试 `test_dx_refresh.py`。

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

### 3.7 `summary.html`:给人看的一页总结(与 `samples.md` 同目录)

`samples.md` 是给会读 markdown 的人;`summary.html` 是给任何人:打开即读,手机能看,没有 JavaScript 也一样。

- **风格**:lovable 式单页——米黄背景(`#fbf7ec` 一类,不是纯白)、正文与标题用蓝色系(`#1d4ed8` / `#1e3a8a`),大标题 + 一句话结论 + 指标卡片 + 分层表格 + 抽样对照,留白多,字号从 16 px 起。
- **窄屏自适应**:`<meta viewport>`;布局只用流式块与 CSS grid(`grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr))`),表格外套 `overflow-x: auto`,并排对照在 < 40 rem 宽时改为上下堆叠(`@media` 一条);没有固定像素宽度。
- **无 JS**:页面**不含 `<script>`**;折叠用 `<details>/<summary>`,导航用页内锚点,状态色只用 CSS。测试断言"HTML 里没有 `<script`"。
- **内容顺序**:① 一句话结论(N 例 / 九层 / 真差异 0 条)② 六类差异计数卡片 ③ 每层通过率表 ④ 抽样对照(§3.6 的三栏,长内容已截)⑤ 环境与版本(批次、`world_sha`、ClinVar/HPO 版本、schema、时间)⑥ 缺口与下一步。
- **自包含**:CSS 内联,不引外链字体或样式;图片不用(数字用文字),整页 < 200 kB。
- 生成器:`tools/roundtrip_check.py --html`,模板是 Python 字符串拼接(不引模板库),样本数据与 `samples.md` 同源。

### 3.8 清理口径:每例入库前先清掉上一次注入的东西(2026-09-22 核对)

`tools/roundtrip_check.py::cleanup(uids)` 在每例登录拿到账号 id 后、任何上传前执行,**按该例的账号**删:
`th_variant_annotation → th_variant / th_sequencing_sample / th_phenotype / th_phenotype_review / th_disease_code / th_signal_object / th_consent / th_files`,
再删该账号 owner 的 `th_pedigree(_member)` 与 `care_circles(_members)`;账号本身保留复用(邮箱 `haenv-<case>-<role>@rare.test`)。
核对时发现的两个漏项已补:
- **对象存储字节没删**:`th_files` 行删了,`.theta/mcp/upload/` 里的 VCF 还在(积到 2.0 GB / 127 个对象)。现在 cleanup 先按 `th_files.file_key` 调 `storage.delete(key)` 再删行;
  历史孤儿用 `--purge-orphans .theta/mcp/upload` 一次性清(只删**没有任何 th_files 行引用**的 key,清掉 82 个,剩 45 个全部有引用)。
- 不归本脚本清的:demo 账号(user 1)、`test_pg` 的 `pytest-rare-*` 账号与 `pytest-rare-u1` 行、`th_pedigree` 里两条 `owner_user_id IS NULL` 的旧行(按 owner 唯一之前的烟测残留)。它们不影响比对(全部按账号隔离),但会让全表计数不等于 6 例之和。
- 聊天消息表(`th_messages`)在往返里为 0 行:WebSocket 上传不产生消息;Playwright 走对话读取后会有,§3.9 的 cleanup 要加上。

### 3.9 Web 端验证:Playwright 走"上传 → 读回"(与 §3 的 API 往返互补)

§3 走的是 28085 的 WebSocket 协议直传,证明的是入库链路;它绕过了页面上的三件事——文件选择器的 `accept` 白名单、上传状态 UI、对话里工具调用的呈现。这三件事只有浏览器能证明。

**前置(mirobody-web 侧,必须先改)**:`app/components/chat/ChatInput.tsx:723` 与 `app/upload/page.tsx:1604` 的 `accept` 只有 `image/*,.pdf,.txt,.xlsx…`,
**没有 `.vcf .gz .ped .zip`** ——真人从页面根本选不到 VCF。Playwright 的 `setInputFiles` 会绕过 `accept`,所以测试会"假绿";第一条用例就是断言 `accept` 含这四个后缀(改前红)。

**工具选型**:`pytest-playwright`(Python,与插件测试同一套 pytest;`uv pip install pytest-playwright && playwright install chromium`),headless Chromium,
基址 `MIROBODY_RARE_WEB_BASE=http://<host>:28086`、API `MIROBODY_RARE_E2E_BASE=http://<host>:28085`(缺任一即 skip,与 `test_e2e.py` 同规矩),文件 `plugins/mirobody-rare/tests/test_web_playwright.py`,标记 `-m web`。

**页面锚点(现有 DOM,无 data-testid,用可见文案/type 定位;若后续加 testid 以 testid 为准)**:
| 步骤 | 定位 | 备注 |
|---|---|---|
| 登录 | `input[type=email]`(placeholder "Enter your email address")→ 密码分支 `input[type=password]` + `button[type=submit]` | demo 账号用密码 `Rare2026demo` 免验证码;测试账号先经 `/password/register` 建好 |
| 进对话页 | `/chat` | 等 `ChatInput` 的 `input[type=file]`(class hidden)挂载 |
| 上传 | `page.set_input_files("input[type=file]", [ped, father.vcf.gz, mother.vcf.gz, proband.vcf.gz, JD-xx.md])` | 走 `useWebSocketFileUpload`;等 UI 状态从 uploading → completed(hook 的 `upload_completed` 事件),超时 600 s |
| 读回 | 在输入框(placeholder 来自 i18n `chat.placeholder`)发 "查询我的变异 / 家族史 / 表型" 三问 | 断言回复文本含真值基因符号、PED 家系成员数、金标 HPO 标签之一;并断言页面上出现工具调用块(`query_variant` / `query_family_history` / `query_phenotype` 字样) |
| 权限 | 用父亲账号登录再问 "查询 <先证者> 的变异" | 断言回复是拒绝(`denied` / "未授权"),且 DB 无新增行 |
| 留证 | `--screenshot only-on-failure --video retain-on-failure --tracing retain-on-failure` | 产物进 `reports/roundtrip/<ts>/web/` |

**实跑后修正的三个前置(2026-09-22)**:
1. 对话页 `ChatInput` 的上传走 REST `POST /api/v1/data/upload-health-report`(`dataRequestInstance`),这个后端**没有该路由(405)**;只有 `/upload` 页接的是 `/ws/upload-health-report`。
   所以页面上传用例走 `/upload`,读回走 `/chat`。对话页要能传基因组文件,得把它改到 WebSocket hook 上(未做,记 §6)。
2. `/upload` 页不选"share member"不会开始(`请选择受益人`);后端 `/api/beneficiary-users` 列表总含本人(`is_current_user`),用例按邮箱前缀点选本人。
3. 浏览器预检被 `Access-Control-Allow-Origin: *, *` 拒掉:`HTTP_HEADERS` 同时喂给 uvicorn 静态头和 CORSMiddleware,每个响应带两份。
   主包修法 `middleware_stack.static_response_headers`(uvicorn 只拿非 `Access-Control-*` 的头),红测试 `mirobody/tests/test_static_headers.py`;此前 curl / aiohttp 都不做预检,所以 API 往返没暴露。
   另:前端自定义头 `X-Language` 不在 `Allow-Headers` 里,部署 yaml 改为 `'*'`(无凭据模式下浏览器接受)。
4. 页面还连 `ws://…/api/ws/file-progress`(进度推送),后端没有该路由,只影响进度条,不影响上传。
5. 对话页发 `/api/chat` 用的是 `{content, agent, history, …}`,后端按白名单校验(`code -4`,接受 `question/session_id/provider/query_user_id/user_name/question_id/language/file_list`),
   且流式事件是 `{type: text|tool_call|tool_result}` 而页面 store 认 `{content, tool_use, tool_result}`。修在 `service/api.ts::chatSSE` 一处适配(发送裁字段、接收改名),store 与卡片不动。
6. 权限用例改成确定性的:页面 "Query For" 由 `/api/beneficiary-users`(= `accepted_membership`,health_access ≥ 1)喂,先证者看不到 access=0 的父亲、父亲看得到 access=2 的先证者。
   父亲切到先证者后读到先证者变异**是 owner 共享(access=2)的设计**,不是泄漏;工具侧 `permit` 同一规则(API 往返「权限」层验的是先证者读父亲 → 拒)。靠 LLM "拒绝"措辞断言的版本不稳定,弃。

**结果(2026-09-22)**:`test_web_playwright.py` 4/4 通过(3d.1–3d.4),失败截图机制在排障中用了四次(3d.5)。清单进 rare-mvp-testing.md 3d。

**与 API 往返的对齐**:上传完成后直接复用 `compare_case()` 的九层比对(同一个 `report.json` 加一栏 `via=web`),不重写断言;
UI 层只多三条:accept 白名单、上传进度 UI 收敛、对话可见的工具调用。清理复用 `cleanup(uids)`,并追加 `th_messages` 按 user_id 删。

**验收(进 rare-mvp-testing.md 3d)**:
- [ ] 3d.1 `accept` 含 `.vcf .gz .ped .zip`(改前红)
- [ ] 3d.2 五个文件经页面上传全部到 completed,`th_files` 五行、`content_hash` 与本地 sha256 相等
- [ ] 3d.3 三问回复各含对应真值,页面出现工具调用块
- [ ] 3d.4 父亲账号问先证者被拒,库无新增
- [ ] 3d.5 失败时有截图 + trace 产物

**不做**:不测 `/upload` 页(它是通用上传页,与对话页共用同一个 hook);不做视觉回归;不在 CI 跑(需要真库与 28085/28086 在线)。工作量:web accept 改动 0.5 h,用例 0.5 天。

## 4. 验收(进 rare-mvp-testing.md;2026-09-22 全部打勾,读数见 rare-mvp-impl.md「往返」段)

- [x] 传一份 `rareDieaseCollect` 病例 md:`th_phenotype` 有行,`subject=relative` 的句子归对角色,歧义项进 `th_phenotype_review`,化验值仍进 `th_observation`
- [x] 同一 md 传两次:表型行不翻倍
- [x] 不装插件:`_text_hooks()==[]`,主包 `mirobody/tests` 不变
- [x] 6 个 haenv case(2 trio + 4 单样本)按 §3.1 顺序入库 → §3.2 九层全部"相等或设计性差异",无传输损坏、无版本漂移
- [x] PED 后于 VCF 到达时 trio 仍被回填
- [x] `rc_*` 由回读数据算出,与薄壳直答逐位一致
- [x] 抽样对照报告生成:九层分段、左右并排、长内容按 §3.6 截片段、每段有计数行、密文不显示
- [x] `summary.html`:米黄底蓝字、有 viewport、无 `<script>`、无外链、并排对照在窄屏堆叠、含结论/计数/分层表/抽样/版本/缺口六段

## 6. 未实施 / 未测试清单(2026-09-22 盘点,按"缺什么"分组)

**A. 没实现的功能**
1. ~~Web 页面选不到 VCF/PED~~(2026-09-22 修:三处 `accept` + JS 类型门 + 免 20 MB 上限,含 `.md`)。
2. ~~Playwright 页面级验证~~(2026-09-22 做:4 条用例全绿,§3.9)。新缺口:对话页 `ChatInput` 的上传仍走 REST `/api/v1/data/upload-health-report`(本后端无路由),要改到 WebSocket hook;`/api/ws/file-progress` 进度路由后端没有。
3. 三级同意书写入(plan §7 D6):往返里 `th_consent` 为 0 行,`permit` 只靠关爱圈 `health_access` 放行;同意书从未被写入或校验。
4. 图表/位图分流(plan §14.3 ③;testing 1.6 / 1.8):阶段一只在 review 表记 `document_has_images`,没有检出与 md5 去重。
5. 后台处理队列(plan §17.3;testing 6.5):`mirobody worker` 依赖 Redis 做锁与任务源,本机无 Redis,VCF 解析仍在 server 进程内 `spawn`;`/api/chat` 处理期 p95 未测。
6. 同一 WES 连传两次字节仍落盘两次(testing 6.3;库行已按 sha256 去重)。
7. `th_signal_object.file_id` 为 0(DICOM 处理器拿不到 `th_files.id`),影像层靠 `content_hash` 对账。
8. review 表只有工具 `resolve_phenotype_review`,没有前端队列页。
9. gnomAD 大陆人群白名单写死在代码;人群划分随 gnomAD 版本变化时无告警(只在注释行记 `source_version`)。

**B. 实现了、没有可失败的测试**
10. `Assertion.char_span` 对位(testing 1.7,🟡)。
11. 装载器中途 kill 后重跑行数不变(testing 2.12,🟡;只验了同版本 skipped)。
12. 工具参数 schema 自动生成与 `user_info` 注入(testing 4.2,🟡)。
13. 不装插件时 `th_series_data_genetic` / `query_genetic_data` 不变(testing 5.5,🟡)。
14. tabix 路径在全基因组上的收益(testing 6.4,🟡;3-contig 切片反而慢)。
15. 主包 wheel 不增长闸门(testing 5.2;本克隆缺 git-lfs)。

**C. 有测试、口径不够**
16. 层 1 在**真实病历**上的读数为零:合成句按构造接近天花板,D9 金标(30 份人工标注)未做——这是唯一一条合成题证明不了的验收。
17. `asserted_by` ≥ 0.90 无金标无法量化(testing 1.5)。
18. 常驻内存 352 MB > 150 MB 目标(testing 2.10;其余四张词表仍在内存)。
19. 样本层未比 bcftools stats(只比候选集)。
20. 往返只跑了 6 例、单实例部署、并发 2;多实例(不同 28085 进程)同库并发写与 §3.1 的顺序竞争未测。

**D. 工程收尾**
21. mirobody-rare 只有本地提交(origin 指向上游 mirobody 仓),未推送。
22. `th_disease_code` 保留历史行,读者按 `source` 取最新,没有"当前诊断"视图。

## 5. 工作量
主包钩子 + 插件 `text_hook.py` + review 表与工具:1 天;往返比对脚本 `tools/roundtrip_check.py`(账号/圈/顺序/清理/九层比对/分类报告/抽样对照报告):1.5 天;PED 后到回填:2 小时。
