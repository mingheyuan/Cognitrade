# 2026-09-16 UZI 来源与执行修复记录

状态：本轮最小修复、真实链路尝试和新 PR 已完成，**完整 E2E 未通过**。报告 4/10 交付，1 份持续生成超时、5 份上游过载；回测实际执行、0 成交。来源语义仍有待真实修订复验的缺陷，不能把代码回归通过当作报告质量通过。
范围见 [执行方案](../plan/uzi-todo-repair-20260916.md)，验收项见 [TODO](TODO.md)。

## 已实现的最小改动

- 巨潮行业分类/估值接口保留 provider、查询条件和数据日期，移出研究原文列表。旧冻结快照只增加只读分类视图，原数据与 hash 不改写。
- 数据中心和报告共享原文入口检查，导航/列表/API 文档页不会进入必然失败的正文补采。真实文章新 URL 仍走现有补采、归档和新 Manifest 修订。
- 模型提供 URL 与宿主证据 ID 冲突时，以模型声称的 URL 识别待核实来源，不偷偷改成旧 ID 指向的 PDF。未知新 URL 不自动认可；明确归档的 URL 才绑定归档元数据。
- 报告输入明确结构化快照 ID/hash、字段路径、采集状态与研究原文的区别。数据中心研究和报告采用同一窄范围来源冲突检查：明确来自本地快照、查询结果或采集状态的陈述不能冒充公司文档；已知文档发布日期之前不可能记载其后实际发生的收盘行情。不检查预测/情景，也不增加两个不同 URL 的要求。
- 真实传递复查发现独立 Snapshot Artifact 原先丢失数据中心 ID/hash；已增加可选字段并传递原值，保留旧工件兼容。采集工件缓存版本升级为 3，避免继续复用旧投影。前端可查看原始快照编号和内容 hash，不与 Artifact ID 混用。
- 正文阶段强调综合已有研究结论，不重复逐位评委与整份报告；不加硬字数限制、不减少原生问题、不延长 600 秒。
- SDK 收到 `turn/completed` 即结束消费并关闭订阅，不等待额外 EOF。超时/取消前保存进展快照，结束清理产生的事件不能覆盖原始失败状态。
- GitHub Actions 的隔离逐模块回归改用 pytest，避免纯函数测试被 unittest discover 当作 0 项成功跳过。

## 原生要求与宿主边界

| 层次 | 规则归属与本轮处理 |
| --- | --- |
| UZI SKILL 研究要求 | 固定 3.9.4 / `b3665e85b8666477038e11157d8e8feb0e006b76`。每维至少两条证据；同一原文可支持不同事实；无依据内容应明确未知。未升级或改写原生分析/渲染代码。 |
| UZI 程序自审 | 原生 critical 阻止交付、warning 披露；与 Agent 是否完成研究分开，程序结构通过不能证明事实正确。 |
| 本地输入审计 | 权限、不可变 hash、撤销和历史可见性属于宿主约束，不宣称是原生规则。初始目录不是永久边界，但更新需要显式核实和新输入版本。 |
| 本地来源审计 | 导航/正文分类、归档版本绑定及上述有限冲突检查属于宿主审计，不是原生现成校验器，不等同于通用语义核验。有效同文档多事实有正向回归。 |
| SDK 执行 | 超时、取消、无最终回答不是原生 critical。诊断区分无输出、输出未完成、最终回答已到达和清理终态。 |

原生仍需要 Agent 实际检索、阅读、关联和自查；本项目将业务取数放在数据中心，报告阶段
只读固定资料。新资料沿“显式补采 → 核实归档 → 新 Manifest → 局部恢复”进入，因此不应
笼统声称执行环境与直接使用 UZI 完全一致。

## 中科飞测上次超时的已知事实

基线运行 `01M2HTG4YW58Q13S2ANJJD65AD` 的 `narrative_core`：输入 153,610 字符，
28941 个流事件，累计输出 29,238 字符；没有记录到最终回答完成。约 600.079 秒的
`turn/completed` 出现在超时中断清理阶段，不能据此声称报告已完成但系统丢失。
目前支持的结论是“持续输出但预算内未完成”，不是 API Key 过期；究竟为何生成如此多内容，
旧 trace 不足以再细分。终态处理修复有确定性回归，但不能声称它就是这次真实超时的根因。

SDK 官方协议把 `turn/completed` 定义为 completed/interrupted/failed 的终态，
`item/completed` 提供完整条目。本轮据此补齐终态/中断前诊断，
参见 [OpenAI App Server 文档](https://developers.openai.com/codex/app-server)。
诊断只存时长、数量、有限枚举与 hash，不记录凭证、原始回答或思维链。

## 必要本地回归

- 历史适配器与报告证据补采：24 passed。
- 来源绑定、来源语义、诊断契约：81 passed。
- SDK、正文综合、原生输出边界与分析：99 passed，11 subtests passed。
- 来源类型、数据中心研究与公司共享原文：106 passed。
- 最后补入数据中心共享校验后的来源传输/元数据回归：46 passed（修正了测试视图预期，未取消正文/hash 校验）。
- 最终来源/SDK/诊断/补采组合：224 passed，11 subtests passed。
- Snapshot 实际采集工件、分析入口、历史降级回归：71 passed，27 subtests passed；缓存版本补充复查 25 passed；前端快照审计展示 4 passed。
- `git diff --check` 与 Finance Artifact 生成契约检查通过。
- Actions 首轮完整发现了 4 个失败断言。定向复现确认：同输入缓存用例未固定原生随机评委话术；Agent 恢复用例受 `CI=true` 的原生 CLI 降级规则影响；异步回测用例用多线程共享单一内存 SQLite 连接，高频轮询还触发原生进程崩溃。分别固定测试话术、显式选择 Agent 测试环境、改用与部署相同的文件 SQLite 独立连接；未放宽断言或修改业务门禁。`CI=true` 下 68 passed、4 subtests passed，完整 CI 再交 Actions。
- 本地 E2E 观察脚本中断现在记录 `observer_interrupted`，不会误记为后台业务取消或已完成；保存原任务 ID 继续观察。

以上测试集合部分重叠，不相加成独立用例总数。完整回归交 GitHub Actions。
未新增数据库迁移；部署前只读检查数据库版本为 `20260906_0006`，任务表无活跃采集、
编排或回测任务。真实任务、输入 hash、耗时及剩余问题在实际执行后补充，不预填成功。

## 实际部署与 SDK 探针

API 镜像已重新构建并部署，镜像 config digest 为
`fc7ba19cd6ea0e8d97b85ed9f726ccbe81c64c13ef77d18ddf409274e0a0a568`。
在容器实际业务用户下使用原配置运行 SDK 真实结构化探针：ChatGPT 登录有效，
`gpt-5.6-luna` 可用，调用完成，返回 `{"ok":true}`。模型调用耗时约 11.73 秒，
含账号/模型核查全程 16.96 秒，终态 `completed`、`generation_state=final_received`，
运行时已关闭。SDK/CLI 均为 0.154.0。

探针证据保存在容器 `workspace/reports/uzi-todo-repair-20260916/sdk-probe.json`。
这只证明新 SDK 链路可用，不替代完整报告或回测验收。

## 真实采集、冻结输入与报告

- 当日探针 `01M2MJFD95RC97AWV1Q58D7BSZ`：`688012.SH / 2026-09-16`，行情和事件 2/2 成功落库，服务端耗时 93.575 秒。
- 行情快照 `01M2MJJ8KK76DZKFS7HA73DBJ2`，覆盖 360 个观测日，最后一根真实行情为 `2026-09-16`，hash `da450b4b91e9e6261fecbe95bca23244bc0f45f3859fc303b1528a48a2d0a27e`。
- 历史基金补缺任务 `01M2MJKMVFADAX0PXA1QVYB6SG` 已完成，服务端耗时 1,211.874 秒（约 20 分 12 秒）。复用父持仓，追加一次 11 槽定向补采后仍为严格可用 161/220、缺口 59（可重试 11、不可恢复 48）；`partial / ready_degraded`。没有为了通过而将缺口改成合格。
- 中科飞测原运行 `01M2HTG4YW58Q13S2ANJJD65AD` 的 22 个真实冻结快照经新投影离线检查：快照集合 hash `e8ac24272d5fc7aa450faded8ed43c7d656c7cdb88bdb98f1a60434e49fcf778` 未改变，六维来源 ID/hash 完整，业务取数/模型调用均为零。这是传递检查，不是重新生成报告。
- 为先部署遗漏的来源定位传递修复，暂停了一次本地 `all` 验收驱动；未取消后台业务任务，随后以 `collect` 阶段继续观察同一任务。
- 采集结束且任务表无活跃任务后部署 Snapshot 来源传递修复。报告实际 API 镜像 config digest 为 `ca9d93ad82971483751e8b533b89bf7a3707ee6c8647cd4646b8e7753c85219f`，Web 镜像 ID 为 `d85e8e41da6ed9c21032b573aa30f73f7ce80ccdb860f722a9e4c360bc1661ff`；数据库版本不变。
- 冻结确认耗时 5.227 秒；持仓未变，基金 Bundle 复用 `01M1NX6SGD66QW8H72HJ1JMHGE`，十只股票固定为本次新候选 Bundle，均已逐一核对 ID/hash。
- 报告任务 `01M2MKVFYD5BC8GAF7N3C8Z0AR`，批次 `01M2MKVK96SSV8A9ZHETY6H7TM`，Manifest `01M2MKVH2G87NEH76XXY96YEF1`，hash `95bb4a8062068adf00a7912327c3253bf3cd5b715fa3c456cf9458fa2f8f225d`。实际 `gpt-5.6-luna / medium / full`，固定 UZI 提交不变。
- 实际海光运行的 22 个 Snapshot Artifact 均保留了原始快照 ID/hash。报告输入预检通过，研究原文缺口仍作为明确提示保留，没有重新加成独立启动硬门禁。

PR：[#33](https://github.com/mingheyuan/Cognitrade/pull/33)，草稿，不自动合并。

## 内容审计发现与补充修复

本次长川 `01M2MKVSEZZ3ZM572E08239WX3` 已通过原生交付门（0 critical、3 warning），
但人工定向审计不通过：铜价/期货快照已改为独立缺口披露，未继续直接绑定半年报；
`7_industry.evidence[0]` 的行业快照数值及 `15_events.evidence[0]` 的有界查询零条结果
却仍附着公司 PDF。说明原生结构自审通过不等于来源语义正确。

针对该真实样本，补充显式“固定快照显示／本次查询结果”来源冲突检查，并验证旧缓存
也会重新检查、只重做 B 组（A/C 复用）。84 项必要回归通过。此补充不在本次完整
批次使用的镜像中，不将后补代码冒充该报告实际使用的版本。人工审计证据单独保存在
`workspace/reports/uzi-todo-repair-20260916/e2e/report-content-audit.json`。

海光 `01M2MKVRXWH55B139AZ080MFRN` 同样通过交付门，材料/期货引用已指向公司采购和
套期业务披露；关联品种未识别则明确表述为本次固定资料的限制。该检查仅覆盖此前
问题位置，不宣称全文所有事实都已被证明。

长川正文模型调用 92.776 秒，最终输出 4,856 字符，SDK `final_received / completed` 且
`runtime_closed=true`。这是真实正常终态样本，但不证明所有股票均不会再超时。

寒武纪 `01M2MKVRD4Z8PZR7GFNDBVMV2T` 随后在正文阶段超时：排队 8.388 秒，模型请求
600.076 秒；输入 148,988 字符，截止时已输出 33,756 字符，仍没有最终回答。
`stop_snapshot.generation_state=output_incomplete`，最后一次输出在 600.041 秒，
中断已确认，终态 `interrupted`，运行时已关闭。这排除了“早已完成但本地一直等 EOF”
这一解释，也不是鉴权失败；但未证明模型为何持续生成长文。收敛提示未完全解决问题，
TD-03 保持未完成，不延长超时、不修改原生评分或伪造完整报告。

北方华创的事件证据也将公司文件披露与本地有界查询结果混为同一 PDF 来源，新检查能够
识别该明确冲突。中微定向检查未发现同类模式，仍不等同于全文事实核验。

新镜像下对长川原始 22 个快照做了一次独立研究阶段复验（不是新报告）：A/C 组复用，
B 组旧错绑缓存被拒绝，重新请求模型时返回 `serverOverloaded`，短退避次数耗尽后停止；
再次阶段调用正确返回 `stage_budget_already_exhausted`，未额外发起整股重跑。总耗时
20.433 秒，2/3 组完成，最终 `incomplete`。原快照集合 hash
`658ce71ac048efb3ccec0454ec7397baf3c55497e3e3bf0de4033e10f095ee1e` 未变。
因此已证实“旧错绑不能直接复用”，尚未证实“修订报告已正确交付”；TD-02 保留未完成。

## 报告终态与 HTML

本轮报告 08:03:11—08:45:14 UTC，总耗时 **2,522.724 秒（42 分 02.724 秒）**。
`completed_partial / final_partial`，覆盖率 40%；无人工取消、无仍在运行的股票。

| 股票 | 运行 ID | 本轮终态及直接原因 |
| --- | --- | --- |
| 中微公司 688012 | `01M2MKVQ9GS9JCZ42EJBF50AHK` | 原生门允许交付，HTML 已生成 |
| 北方华创 002371 | `01M2MKVQV6Q2RP3A3H6S1CPBJ0` | 原生门允许交付，HTML 已生成；事件来源错绑待修订复验 |
| 寒武纪 688256 | `01M2MKVRD4Z8PZR7GFNDBVMV2T` | `narrative_core` 持续输出，600 秒无最终回答 |
| 海光信息 688041 | `01M2MKVRXWH55B139AZ080MFRN` | 原生门允许交付，HTML 已生成；此前映射状态错绑未再观察到 |
| 长川科技 300604 | `01M2MKVSEZZ3ZM572E08239WX3` | 原生门允许交付，HTML 已生成；行业/事件来源错绑待修订复验 |
| 拓荆科技 688072 | `01M2MKVT1T93MN4FHTFQNEXA0T` | 定性研究及正文通过，买入区技术/游资/成长阶段 `serverOverloaded` |
| 华海清科 688120 | `01M2MKVTHF248CMR31MSHP96Q2` | 买入区价值/游资阶段 `serverOverloaded` |
| 中科飞测 688361 | `01M2MKVV1CG2CV29R24WWVD8K4` | 正文 125.879 秒完成；买入区成长/技术阶段 `serverOverloaded` |
| 中芯国际 688981 | `01M2MKVVF4FKW1H99S1KJN9DDP` | `dimensions_16_20` 阶段 `serverOverloaded` |
| 华峰测控 688200 | `01M2MKVVZ05Y6DHZCC4NYC5PTQ` | 买入区价值/游资阶段 `serverOverloaded` |

拓荆原接口 URL 冲突及中科原正文超时在对应阶段未复现，但二者本轮均**未交付整份报告**。
验收主因分类是 6 个系统执行失败，不是 6 个新的独立原生内容阻断；缺失 Agent 输出仍会
在后续原生/完整 SKILL 门体现为 critical/incomplete，两层记录均保留，不相加成 12 份失败。

四份 HTML 已核对标题、历史截止日/实际生成日提示、评级和作战价位的传递。原生渲染
服务耗时分别约 41–66 毫秒，HTTP 读取约 0.80–1.12 秒；不是本轮性能瓶颈。正文页面未
逐项铺开研究 evidence，完整引用保留在 HTML 内嵌分析数据中，因此必须结合结构化分析
审计，不能因为可见正文没有那句话便判定来源错绑消失。本轮未做全页面像素级视觉验收。

已交付报告均为原生 0 critical、3 warning，其中包含原生 `industry_mapping_sanity` /
`metals_materials` 的 `NoneType` 检查异常和 DCF 为零提示；均需披露，不宣称原生检查器
完成了所有事实校验，也没有为了通过而修改固定的原生实现。

## 真实报告条件回测

- 行情任务 `01M2MP9GG0CTG77TAKC0G558HJ`：十只股票的 `2_kline / 2026-09-04`，复用已落库数据，10/10 成功，服务端 0.509 秒。没有要求回测行情采齐 22 维。
- 回测 `01M2MPK1C78C88CP4EWAFXNX3G`：`2026-09-02—2026-09-04`，`report_conditions_v1`，仅本轮报告集 `01M2MKVFZ0PQE46KQ3D2D8BJ5K`，`existing_only`，实际完成账本，服务端 0.202 秒。
- 回测使用落库行情并固定实际选择清单，hash `5224ec3297412c99ad31af27ceca2713df8baed780b67aa748d84cccefaf87a6`。驱动未强制绑定刚采集任务的精确 Snapshot ID，不应声称两者完全同一集合；以实际回测清单为准。
- 9 月 2 日：10 只均无已生效计划，不使用同日未来数据。9 月 3、4 日：每天 4 条 `native_verdict_no_new_entry`（原生评级“谨慎”），6 条 `report_plan_not_executable`（报告执行失败）。合计 30 条逐日判断，完整股票池保留。
- 0 笔交易、0 费用、0 持仓，初始及期末现金/权益均为 100 万。没有改评级、放宽门禁、加 MA 策略或制造交易。

该结果证明条件回放引擎和账本实际运行，**不证明真实成交分支已验证，也不证明十份报告
全部合格**。已有两份来源审计问题仍成立，本次回放属于诊断结果，不应作为可靠投资业绩。
总体验收保持 `pipeline=blocked / native_report=partial_delivery / replay=completed_without_trades`。

## 最终部署、CI 与证据

提交 `4988744` 的 [Actions 35074315889](https://github.com/mingheyuan/Cognitrade/actions/runs/35074315889)
四项自动检查全部通过：完整后端、固定原生 UZI、前端及 SQLite/PostgreSQL。手动真实
数据源 smoke 按工作流条件跳过，不能算作通过。本地真实验证结果另见上文。

全部业务任务和独立探针退出后再次只读确认无活跃采集/报告/回测任务，才部署补充检查；
保留原有 paused 任务，未恢复或取消它。最终 API 镜像 ID
`b79d5410d590e65da047a87550328665c68f51bae0af1cd022ea73882eb239f3`，
config digest `7f799cbb806bd222acd7b9a0afa0ca6f31c949ce5cc7b1676df283986dcb3393`，
服务健康，数据库仍为 `20260906_0006`。该部署不会重写本轮旧报告。

可随 PR 检查的脱敏摘要见 [机器可读验收记录](uzi-todo-repair-20260916.json)。完整本地证据
位于 `workspace/reports/uzi-todo-repair-20260916/`：`e2e/e2e-result.json`、
`e2e/report_acceptance.json`、`e2e/report-content-audit.json`、
`e2e/report_html_evidence.json`、`e2e/backtest.json`、`source-repair-probe.json`。
原始大体积报告/研究数据不提交到仓库。

后续最小范围：服务恢复后只修订长川/北方有问题的研究阶段并核查 HTML；对正文持续生成
追查有效输出与收敛机制；其余五只过载失败复用合格阶段恢复。保持自动补采关闭，不扩大
并发/超时，不将这些剩余工作标成已完成。

## 后续：正文写作要求对齐原生

对照固定提交的 `SKILL.md`、`references/task4-synthesis.md`、`run_real_test.stage2()`
和 `agent_analysis_validator.py` 后确认：原生由宿主 Agent 读取已有 JSON 产物，完成
Task 4 并写回 `agent_analysis.json`，再由脚本合并/校验/渲染；没有对应本项目的
`narrative_core` 600 秒 SDK 请求，也没有专门保证正文超时自动续写成功的机制。
SKILL 中的采集器 90 秒和 stage1 resume 不能当作正文生成的恢复协议。

本地遗漏的是 Task 4 参考文档的辩论语言与金句写作说明。已将该段固定原文接入实际
正文请求，包括每段 ≤80 字、冲突性表达及金句篇幅；不复制旧参考中的不同选角、
前五风险或直接写 `synthesis.json` 的旧流程。这些仍以主 SKILL 的选角、Top 3 风险、
`agent_analysis.json` 写回为准。

需要区分：原生 Python 校验器本身只要求至少三轮，没有把 80 字设为代码硬门禁。
因此此次仅补写作/自查指导，不新增 maxLength/maxItems 拒绝规则、不裁剪输出，
不新增模型阶段，也不改模型、推理强度、预算或冻结证据。提示版本改变只使正文缓存
失效，其他合格阶段继续复用。

此修改晚于上文真实 E2E，没有改变其记录，也尚未真实验证能否消除寒武纪超时。
“原生写作要求已补齐”不等于“执行方式完全等同宿主 Agent”或“原生不会超时”。
必要回归：`test_uzi_native_agent_protocol`、`test_uzi_narrative_panel_context`、
`test_uzi_synthesis_qualitative_context` 共 18 passed，覆盖实际请求、原生职责保留、
不新增长度硬门禁和仅正文缓存失效。此后续补丁尚未重建部署，完整回归交 CI。
