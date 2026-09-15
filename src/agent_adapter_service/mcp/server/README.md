# M12 FastMCP Admin Server

T27–T29 提供独立的管理端 MCP 接口，使用 FastMCP 4.0.3 官方认证、工具 schema、Streamable HTTP 和 ASGI 生命周期。默认关闭；启用后的默认端点为 `/mcp/`，不依赖 Parlant 或消费者身份。

## 配置与启动

1. 安装依赖：`python -m pip install -e ".[dev,integrations]"`。
2. 编辑 `configs/mcp/admin_server.yaml`，为当前 Saleor 实例设置 store_scope、客户端 principal、最小必要 scopes 和 enabled_tools。required_scopes 必须与权限策略匹配。默认配置只是单店铺示例。
3. 设置 `ADMIN_MCP_ENABLED=true`；启用并配置数据库及 Saleor：`DATABASE_ENABLED`、`DATABASE_URL`、`SALEOR_ENABLED`、`SALEOR_API_URL`、`SALEOR_SERVICE_TOKEN`。开发环境可使用 `DATABASE_CREATE_SCHEMA=true`；生产环境需预先准备数据库结构。
4. 通过进程环境或 secret manager 设置 token_env 指定的凭据，例如 `ADMIN_MCP_OPERATOR_TOKEN`。使用独立、随机且至少 32 字符的 token；不要写入 YAML 或 `.env`。任意命名的客户端凭据仅从进程环境读取，轮换后重启服务。不要复用 Saleor service token。
5. 运行 `uvicorn agent_adapter_service.main:app`，MCP 客户端连接 `/mcp/` 并携带 `Authorization: Bearer <admin client token>`。对外部署通过反向代理提供 HTTPS。

启动时先建立业务 Service 与持久化审计，再启动 MCP；关闭时按相反顺序释放。缺少数据库或启用工具对应的 Service 会使启动失败。未知工具、错误 scope、重复客户端和跨 store principal 在启动时拒绝。

Admin 身份和权限仅由服务端凭据映射决定。工具参数、自定义身份请求头及消费者登录状态不能授予管理权限。未认证 HTTP 请求返回 401，工具调用再次校验凭据、store 和 scope。

## 工具与权限

| 工具 | 必需 scope |
| --- | --- |
| create_product / update_product / publish_product | products:write |
| update_variant / update_variant_price | products:write |
| get_stock | inventory:read |
| update_stock | inventory:write |
| get_order | orders:read |
| cancel_order | orders:cancel |
| fulfill_order | orders:fulfill |
| get_customer | customers:read |
| update_customer | customers:write |
| create_collection / update_collection / add_products_to_collection | collections:write |
| create_promotion / update_promotion / disable_promotion | promotions:write |

价格更新显式要求 channel_id、currency，由 Service 验证渠道与货币一致。订单操作检查当前状态与剩余可履约数量，最终并发校验由 Saleor 完成。库存读取有分页上限，返回仓库、Variant、数量及已分配数量。

客户更新仅暴露 firstName/lastName，拒绝邮箱、账户启停、metadata 等字段。集合工具暴露名称、slug、描述及创建时的产品列表，不接受 Upload 或私有 metadata。

Saleor 没有 promotion enabled 字段。disable_promotion 读取现有时段，将结束时间设为当前 UTC；尚未开始的促销会同时将开始时间移至结束时间之前，使其成为有效的已结束时段。已经结束的促销直接返回当前状态。

工具仅调用 `saleor/services/`，不包含 GraphQL。新增操作位于 `saleor/graphql/`，生成客户端以仓库 `schema.graphql` 为准。Windows 重新生成命令：`python -X utf8 -m ariadne_codegen`，避免默认代码页损坏输出。

## 结果与审计

业务结果为 `{ok, data, trace_id}` 或 `{ok: false, error: {code, message}, trace_id}`。协议及参数错误由 FastMCP 处理；参数错误不回显输入值。内部异常文本、token 和堆栈不进入业务结果或参数校验日志。

每次已授权调用先持久化 pending 审计，再访问 Saleor，完成后追加 success/failure；权限拒绝记录 denied。参数摘要脱敏，trace_id 同时传至 Saleor。数据库不可写时阻止操作。

外部写入与审计不构成分布式事务。如果业务操作完成后最终审计失败，返回 outcome_unknown，保留 pending 供核查，不自动重试。进程中断也可能留下 pending，需按 trace_id 核对 Saleor 状态。

## 验证

`python -m pytest tests/mcp/test_admin.py tests/saleor/test_admin_extensions.py`

测试使用官方 FastMCP Client、ASGI HTTP、SQLite 审计及模拟 Saleor HTTP 响应，覆盖 18 个工具的调用、认证与权限拒绝、审计失败、敏感字段限制、异常脱敏、应用启停和新增 GraphQL 操作，不访问真实 Saleor。
