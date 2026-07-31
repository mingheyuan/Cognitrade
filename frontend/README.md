# Cognitrade Web

React + TypeScript + Vite 前端。所有研究、行情、模拟交易和运行状态均通过版本化
FastAPI 获取，不直接读取 `workspace`，也没有 CLI 依赖。

## 开发

```bash
npm install
npm run dev
```

默认 API 地址：

```text
http://127.0.0.1:8000/api/v1
```

可以设置：

```dotenv
VITE_COGNITRADE_API_BASE_URL=http://127.0.0.1:8000/api/v1
VITE_COGNITRADE_API_TOKEN=<token>
```

也可以在页面顶栏配置地址和 Token。Token 只进入当前标签页的
`sessionStorage`。

## 数据边界

- 研究运行、Dashboard、事件和 Artifact：`/api/v1`
- 行情聚合、VeighNa 模拟撮合与 Hikyuu 独立回测：`/api/v2/finance`
- 模拟盘现金、费用、持仓、T+1 与挂单冻结由服务端 SQLite 账本负责；
  浏览器不提供可执行成交价。
- 回测只消费 Finance Pack 的真实历史 K 线，结果独立持久化，不修改模拟账户。
- API 返回数据缺口时显示可读提示，不展示上游连接异常原文。
- 没有后端合同的功能必须标记“本地预览”；只允许处理已加载数据或文件元信息，
  不生成虚构行情、财务或 Agent 结论。

## 验证

```bash
npm run lint
npm test -- --run
npm run build
```

接口合同见 [`../api-doc/api-v1.md`](../api-doc/api-v1.md) 和
[`../api-doc/api-v2.md`](../api-doc/api-v2.md)。
