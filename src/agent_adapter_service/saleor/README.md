# Saleor services

本模块复用现有 `saleor_client.Client` 和 30 个 GraphQL 操作。
生成目录和 GraphQL 文档保持不变；本次未运行 Codegen。

## 生命周期与调用

开启 `SALEOR_ENABLED` 并配置 `SALEOR_API_URL`、`SALEOR_SERVICE_TOKEN` 后，
应用默认创建客户端并注册 channel、product、customer、order、inventory、promotion 服务。
对应的强类型 getter 位于 `app/dependencies.py`。
显式传入 `saleor_client` resource factory 时仍由该工厂决定如何注册测试替身。

启动只装配本地资源，不调用远端；应用 ready 状态不代表 Saleor 可用。
可显式调用 `await client.healthcheck()`，它使用已有 ChannelList 操作，要求相应渠道查询权限。
客户端仅关闭自己创建的 HTTP client；外部注入的 dedicated、无 cookie 客户端由调用者关闭。

```python
from agent_adapter_service.saleor.client import saleor_trace

with saleor_trace(run_id):
    products = await product_service.search_products("jacket", "us", first=20)
    order = await order_service.get_customer_order(customer_context, order_id)
```

trace 通过 ContextVar 按异步任务隔离，以 X-Request-ID 发送到 Saleor。
未提供 trace 时不自动生成；不记录原始输入、认证头或上游错误消息。

## 身份边界

- 商品及 Variant 查询使用公开请求，不携带服务 token。列表另加已发布、列表可见筛选。
  渠道有效性检查使用服务 token，因此服务凭据需要渠道查询权限。
- `CustomerContext` 只能从可信 BFF/Identity 上下文构造。匿名及认证不可用状态不能读取客户、订单。
  不带客户 token 时，通过服务 token 查询已验证用户；带 token 时客户详情使用 Me。
  客户订单先查询最小归属信息，再查询详情，并再次核对归属。
- `AdminContext` 必须由已认证、已授权的后台适配层创建，不能由模型或浏览器参数直接构造。
  principal_id 是调用上下文，不是授权证明。M09/M12 负责具体操作的 scope 检查和审计。
  本模块不会自动开放 HTTP、Parlant 或 MCP 工具入口。

## 输入与返回值

查询结果为 `models.py` 的业务 DTO，分页返回 `Page.items` 和 `Page.page_info`。
商品/Variant 可售数量与价格缺失时保留 None；不将未知库存当作零。
Variant 属性解析使用属性 slug 与值 slug 精确匹配；分页不完整、多匹配均不会任意选一个结果。

后台输入复用生成目录中的 Pydantic Input 类型；写入按服务允许的字段集合校验。
商品和客户写入不开放 privateMetadata、身份确认等敏感字段。
调用时仅显式构造需要更新的字段，不要使用带全部默认值的 model_dump 重建输入：

```python
from agent_adapter_service.saleor.saleor_client.input_types import PromotionUpdateInput

# 不修改时间。
await promotion_service.update_promotion(admin_context, promotion_id,
                                        PromotionUpdateInput(name="Summer sale"))
# 显式移除结束时间。
await promotion_service.update_promotion(admin_context, promotion_id,
                                        PromotionUpdateInput(endDate=None))
```

生成器保留“未传”与“显式 null”的差异。金额 DTO 使用 Decimal，输入价格也建议使用 Decimal。
上游 Money.amount 为 Float；DTO 转换无法恢复上游浮点表示已经丢失的精度。

写操作不自动重试。超时不代表写入未发生；调用方应核查结果后决定下一步。
HTTP、GraphQL 顶层错误、payload errors 统一转换为 SaleorError 子类，批量错误保留索引。
上游原始 message 不对外返回；Saleor 仍负责最终权限、状态、仓库有效性及并发校验。

## 当前能力边界

- `ChannelService`：列表、按 ID/slug 查询、启用状态及币种校验。
- `ProductService`：商品/Variant 查询与解析；商品创建、更新、渠道发布；Variant 创建、更新、渠道价格更新。
- `CustomerService`：当前客户解析、后台读取、创建、更新。
- `OrderService`：客户订单列表/详情、后台详情、取消、履约；履约数量不得超过剩余数量，不开放超库存履约。
- `InventoryService`：按仓库创建/更新绝对库存，返回 Variant ID/SKU，不返回库存快照。
- `PromotionService`：促销及规则创建/更新；检查显式提供的日期和奖励参数。
  未同时提供完整起止时间或奖励类型时，剩余校验由 Saleor 完成。

不提供库存读取、促销读取/自动启停、Checkout 或 Collection 服务。
这些能力需要新的 GraphQL 操作，本阶段不增加。

## 验证

`python -m pytest tests/saleor` 使用 MockTransport 经过实际生成客户端验证请求和响应，
覆盖凭据隔离、业务错误、身份/订单归属、写操作校验、分页和生命周期。
无需真实 Saleor；部署前仍需验证实际 token 权限和渠道配置。
