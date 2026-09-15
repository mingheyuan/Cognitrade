# UZI 报告链路：具体问题与修复准备

状态：**待执行方案**。2026-09-08 核对当前工作区代码及 09-07 留存实验；本轮未修改业务代码、未启动采集或模型请求、未重启服务、未提交推送。

后续用户已明确改为“先对齐原生，再测试剩余问题”。实际执行顺序以 [原生优先目标](/Users/bytedance/Documents/Cognitrade/plan/uzi-native-first-alignment-goal-20260908.md) 为准；本文件保留问题清单。Q1/Q2 等原生固有缺陷不再与接入对齐同时修改。

## 结论与证据边界

目前不是一个“LLM 过期”或“预检过严”的单点问题。直接链路有研究原文补采不完整、基金漏调度、模型排队/恢复及最终状态混用；即使这些修好，报告仍有评审汇总和特征计算错误需要处理。

- 原 E2E：基金 `020464 / 2026-09-02`，154/220 严格可用，66 槽缺口，10 股均未交付；9 股缺完整 Agent 聚合产物，海光存在源缺口和本地定性研究门禁。
- 同一批历史工件原样交原生检查，10 股仍全部不通过。不能解释成“相同输入只有 Cognitrade 失败”。
- 原生在线成功样本是 09-07 当前数据、单股海光及 Codex Agent 实际补证，不是原 Luna 网关与历史十股的同条件成功样本。
- 当前 HEAD 为 `fe9ae54`，但工作区已有大量未提交实现，不能用 HEAD 代表完整运行代码。后续部署/复验应记录工作区执行 hash、镜像 hash、数据库版本和实际模型。
- 保持固定 UZI 3.9.4 提交 `b3665e85b8666477038e11157d8e8feb0e006b76`；本轮准备不包含上游升级。

基线：[项目 E2E 记录](/Users/bytedance/Documents/Cognitrade/docs/uzi-e2e-defects-repair-20260907.md)、[原生实跑对照](/Users/bytedance/Documents/Cognitrade/docs/native-uzi-live-comparison-20260907.md)。

## A. 直接影响链路的待修问题

### D1 · 研究原文发现、正文归档与相关性判断覆盖不足【P0，实测＋代码确认】

**具体问题**

1. 原料/期货研究只接受三家商品交易所来源，巨潮发现仅接到行业/事件，无法充分利用公司年报、中报、ESG 的材料成本、供应链、套保披露。期货价格来源和公司成本研究来源被混在一起。
2. 宏观/政策相关性使用单一公司名或行业字符串，正文没有该精确词就可能被拒绝，缺少“政策主题→行业→公司”的有依据关联。
3. 浏览器能发现链接，但正文仍由 requests 下载；发现成功不等于原文成功入库。
4. `index.html` 路径规则可能误拒正文；通用 PDF 发现缺少“官方发布页→附件→发布日期/版本”的关联证明。不能通过简单取消历史验证修复。

**实证**：[historical_collection_children.json](/Users/bytedance/Documents/Cognitrade/workspace/reports/e2e-uzi-final-fund-20260907-pFehIf/historical_collection_children.json) 中，首波子任务 `01M1X6QJ3JC398A8VNHFZXSPTT` 的 `progress.research_archive` 为 attempted=50、succeeded=0、persisted_snapshot_count=0；宏观重试子任务为 10/0/0。这是研究归档阶段，不是说全体结构化数据都没落库。原生研究 A/C 实际找到了部分相应原文，但尚不能直接当作旧日期已合格输入。

**定位**：[research_archive.py:30](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:30)、[146](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:146)、[283](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:283)、[669](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:669)、[research_collection.py:196](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_collection.py:196)。

**最小修复**

- 复用 PIT 主体/行业上下文，按研究问题发现公司官方原文、宏观和政策原文；同文档按不同事实建立不同维度关系，不机械填满六维。
- 在已有归档器增加有界的官方正文/发布页附件适配；允许受控浏览器正文兜底，保留权限、截止、取消和原始失败信息，不绕过登录限制。
- 文档按 hash 复用，保存发布页及附件关系、发布日期、观察时间与版本依据；只产生新快照/Manifest，不修改旧冻结集合。

**验收**：公司成本/对冲披露可归入对应研究维度；无关公司不得通过；正文型 index 与真正列表页可区分；JS 正文、PDF 附件、缺发布时间、修订晚于截止均有夹具测试；报告读取阶段业务取数仍为零。

### D2 · 归档完成度仍用“两个不同 URL”代替证据覆盖【P1，代码确认】

**具体问题**：归档器一 URL 一文档，默认收到两篇即 ready；现有两 URL 就复用，少于两 URL 又进入补采。既可能重复下载一份已够用的原文，也可能把两篇无关原文误理解为研究满足。

**定位**：[research_collection.py:172](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_collection.py:172)、[299](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_collection.py:299)、[research_archive.py:138](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:138)、[223](/Users/bytedance/Documents/Cognitrade/research_system/market_data/research_archive.py:223)。

**最小修复**：分开原文库存、可引用事实/摘录、Agent 研究完成度；库存采集策略不冒充原生证据门。下载按内容复用，引用按不同事实绑定。不能把阈值从两 URL 简单改为一 URL 就宣称研究通过。

**验收**：同 URL 两个不同 finding 允许；两 URL 相同事实不自动满足；已有正文不重复下载；缺研究仅提示而不恢复旧启动硬门。

**特别说明**：报告启动前的“两不同 URL”硬门已经移除，Agent 输出已允许同 URL 不同 finding。本项是数据中心归档层残留，不是说之前的启动修复没生效。

### D3 · 基金与单股“要不要采集”判断不同，首波漏掉缺口【P0，实测＋代码确认】

**具体问题**：基金首波只看 `coverage.eligible`。部分 data_gap 因原生允许披露而被当成“不必采集”；父重试又只找本父任务最新子槽，没生成过子槽的缺口无法进入重试。真实遗漏了十股的同业、护城河、风险共 30 槽。

**定位**：[fund_holding_collection.py:3286](/Users/bytedance/Documents/Cognitrade/research_system/market_data/fund_holding_collection.py:3286)、[collection.py:2535](/Users/bytedance/Documents/Cognitrade/research_system/market_data/collection.py:2535)、[job_diagnostics.py:497](/Users/bytedance/Documents/Cognitrade/research_system/market_data/job_diagnostics.py:497)。

**最小修复**：基金与单股共享采集需求判断；分别记录可请求、需要派生实现、缺历史档案、不可适用。没有旧子槽不等于不可恢复。显式任务内为有实际能力的缺口建立子任务；缺适配/派生能力的给出准确修复原因。

**验收**：上述 data_gap 不再因 eligible 跳过；合格快照和治理审计型 partial 不重采；不重取基金持仓；冻结后通过新修订补齐；不能把全部 30 槽包装成可通过网络重试解决。

### E1 · 模型队列按请求公平，不按股票公平【P0，真实执行失败＋代码确认】

**具体问题**：一股可同时挂入多个研究/评委请求，匿名 FIFO 没有 run/股票级准入；其他股票排队约 600 秒时 HTTP 请求数仍为零。排队公平单位错位，不等于模型连续运算了十分钟。

**定位**：[uzi_llm_support.py:140](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_llm_support.py:140)、[338](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_llm_support.py:338)、[uzi_392_analysis.py:1026](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:1026)。

**最小修复**：传递 run 身份、按股票轮转、限制单股同时挂入的阶段，空闲容量可借用；统一线程池等待、limiter 等待、请求与退避的可观测预算。不要通过把预算起点挪到拿到模型槽后延长总耗时。

**验收**：五股同时入队时不会让一个股票长期独占；持仓并发保持 5、现有模型上限保持 4；取消排队后不会发请求；重试不重置截止；单股热缓存和十股冷启动分别计时。

### E2 · 并发限制 429 未进入正确恢复，逐次请求证据不足【P0，实测＋代码确认】

**具体问题**：已观察到 `gateway_concurrency_limit` 429，但瞬时恢复主要匹配连接异常与 502/503/504。Provider 将 HTTP 失败包装为文本，阶段事件缺 request ID、结构化错误码、失败原因和线程池等待时长，难以区分没发请求、被拒、超时和结构化输出错误。

**定位**：[uzi_llm_support.py:350](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_llm_support.py:350)、[408](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_llm_support.py:408)、[openai_provider.py:188](/Users/bytedance/Documents/Cognitrade/research_system/runtime/providers/openai_provider.py:188)。

**最小修复**：保留脱敏状态码、错误类型、Retry-After、request ID；仅明确瞬时并发限制做有界冷却和现有最多两次重试，不让 SDK 再嵌套重试。逐 attempt 记录 executor wait、limiter wait、HTTP、backoff、剩余预算。

**验收**：并发 429 后成功可恢复；401/403、额度/余额问题不盲重试；超过共同截止退出；不输出密钥和完整提示词。当前没有凭据过期证据，不以更换模型作为已确认修复。

### G1 · 原生机械自审、本地工作流完成度、冻结安全混为一个结果【P1，代码及实测确认】

**具体问题**：先调用原生 CHECKS，再追加 `agent-os-boundary` critical，最终合并为一个 passed/critical_count。界面和诊断容易把“本地完整 Agent 未完成”称为“原生 Python 自检拒绝”。

**定位**：[uzi_392_analysis.py:2994](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:2994)、[3051](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:3051)、[3085](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:3085)。

**最小修复**：保留兼容的最终交付判断，拆分原生检查、完整 Agent 状态、冻结证据/PIT 校验的来源与结果。逐条对齐固定 SKILL 和 Python 实现；本地安全约束明确标为本地，不伪装成原生规则。

**验收**：旧海光分别展示原生第 19 维 1 条 critical、本地六维 1 条问题；原生脚本 warning 不被描述为原生 critical；同 URL 不同事实可用。原生文字规范仍要求研究，不能仅因脚本不 abort 就把未完成分析标为完整报告，更不能供回测执行。

## B. 需要修正的报告质量与信号语义

### Q1 · 个人评委更新了，部分汇总和综合仍沿用旧值【P1，当前本地只读复现】

**具体问题**：当前投影已重算部分票数和派系，但 `long_active`、`short_consensus` 残留；综合生成和 panel 更新顺序也可能留下旧 `synthesis.school_scores`。海光保存样本经当前投影后 active=37、long_active 仍为41；short 摘要仍有没有事实依据的“审计有保留意见”，而个人意见已更正。不能把该残留文字当成公司真实审计事实。

**定位**：[uzi_392_analysis.py:550](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:550)、[1179](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:1179)。

**最小修复**：个人覆盖后构建唯一完整投影，再供叙事、综合、HTML 和信号读取；按各字段明确的 short/long、skip、风格权重口径重算，不强迫不同口径的数字相等。

**验收**：同时检查逐人方向、票数、long_active、派系、short 摘要、最终 HTML；旧审计断言消失；单纯检查总分或人数不算通过。

### Q2 · 原生特征提取把否定/缺失当成肯定/精确值【P1，当前本地只读复现】

**具体问题**：`非多头` 因字符串包含 `多头` 被判 true；PE 分位 `—` 被填为50。完整原生路径仍复现，旧的另一套 methodology 有正确处理不代表本路径已修。

**定位**：[uzi_392_analysis.py:929](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/subagents/uzi_392_analysis.py:929)、[score_fns.py:334](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts/lib/pipeline/score_fns.py:334)。

**最小修复**：使用显式布尔/枚举与真实指标，缺失值保持未知并避免进入依赖它的规则；覆盖规则引擎内部二次提取等所有消费者，不能只改 Agent 提示词的一份 features。

**验收**：真实多头、非多头、空值分别正确；缺失 PE 分位不变50、不生成精确估值证据；修正通过版本化适配记录原值和依据。保持 vendor 固定提交，不静默更换旧 Manifest 的实现，明确记录相对上游的确定性缺陷修正。

### Q3 · 作战价位来源标识不准确；改价位属于策略契约变更【P1，当前代码确认】

**具体问题**：当前 dashboard.battle_plan 确实来自现价×0.92/0.85/1.25，本地回测还校验这些比例。它对齐固定版原生模板，但不等于 Agent 独立研究产生的入场/止损/目标。

**定位**：[score_fns.py:1399](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts/lib/pipeline/score_fns.py:1399)、[report_conditions.py:111](/Users/bytedance/Documents/Cognitrade/research_system/strategies/report_conditions.py:111)。

**最小修复范围**：明确标识“原生模板条件”、原始字段路径、锚点、公式/提取版本、报告 hash；保留当前实际评级许可及回放约定。此次准备默认不改价位算法。

**边界**：若要改成 Agent 自主作战计划，必须另行明确权威输出与新策略版本，不能把 buy_zones 偷换进来、改写历史信号或称为无差别原生对齐。

**验收**：UI、报告说明和执行记录不会声称模板价位是 Agent 定价；旧策略 hash/历史结果不变；未通过交付及无买入许可的报告仍不可新开仓。

## C. 能力限制，不应包装成普通代码重试修复

### H1 · 第 19 维缺少可验证历史档案【P0 验收约束，不保证能补齐】

本地当前能力是 current_only，缺少可回建字段。原生 medium 的真实空数据检查不会因补一段评论解除。原生当前日返回的登录提示和空列表虽使外层 data 非空，却没有真实持仓，不能移植为 09-02 的历史事实。

**定位**：[historical.py:178](/Users/bytedance/Documents/Cognitrade/research_system/market_data/historical.py:178)、[原生 self_review.py:146](/Users/bytedance/Documents/Cognitrade/domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts/lib/self_review.py:146)。

**准备动作**：先区分未归档、源请求失败、登录受限、没有适配器、已核实为空；若有实际公开历史档案，验证主体、日期和版本再接入。需要新账号权限则停止请求用户授权。当天真实数据通过用户显式采集归档，订阅保持关闭。

**验收**：没有历史证据时仍准确阻断并指向能力缺口，不伪造 data/error、不给空对象塞说明来绕门、不用今天组合回填过去。即使 D/E 类代码全部修好，这一项也可能使原历史 medium 报告继续无法正式交付，应如实报告。

## D. 已有保护与需要补验的风险

以下不再当作“当前已确认未修”的故障重复实现：

- 冻结引用丢失、证券别名假缺口、GB 级快照反复写入已有修复和验证。
- 启动前独立 URL 数硬门已移除，研究缺口为 advisory；最终 Agent 同 URL 不同 finding 已支持。
- 源缺口误驱动模型重写、跨轮耗尽保护、同波失败聚合、结构化 ValidationError 分类已有修复。
- 报告后额外 LLM 进度摘要已修，但最后十股实测早于该部署，不能声称新版本已通过真实模型批次。
- 当前冻结评分作用域已禁原生隐藏基金持仓补拉，不把新原生实验的网络扫描再算成本地复现。应增加大基金列表/所有业务入口零调用测试；禁采产生的零/false须检查是否误表达为“已证明没有量化持有”。
- 本地 `/100` 字距已有修复和测试，不直接照搬原生截图报新故障。
- 手机 UPSIDE 溢出、外部资源慢导致附加图片/PDF 导出等待，是需要在本地实际页面复验的风险；本轮没有重新复现本地浏览器故障，先补验再决定修复范围。附图失败与主报告交付分开。

## E. 实施顺序与验收关口

1. **小范围基线回归与诊断**：把上述固定失败样本纳入必要测试；分层展示 G1，记录 E2 诊断。确认活跃任务、工作区、镜像/数据库及版本，不中断用户任务。
2. **并行修两个主要阻塞面**：E1/E2 模型准入与恢复；D1/D2/D3 数据中心归档与采集范围。保持持仓5/模型4、原有总截止及最多两次瞬时重试，不靠扩预算。
3. **报告质量**：修 Q1/Q2，补 Q3 来源标识及 D 节已有保护的回归；版本化适配改动纳入执行身份，新任务不复用旧计算结果。
4. **先验证模型请求链路**：使用实际配置模型和网关做最小结构化请求，记录队列、HTTP、输出校验。不得用 Codex 子 Agent 成功代替服务模型验收。
5. **显式数据任务**：当天探针按实际执行日取数；历史 `688041 / 2026-09-02` 单独补证验证，使用新 Bundle/Manifest。两种日期输入不可混用。只重试具体可恢复缺口。
6. **单股完整 Agent→基金分支**：用新固定输入先跑一股，分别记录实际模型执行和证据检查。H1 若仍无真实历史档案，记录原生阻断，不再反复整批调用模型碰同一硬缺口。合格输入和单股执行满足后，再扩展 `020464` 十股完整工作流。
7. **报告→回测**：只有真正交付且有合格执行计划的报告才进入真实历史回放，仍使用 `2026-09-02—09-04` 的已落库实际行情；不修改报告制造交易。报告阻断、系统失败、取消、无买入许可、引擎已跑但零成交、真实成交分别记录。当前日报告没有未来行情时不得伪造对应回测。

本地只跑采集/归档、基金协调器、LLM 控制/恢复、原生投影与交付、报告条件和相关前端必要回归；完整回归交 GitHub Actions。最终交付机器结果与中文报告，记录输入/输出 hash、任务 ID、代码/镜像/模型版本、耗时和未解决限制。

## 执行边界

本文件是修复准备，不代表上述事项已实施或已取得新的 E2E 成功。保留全部既有修改和不可变历史数据；自动补采/订阅保持关闭；不自动 commit/push。若需要更换模型、获取新权限或改变作战条件策略，先单独确认。
