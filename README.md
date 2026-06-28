# Cognitrade

当前目录是项目运行根目录。

这是一个面向 A 股研究场景的研究增强系统，当前已实现到 `Phase 3` 的最小闭环，支持四条主链：

- `stock_check`
- `sector_research`
- `distill_kol_logic`
- `review_thesis`

当前代码重点是：

- 多 Agent 运行时骨架
- `CLI` 执行与 run 归档
- `LLM + Verified Snapshot + 检索 + Gate` 最小闭环
- `Qdrant` 本地检索与重建
- 最小本地回归测试

## 目录结构

```text
Cognitrade/
├─ research_system/    # 主代码
├─ tests/              # 本地回归测试
├─ workspace/          # 运行数据、Qdrant 本地库、runs 归档
├─ .env
└─ .env.example
```

## 环境变量

先准备根目录 `.env`。

最小示例见 [`.env.example`](</D:/work_26_6_24/Cognitrade/.env.example>)。

当前常用项：

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-5.5
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=research_evidence
DEFAULT_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
LLM_REQUEST_TIMEOUT_SECONDS=25
RUN_TIMEOUT_SECONDS=120
EXTERNAL_WAIT_TIMEOUT_SECONDS=30
```

说明：

- 若使用本地 `Qdrant`，可不填 `QDRANT_URL`
- 若使用兼容 OpenAI 网关，`OPENAI_BASE_URL` 填网关地址；代码会自动归一化到 `/v1`
- `OPENAI_MODEL` 建议与网关真实支持的模型名保持一致
- `LLM_REQUEST_TIMEOUT_SECONDS` 控制单次 LLM 请求超时；建议明显小于 `RUN_TIMEOUT_SECONDS`

## 常用命令

在项目根目录执行。

### 运行研究

```bash
python -m research_system.cli.app run "帮我看下 000001 这个股票" --json
python -m research_system.cli.app run "最近创新药板块怎么样" --json
python -m research_system.cli.app run "请蒸馏这个雪球 statuses 文件 D:\\work_26_6_24\\scraper\\output\\xueqiu_webbridge_20260624_145246\\statuses.jsonl" --intent distill_kol_logic --json
python -m research_system.cli.app run "复核上次创新药判断" --intent review_thesis --run-id <target_run_id> --json
```

### 交互模式

当前 `chat` 是一个常驻输入框的终端对话壳，接近 `codex` 的命令行交互方式：

```bash
python -m research_system.cli.app chat
```

界面特点：

- 底部输入框始终可见
- 上方持续滚动输出
- 默认主视图是 `thinking / status / answer`，并按区块分隔
- 状态栏会持续显示 `intent / run / phase / gate / clarify / state`
- 输入框上方有一条动态提示栏，会根据 `idle / running / clarification pending / popup open` 切换提示
- 支持运行中停止和退出

基本键位：

- `Enter`：提交输入
- `Up/Down`：回看输入历史
- `Tab`：补全 slash 命令和常见参数
- `Esc`：清空当前输入框；若命令浮层打开则先关闭浮层
- `F1`：打开/关闭命令浮层
- `Ctrl+C`：停止当前 run；空闲时清空输入
- `Ctrl+D`：退出交互壳

输入方式：

- 直接输入自然语言，会默认当成一次 `run`
- 斜杠命令会被识别成控制指令
- 如果上一轮停在 `needs_clarification`，下一句普通输入会自动作为补充信息续接，而不是重新起一轮

常用示例：

```text
最近创新药板块怎么样
/history
/resume <run_id>
/replay <run_id>
/intent review_thesis
/run-id <target_run_id>
复核上次创新药判断
/review <target_run_id> 复核上次创新药判断
/status
/doctor
/check
/exit
```

当前支持的交互指令：

- `/commands`
- `/help`
- `/exit`
- `/intent <intent|auto>`
- `/run-id <run_id|clear>`
- `/thinking <on|off>`
- `/events <off|compact|full>`
- `/stop`
- `/run <query>`
- `/review <run_id> <query>`
- `/history [limit] [status]`
- `/status`
- `/doctor`
- `/check`
- `/replay <run_id|last>`
- `/resume <run_id|last>`
- `/cancel <run_id|last>`
- `/cleanup`
- `/reindex`
- `/last`
- `/clear`

显示模式说明：

- `thinking`
  - 面向用户的“思路流”
  - 不直接暴露底层原始 agent 日志
- `status`
  - 当前阶段推进和 gate 摘要
- `answer`
  - 最终回答流式输出

调试开关：

- `/thinking off`
  - 关闭思路流
- `/events compact`
  - 仅显示压缩状态
- `/events full`
  - 显示原始事件行

### 查看历史与回放

```bash
python -m research_system.cli.app status --json
python -m research_system.cli.app doctor --json
python -m research_system.cli.app history --json
python -m research_system.cli.app replay <run_id> --json
python -m research_system.cli.app resume <run_id> --json
python -m research_system.cli.app cancel <run_id> --json
```

### 清理与重建

```bash
python -m research_system.cli.app cleanup --json
python -m research_system.cli.app reindex-qdrant --json
```

`reindex-qdrant` 会：

- 清空当前 `Qdrant` collection
- 从 `workspace/runs/*/blackboard.jsonl` 重新回灌 `source_record` 和 `verified_snapshot`

### 本地检查

```bash
python -m research_system.cli.app check --json
```

`status` 用于查看当前项目状态摘要，包括：

- 最近 run
- 各状态 run 数量
- 当前模型与 `Qdrant` 模式

`doctor` 用于查看当前环境诊断，包括：

- `.env` 与关键配置是否存在
- LLM 单次请求超时是否合理
- workspace 目录是否齐全
- 本地 `Qdrant` 目录是否存在
- 当前 run 归档是否可见

当前会统一执行：

- `research_system` 编译检查
- `tests` 编译检查
- `unittest` 回归测试

也可以单独执行：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall research_system tests
```

## 运行产物

每次 `run` 都会生成：

```text
workspace/runs/<run_id>/
├─ request.json
├─ task_plan.json
├─ run_state.json
├─ run_control.json
├─ checkpoint.json
├─ events.jsonl
├─ logs.jsonl
├─ blackboard.jsonl
├─ result.json
└─ summary.json
```

`workspace/qdrant_local/` 是本地 `Qdrant` 存储目录。

### result.json 稳定字段口径

当前 `result.json` 的稳定主字段是：

- `conclusion`
- `evidence`
- `risk`
- `follow_up`
- `degraded_note`

按 intent 还会有这些显式稳定字段：

`distill_kol_logic`

- `scorecard`
  - 蒸馏态势卡
  - 当前包含 attribution/review 强度等结构化摘要

`review_thesis`

- `review_summary`
  - 后验复核汇总
  - 在 LLM 成功、LLM 失败、LLM 不可用三条路径下都稳定返回
  - 当前稳定包含：
    - `finding_count`
    - `confirmed`
    - `rejected`
    - `inconclusive`
    - `benchmark_scope_counts`
    - `benchmark_scope_explanations`
    - `overall_verdict`
    - `summary_text`
    - `verdict_by_claim_type`

`review_summary.verdict_by_claim_type` 的口径是：

- key 为 `claim_type`
  - 当前支持 `direction`、`catalyst`、`time_window`
- value 为该类 claim 的 verdict 分布
  - `confirmed`
  - `rejected`
  - `inconclusive`

最小示例：

```json
{
  "review_summary": {
    "review_required": false,
    "finding_count": 3,
    "reviewable_claim_count": 3,
    "claim_type_counts": {
      "direction": 1,
      "catalyst": 1,
      "time_window": 1
    },
    "benchmark_scope_counts": {
      "sector_vs_hs300": 3
    },
    "benchmark_scope_explanations": {
      "sector_vs_hs300": "板块方向主张按板块相对 HS300 表现复核。"
    },
    "overall_verdict": "mixed",
    "summary_text": "overall_verdict=mixed; findings=3; confirmed=1; rejected=1; inconclusive=1; benchmark_scopes=sector_vs_hs300",
    "verdict_by_claim_type": {
      "direction": {
        "confirmed": 1,
        "rejected": 0,
        "inconclusive": 0
      },
      "catalyst": {
        "confirmed": 0,
        "rejected": 1,
        "inconclusive": 0
      },
      "time_window": {
        "confirmed": 0,
        "rejected": 0,
        "inconclusive": 1
      }
    },
    "confirmed": 1,
    "rejected": 1,
    "inconclusive": 1,
    "notes": []
  }
}
```

## 当前已实现能力

### stock_check

- 个股行情快照
- 个股基本面快照
- 公告快照
- `Retriever` 去重与结构化摘要
- `Reporter` 输出研究结论

### sector_research

- 板块识别与真实板块快照
- `HS300` 基准对照
- 相对强弱
- 板块内部广度
- 代表成分股催化样本
- 主题新闻
- 政策信号
- 监管信号
- `signal_scorecard`

### distill_kol_logic

- `collector -> distiller -> critic -> retriever -> reporter` 主链
- `logic_block / distilled_dimension / review_finding` 落 blackboard
- chunked distillation
- quoted content attribution
- critic review entry
- Qdrant 回灌与检索
- timeout-aware fallback

### review_thesis

- `collector -> reviewer -> retriever -> reporter` 主链
- 支持 `--run-id <target_run_id>` 复核旧 run
- 当前支持三类最小可复核 claim：
  - `direction`
  - `catalyst`
  - `time_window`
- `ReviewGate` 已接入 runtime
- 最终 `result.json` 显式返回 `review_summary`

## 当前测试覆盖

当前本地回归测试覆盖：

- `sector_snapshot` 去重优先保留当前 `run_id`
- `market_snapshot` 去重优先保留当前 `run_id`
- `signal_scorecard` 摘要输出
- `reindex-qdrant` 回灌逻辑
- `stock_check` 采集链调用顺序
- `market_snapshot` 文本落库格式
- `critic` 抽检规则
- chunked distillation 合并
- `llm_timeout` 风险落盘

## 已知限制

- `AkShare` 部分接口仍可能偶发不稳定
- 本地 `Qdrant` 文件模式不支持多进程并发访问
- `resume` 当前是恢复判定，不是完整自动续跑
- `cancel` 对已进入执行中的任务是控制面终止，不是任意步骤的强杀
- `Mailbox` 仍未进入主链深度协作
- `Phase 2` 仍是最小闭环，蒸馏质量仍受网关时延和 fallback 比例影响
- `Phase 3` 仍是最小闭环，claim 抽取和复核规则还比较保守
- `proposal / mem0` 还未开始

## 当前状态

如果只看可运行性，当前已经具备：

- `Phase 0` 运行时主骨架
- `Phase 1` 的两条主要研究闭环
- `Phase 2` 的最小蒸馏与 critic 抽检闭环
- `Phase 3` 的最小后验复核闭环
- 最小测试与本地检查入口

如果按完整文档验收，`Phase 1` 仍有增强项未收尾，`Phase 2` 和 `Phase 3` 也都还不是完整高质量版，但四条主链都已经可运行。

