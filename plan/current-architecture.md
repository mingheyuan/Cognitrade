# Cognitrade 当前架构

本文只描述当前可运行主线。历史 CLI、ResearchOrchestrator、TaskRouter、动态
Subagent discovery、Proposal 与 methodology evolution 架构已删除，不再作为兼容入口。

## 请求主线

```text
Web
  -> FastAPI /api/v1
    -> RequestIntakeService
      -> Core conversation/task
      -> DomainPack-owned intake
    -> TurnRunner
      -> CoreLoopExecutor
      -> ExecutionGraphRunner
        -> CapabilityPlanner
        -> DomainPack Registry
        -> governed Tool Registry
        -> ArtifactBoard
        -> quality gates / approval ledger
```

- `/api/v1` 提供研究运行、Chat、Dashboard、Admin 与 System。
- `/api/v2/finance` 提供行情聚合、VeighNa 模拟撮合和 Hikyuu 独立回测。
- 未版本化 `/api/*` 业务路由不存在。
- `TurnSnapshot` 与 `TaskSnapshot` 分别持久化为 `turn_state.json` 和
  `task_state.json`；旧别名只能通过显式迁移命令处理。
- Finance 与 Legal Contract 都以 DomainPack 安装，核心运行时不包含领域流程分支。

## 运行时职责

| 模块 | 职责 |
| --- | --- |
| `runtime/intake` | Core/Pack 请求识别与澄清 |
| `runtime/turn_context.py` | Turn 执行上下文和 Task 协议 |
| `runtime/core/core_loop.py` | Core provider/tool 单步循环 |
| `runtime/turn_runner.py` | Turn 生命周期、恢复、控制和 ChildTurn |
| `runtime/planning` | 依据 Pack capability 合同生成和动态修补图 |
| `runtime/execution/core` | 图调度、节点执行、Gate、缓存、审批 |
| `runtime/graph_delivery.py` | Graph 产物到 Main Agent 最终交付 |
| `runtime/domain_packs` | Pack 加载、完整性和实现解析 |
| `api` | 鉴权、验证、请求 ID、HTTP/SSE 和版本边界 |
| `api/veighna_paper_adapter.py` | VeighNa PaperAccount 无界面订单建模与 Tick 撮合 |
| `api/paper_trading_service.py` | SQLite 资金、费用、T+1、FIFO 持仓与挂单冻结账本 |
| `api/hikyuu_backtest_service.py` | 隔离子进程执行 Hikyuu 策略并持久化结果 |

## 工作区

```text
workspace/
  config/                 # provider 与数据源配置
  runs/<run_id>/          # canonical Run/Turn/Task/Artifact 状态
  memory/                 # episodic/domain/policy memory
  cache/                  # 可重建缓存
  paper_trading/          # SQLite 模拟盘账本
  backtests/              # Hikyuu 回测结果与隔离运行缓存
  .migrations/            # 已执行的显式迁移标记
```

旧工作区一次性迁移：

```bash
./scripts/deploy.sh migrate
```

正常启动不会隐式复制、双写或删除旧状态文件。

## 前端数据边界

研究结论、行情、财务、Agent 状态只来自真实 API。尚无后端合同的交互允许做
显式“本地预览”，但只能使用浏览器已经加载的数据或本地文件元信息，不能生成
虚构市场数据、财务数据或 Agent 结论。
