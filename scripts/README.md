# M14 开发运维脚本

在仓库根目录使用 Python 3.11+，先安装 `python -m pip install -e ".[dev,integrations]"`。
所有命令支持 `--help`；成功输出 JSON，运行失败返回 1，参数错误返回 2，中断返回 130。
失败信息不回显上游响应、LLM 输出或凭据。下面的配置检查不调用模型或修改远端数据。

```powershell
.venv/Scripts/python.exe scripts/bootstrap_agent.py --tenant-id tenant --store-id store --check
.venv/Scripts/python.exe scripts/verify_tools.py
```

## Bootstrap

```powershell
.venv/Scripts/python.exe scripts/bootstrap_agent.py --tenant-id tenant --store-id store
```

加载现有 `Settings`（含 `.env`），通过应用 lifespan 装配数据库、Saleor/RAG、真实工具注册表和
Parlant Runtime。Runtime 启动时调用 `AgentBootstrapper.bootstrap`，报告 Agent ID、工具名和
规则/流程/术语数量，随后关闭资源。需要启用数据库与 Parlant，提供数据库 URL、模型密钥及
可写的 Parlant 数据目录；启用 Saleor/RAG 时也须提供相应配置。
该命令会同步 Parlant 配置，适用于部署环境显式执行。运行时不会建立客服会话或执行购物操作。

T31 默认配置现在启用 14 个工具，因此仅以空 `StaticToolRegistry` 启动 Parlant 不再适用。
服务启动入口应调用 `create_app(agui_scope=StoreScope(...))`，或注入等效的真实工具注册表。
浏览器认证和购物车确认仍须由可信 BFF 提供；YAML 不能代替授权实现。

## 生成候选配置

```powershell
.venv/Scripts/python.exe scripts/generate_agent_config.py --business-description business.md --model YOUR_MODEL --output-dir .cache/agent-candidates/review-001 --llm-command YOUR_LLM_EXECUTABLE
```

`--llm-command` 必须放在最后，其后的内容是可执行程序及参数，不经过 shell。
可以指定已有的、支持从标准输入读取提示词的 LLM CLI，或部署方的模型调用程序。
程序须读取 UTF-8 JSON 请求，其中包括 `model`、`instruction`、`business_description`、
当前真实 SDK 工具元数据、配置 JSON Schema 和 baseline；调用指定模型后，在 stdout
只返回一个符合 Schema 的 JSON 对象，不带 Markdown 围栏。密钥由该程序从环境变量读取，
不要写进业务说明。命令必须直接等待模型完成，不应自行派生后台进程。

`ConfigGenerationProvider` 是可注入接口，`CommandLlmProvider` 是现成的命令适配实现；
项目没有绑定某一家模型服务或默认付费模型。`--timeout` 默认 120 秒。

生成结果经过 Pydantic、`AgentConfigValidator`、工具确认能力、购物车风险及 Agent ID 校验，
再写入五份 YAML 并重新加载验证。输出目录必须不存在，且不能是批准配置目录的自身、
子目录或父目录；已存在的候选也不会覆盖。默认批准配置为 `configs/agents/customer_service`。
候选不自动启动、不替换线上规则；经人工审查和版本控制后再安排发布。
模型生成的业务规则是否准确仍需人工审查，结构校验不能证明规则语义正确。

## 同步 Saleor schema

```powershell
.venv/Scripts/python.exe scripts/sync_saleor_schema.py --endpoint https://saleor.example/graphql/ --token-env SALEOR_SERVICE_TOKEN
```

使用标准 GraphQL introspection，验证响应并输出 SDL，默认写入 `.cache/saleor/schema.graphql`。
`--output schema.graphql` 可显式更新开发 schema。`--endpoint` 默认取 `SALEOR_API_URL` 环境变量；
也支持 `--token`，建议使用环境变量以免令牌进入命令历史。此脚本不加载 `.env`，不跟随重定向。
请求或解析失败不会替换原文件；成功后同目录原子替换。不会自动运行 ariadne-codegen。

## 工具与连接检查

```powershell
.venv/Scripts/python.exe scripts/verify_tools.py --online --admin-url http://127.0.0.1:8000/mcp/ --admin-token-env ADMIN_MCP_OPERATOR_TOKEN
```

默认检查真实 Parlant 注册表、五份 Agent YAML 和本地 FastMCP Admin 的工具目录，列出配置的
RAG 工具，检查同名冲突；JSON 明确标记 `remote_dependencies=not_checked`。
`--online` 另检查客服工具所需的 Saleor/RAG：Saleor 读取 channel，RAG 连接并调用 `list_tools`，
缺少依赖、超时或缺少配置工具均失败。启用 Admin MCP 或传入 `--admin-url` 时，还会通过
认证的 FastMCP Client 比较远端 Admin 目录。不会执行后台写工具，也不会启动一个远端 Parlant 实例。
该检查证明连接和目录可用，不能替代所有业务操作的权限与数据验证。

## 测试分层与边界

```powershell
.venv/Scripts/python.exe -m pytest tests/unit
.venv/Scripts/python.exe -m pytest tests/integration tests/e2e
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check src tests scripts
```

T33 的五个领域入口位于 `tests/unit/`，补充脚本及候选生成器测试。
已有 `tests/customer_identity`、`tests/conversations`、`tests/saleor`、`tests/frontend`、
`tests/security` 中的深入回归保留原目录，不重复复制；包含并发创建、身份隔离、订单归属、
响应归一化、mutation 错误、版本冲突和默认拒绝策略。未知 Frontend State 字段延续现有实现：
拒绝整个非法更新并保持原状态，避免敏感字段进入上下文。

T34 的四组 integration 与四组 E2E 场景位于独立目录。测试使用真实 SQLite、SDK tool
装饰器、Saleor Service/GraphQL 客户端、FastMCP Client/Server 和 ASGI HTTP/SSE。
可控的 Parlant processing 替身决定调用顺序，Saleor HTTP 使用 MockTransport，浏览器由
测试客户端执行动作并经第二条 HTTP 请求发送回执。测试覆盖确认单次消费、状态写入后继续、
匿名 A/B 隔离、登录升级保留 Session、仅查询本人订单，以及 Admin 审计落库。

这些测试不调用真实 LLM、不访问生产 Saleor/RAG，也不打开真实浏览器；模型决策质量、
真实 Storefront/BFF 和 PostgreSQL 部署仍需在相应环境联调。Windows 沙箱若禁止 asyncio
命名管道，LLM 命令子进程测试需要在允许本地子进程管道的环境运行。
