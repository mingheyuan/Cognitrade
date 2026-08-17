# Cognitrade 2.0 中文版本说明

发布日期：2026-08-18

## 发布概要

Cognitrade 2.0 的核心升级，是把研究报告、策略信号、历史回测、机器验证、人工确认和
模拟交易连接成可审计的策略治理闭环。同时，内置 UZI 股票研究能力以 UZI-Skill
`main` 分支提交 `b004d7a988ca53a4805bf36784af091fa1209f22` 为唯一基准完成严格
对齐，上游版本号仍为 `3.9.4`。

这次发布不是仅更换报告样式。它既新增了 2.0 的策略回测主线，也同步并验收了 UZI 的
生产 Python 运行时、数据采集器、方法论、commands、skills、报告模板、渲染辅助器和
Cognitrade 宿主边界。完整的 `v1.0` 差异见
[《Cognitrade 1.0 → 2.0 中文升级报告》](./UPGRADE_REPORT_1.0_TO_2.0_CN.md)。

## 核心升级：从手工模拟盘到策略回测治理闭环

`v1.0` 已有独立的 A 股手工模拟盘，包含服务端账本、FIFO、T+1、费用、整手校验和
幂等订单，但没有版本化策略、历史回测及晋级治理。2.0 新增完整链路：

```text
不可变策略版本 + point-in-time 基金/UZI 日报 -> 同版本四态信号 -> 回测预检
-> Hikyuu 技术事件 + 确定性成交账本 -> 指标/基准/lineage
-> 机器验证 -> 人工 Verified -> 人工 Promoted -> VeighNa 模拟交易
```

- 两种正式回测模式：`fund_only` 回测基金本体，`holdings_portfolio` 回测披露持仓组合。
- `fund_only` 区分交易所 ETF/LOF 与场外基金 NAV，分别处理交易费用和申购赎回确认日。
- `holdings_portfolio` 以报告日持仓和权重为输入，执行 A 股费用、T+1、单股上限和偏离再平衡。
- Hikyuu 负责技术事件；Cognitrade 的确定性账本负责成交、持仓、现金、费用与净值，职责不混写。
- 每笔成交保存策略版本/内容哈希、信号、日报和市场数据 lineage，缺失信号不会被静默忽略。
- 回测提供预检、异步执行、取消、重试、内容寻址结果、确定性复跑以及多组基准。
- 回测服务关闭时会等待 worker 及其租约心跳完整退出，再允许数据库连接池销毁，避免任务
  尾部写入与 SQLite/PostgreSQL 生命周期发生竞态。
- `completed` 终态只在结果、成交 lineage 和内容寻址引用全部落库且 worker 退出后对外
  可见，机器验证不会再读取事务中的半成品结果。
- 内存 SQLite 的 `StaticPool` 会让 worker、租约心跳和状态轮询共享同一物理连接；2.0
  仅在该配置下串行化 Session，避免并发事务释放彼此 savepoint。文件 SQLite 与
  PostgreSQL 继续保持连接级并发。
- 文件型 Tool 资源租约按资源键为获取、续约和释放增加线程内及跨进程互斥，消除旧 owner
  在租约过期回收的瞬间覆盖新 owner 的竞态；高负载下的短租约心跳不再把互斥调用误报为
  执行失败。Redis 租约继续使用原有的 owner-token 原子脚本。
- 机器检查覆盖无前视、lineage、复跑一致性、样本、信号、费用、回撤、收益、Sharpe、
  超额收益、换手和费用影响；通过后仍必须人工确认并晋级。
- 只有与账户、基金、模式精确绑定的 Promoted 版本可沿策略链路下模拟单；当前该链路
  只接受 `holdings_portfolio` 的 A 股持仓委托，不代表实盘自动交易。

## 严格对齐范围

- 22 个 UZI fetcher 与完整 pipeline registry。
- 73 个上游数据源定义及字段契约。
- 20 个独立 commands。
- 23 个机构研究和决策方法实现。
- 66 席评审团，包括 64 个多头 mandate 和 2 个独立空头 mandate。
- UZI 3.9.4 原生 HTML 报告模板、panel cards、机构建模卡片和 SVG 组件。
- investor-panel、lhb-analyzer 和 trap-detector 独立 skill 资源。

## 数据采集与稳定性

### 子进程硬超时

新增 `process_runner.py`，pipeline 使用 `multiprocessing spawn` 运行采集任务。单个数据源挂起、超时或无法退出时，父进程会终止对应 worker，不再拖住整份报告。

默认超时：

- 单 fetcher：120 秒，可通过 `UZI_PIPELINE_FETCHER_TIMEOUT` 调整。
- 单 wave：300 秒，可通过 `UZI_PIPELINE_WAVE_TIMEOUT` 调整。
- 依赖 mini-racer 保持串行分组，避免数据源竞态。

### 缓存和断点续跑

- 恢复缓存必须具有可解析的 `fetched_at`。
- 按 fetcher TTL 判定数据是否仍然新鲜。
- 无时间戳、已过期、fallback、missing 或 error 结果会重新采集。
- 恢复后重新计算完整性和 recovery artifact，不再沿用过期缺口。

## 财务、估值与数据真实性

- 修正年度值、MRQ 和报告期口径，避免将季度数当成年度数。
- 增强 MX 在财务、估值和定性数据上的 fallback。
- 改善自由现金流、现金流质量、估值及派生特征的取值逻辑。
- 护城河、情绪、FCF 等数据无法获取时保留 `null`，不使用 `0` 伪装成真实观测。
- 缺失数据会进入 data gap、证据链和质量降级说明。

## 66 席评审团

- 完整评审团统一为 66 席。
- 多头共识仅由 64 个多头 mandate 形成。
- Burry 和 Chanos 两个空头 mandate 进入独立 `short_consensus`，不再污染多头投票分布。
- 无有效分数时启用空洞共识保护，防止数据不足时生成虚假一致结论。
- one-liner 按上游 `long_active` 口径动态展示参与多头投票的评委数量。
- 基金持仓报告档位页面展示实际执行的 66 席，不再显示旧的 10/51 席口径。

## 报告呈现和宿主兼容

- 原生报告模板和可见渲染 helper 按上游提交逐字节保留。
- 缺失规则数据使用安全占位符，避免 `None` 导致页面异常或错误数值。
- 保留 Cognitrade 的浏览器兼容补丁，修复 Alpha Score 伪元素继承负字间距导致的文字重叠。
- HTML 与 PDF 共用同一份 UZI 可见报告，Cognitrade 运行元数据继续放在 ArtifactBoard 与响应边界。
- 上游子进程启动后，请求级雪球策略只传入 `19_contests` worker，不写入全局环境，不泄露给其他任务。
- 上游 `moat_total=None` 仅在旧 idea-screen 内部读取副本中兼容为 `0`，原始证据和报告缺口仍保持 `null`。
- UZI 评分和综合研判只消费 Cognitrade 已冻结快照；上游风格识别在基金经理样本不足时
  触发的隐式 AkShare 补采会在宿主评分作用域内禁用，避免回测/报告重算产生未记录外呼或
  在 Linux 分页请求中无限等待。作用域退出后采集模块会原样恢复，不影响正式快照采集。
- 已提供 `portfolio_holdings` 的收益归因、持仓再平衡、资产配置和组合批量研究请求只做本地
  意图解析，不再把组合方法描述误交给证券或基金主数据进行在线名称检索；冷缓存或供应商
  波动不会再阻塞请求入口。
- 即使未安装可选 VeighNa 引擎，模拟交易也会预先创建受控的 `veighna_runtime/.vntrader` 目录，避免 fallback 引擎初始化不一致，并确保后续启用原生引擎时不会回落到进程 HOME。

## 来源、完整性和 CI

- 所有 UZI source commit pin 统一为 `b004d7a988ca53a4805bf36784af091fa1209f22`。
- CI 使用该提交的 pristine checkout 做逐字节对比，并将 vendored 实现覆盖到上游测试树中运行。
- 源码树、command 树、分析资产、独立 skill 和报告渲染器均有 SHA-256 完整性锁。
- 完整后端 CI 按测试文件使用独立 Python 解释器，隔离 UZI/provider 原生依赖的进程级状态，
  同时保留全部测试清单和 faulthandler 崩溃诊断。单模块超过 180 秒未退出时会终止并在
  全新进程重试一次；普通断言失败不会重试。
- 更新 Artifact Schema、生成的 TypeScript 合同、测试快照、评估 fixture 和数据源配置。

## 验证结果

- Cognitrade UZI 回归：228 项通过。
- Cognitrade 完整后端套件：690 项通过，2 项跳过。
- 报告和 API 定向回归：40 项通过。
- 前端完整套件：14 个测试文件、44 项测试通过。
- 前端 ESLint、TypeScript 和 Vite 生产构建：通过。
- vendored scope 与 pristine UZI checkout 逐字节检查：通过。
- 完整性报告：`valid=true`。
- 上游确定性测试集合共 710 项；本地为 709 项通过、1 项可选依赖测试跳过，CI 安装完整依赖后执行全部集合。上游另有 2 项 `multiprocessing spawn` 微基准使用 1 秒级墙钟阈值，在 macOS 和 GitHub 托管 runner 上均会受进程启动抖动影响；CI 保留其行为覆盖，但改用 Cognitrade 的 5 秒生产启动预算分别验证硬超时回收和串行组调度，不放宽生产 fetcher 的 120/300 秒超时策略。

## UZI 对齐的升级影响

- UZI 方法论版本仍为 3.9.4，本次 UZI 对齐本身不增加数据库迁移；但从 Cognitrade
  v1.0 升级整个 2.0 仍必须执行策略域迁移，详见独立升级报告。
- 依赖 artifact `source_commit` 常量的调用方需接受新的 commit pin。
- 旧的无 `fetched_at` 缓存将被当作过期数据重新采集。
- 以前使用 `0` 表示未知的部分字段现在会保持 `null`，下游应按缺失数据处理。
- 完整报告的评审团实际为 66 席；独立 quick-scan 仍按其上游 command 契约使用 Top 10。

## 明确边界

Cognitrade 保留的是 UZI 生产能力，而不是对上游 GitHub 仓库的完整镜像。上游安装器、插件市场清单、hooks、通用文档和测试源码不进入生产 vendor；这些文件不参与 Agent OS 运行，并在 source mapping 中明确归类为分发或 CI 边界。

上游模板和源码中仍有少量历史性“50/51 位评委”静态文案或注释。为保持逐字节一致，vendored 文件不在 Cognitrade 内单独改写；Cognitrade 自有的 one-liner 和前端档位已使用真实 64/66 席口径。
