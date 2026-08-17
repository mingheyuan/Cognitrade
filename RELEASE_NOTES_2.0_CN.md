# Cognitrade 2.0 中文版本说明

发布日期：2026-08-17

## 发布概要

Cognitrade 2.0 完成了内置 UZI 股票研究能力的严格对齐。本次以 UZI-Skill `main` 分支提交 `b004d7a988ca53a4805bf36784af091fa1209f22` 为唯一基准，上游版本号仍为 `3.9.4`。

这次发布不是仅更换报告样式，而是同步并验收了 UZI 的生产 Python 运行时、数据采集器、方法论、commands、skills、报告模板、渲染辅助器和 Cognitrade 宿主边界。

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
- 即使未安装可选 VeighNa 引擎，模拟交易也会预先创建受控的 `veighna_runtime/.vntrader` 目录，避免 fallback 引擎初始化不一致，并确保后续启用原生引擎时不会回落到进程 HOME。

## 来源、完整性和 CI

- 所有 UZI source commit pin 统一为 `b004d7a988ca53a4805bf36784af091fa1209f22`。
- CI 使用该提交的 pristine checkout 做逐字节对比，并将 vendored 实现覆盖到上游测试树中运行。
- 源码树、command 树、分析资产、独立 skill 和报告渲染器均有 SHA-256 完整性锁。
- 更新 Artifact Schema、生成的 TypeScript 合同、测试快照、评估 fixture 和数据源配置。

## 验证结果

- Cognitrade UZI 回归：228 项通过。
- Cognitrade 完整后端套件：685 项通过，2 项跳过。
- 报告和 API 定向回归：40 项通过。
- 前端完整套件：14 个测试文件、44 项测试通过。
- 前端 ESLint、TypeScript 和 Vite 生产构建：通过。
- vendored scope 与 pristine UZI checkout 逐字节检查：通过。
- 完整性报告：`valid=true`。
- 上游确定性测试集合共 710 项；本地为 709 项通过、1 项可选依赖测试跳过，CI 安装完整依赖后执行全部集合。上游另有 2 项 `multiprocessing spawn` 微基准使用 1 秒级墙钟阈值，在 macOS 和 GitHub 托管 runner 上均会受进程启动抖动影响；CI 保留其行为覆盖，但改用 Cognitrade 的 5 秒生产启动预算分别验证硬超时回收和串行组调度，不放宽生产 fetcher 的 120/300 秒超时策略。

## 升级影响

- UZI 方法论版本仍为 3.9.4，不需要数据库迁移。
- 依赖 artifact `source_commit` 常量的调用方需接受新的 commit pin。
- 旧的无 `fetched_at` 缓存将被当作过期数据重新采集。
- 以前使用 `0` 表示未知的部分字段现在会保持 `null`，下游应按缺失数据处理。
- 完整报告的评审团实际为 66 席；独立 quick-scan 仍按其上游 command 契约使用 Top 10。

## 明确边界

Cognitrade 保留的是 UZI 生产能力，而不是对上游 GitHub 仓库的完整镜像。上游安装器、插件市场清单、hooks、通用文档和测试源码不进入生产 vendor；这些文件不参与 Agent OS 运行，并在 source mapping 中明确归类为分发或 CI 边界。

上游模板和源码中仍有少量历史性“50/51 位评委”静态文案或注释。为保持逐字节一致，vendored 文件不在 Cognitrade 内单独改写；Cognitrade 自有的 one-liner 和前端档位已使用真实 64/66 席口径。
