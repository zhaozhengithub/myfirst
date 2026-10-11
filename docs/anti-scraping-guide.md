# 商品价格、优惠与优惠券数据防采集：具体算法方案

> 面向电商、内容交易和本地生活平台。目标是识别疑似竞争对手的批量采集行为，并在保护正常购物、搜索引擎、合作方和辅助技术访问的同时，降低价格、促销和优惠券数据被规模化拿走的速度、完整度和可用性。

整理日期：2026-10-11。

## 0. 目标、边界和基本判断

只要普通用户能够合法看到精确价格或优惠，技术上就不能保证对方绝对拿不到。可执行目标是：

1. 限制单个主体在时间、商品和优惠维度的获取量。
2. 识别价格轮询、商品遍历、优惠券枚举、账号农场和分布式采集。
3. 对高风险请求追加成本，或不返回非必要的价格、促销和券字段。
4. 让优惠资格、优惠金额和券核销始终由服务端重新计算。
5. 保留可审计证据、可解释的处置原因和自动恢复路径。

本文只讨论自有站点和自有 API 的防护。IP、设备和行为信号需要做数据最小化、访问控制和留存期限管理，并遵守适用的隐私、消费者保护和无障碍要求。不要向真实用户返回虚假价格、虚假优惠或不可兑换的交易信息；追踪标记只用于归因和告警。

## 1. 保护对象与威胁模型

### 1.1 按数据价值给接口分级

| 等级 | 典型接口 | 数据价值 | 默认策略 |
| --- | --- | --- | --- |
| L0 | 分类、搜索、静态图片、商品标题 | 低 | 可缓存、基础限流 |
| L1 | 商品详情、普通库存、公开展示价 | 中 | 会话和主体配额、对象覆盖限制 |
| L2 | 实时价格、会员价、地区价、活动价、库存变化 | 高 | 短时签名上下文、成本限流、风险挑战 |
| L3 | 可用券列表、券规则、券码校验、优惠资格、批量导出 | 极高 | 账户/购物车/地区/商品绑定，单次 nonce，严格审计 |

建议把 /price、/promotion、/coupon、/coupon/validate、/inventory 和导出接口登记为资产。每个接口记录返回字段、是否公开、允许的主体类型、单次业务成本、合规要求和最大可接受延迟。

### 1.2 竞争对手采集的典型行为

- 匿名或登录 API 批量读取商品详情和实时价格。
- 固定时间间隔轮询同一 SKU，捕捉价格变化。
- 按顺序或分页遍历全量 SKU、类目和门店。
- 先枚举券 ID、活动 ID，再调用资格或券校验接口。
- 通过大量账号、云主机、代理或住宅网络分散请求。
- 复用登录 Cookie、游标、签名 URL 或券校验请求。
- 读取初始 HTML、前端配置、接口响应中的完整券规则。
- 对一个商品建立多个地区、会员等级和数量的价格快照。
- 用自动化浏览器模拟页面，却没有真实购物意图。

不要把云厂商 IP、某个 User-Agent 或没有鼠标事件单独当作竞争对手证据。在线结果应表述为疑似自动化或批量获取风险，而不是断言对方身份。

## 2. 总体架构

~~~mermaid
flowchart LR
  A[客户端] --> B[CDN/WAF]
  B --> C[协议校验与源站保护]
  C --> D[API网关]
  D --> E[特征状态 Redis/流处理]
  E --> F[规则与模型风险引擎]
  F --> G{接口等级与风险}
  G --> H[正常返回]
  G --> I[限流与并发控制]
  G --> J[挑战/登录/二次认证]
  G --> K[字段裁剪或短时报价]
  G --> L[403/429/冷却]
  H --> M[业务授权与服务端重算]
  K --> M
  M --> N[商品、促销和券服务]
  F --> O[审计与监控]
  O --> P[离线标注、回放、模型更新]
  P --> F
~~~

请求必须经过“协议校验 → 身份与权限 → 多维配额 → 风险评分 → 字段级决策 → 业务最终授权”。边缘层负责容量和粗粒度限制，业务服务负责价格和券资格的最终判断。

源站只接受 CDN 或网关的受控访问。缓存键必须包含公开/登录态、地区、货币、会员等级和字段范围等必要维度，不能把一个用户的价格或券响应缓存给另一个用户。

## 3. 在线特征：四个窗口、五类主体

同时维护 10 秒、1 分钟、15 分钟和 24 小时窗口。每个窗口按以下主体分别聚合：

- IP 或可信代理链的哈希，只在受信任边缘解析转发头。
- 账号、API Key、租户。
- 会话和设备摘要。
- ASN 或网络类型。
- SKU、类目、活动、券 ID 和接口模板。

### 3.1 通用特征

对窗口 w：

~~~text
rate_w = request_count(w) / w
sensitive_ratio = sensitive_requests / all_requests
unique_sku_w = distinct(SKU)
bytes_rate_w = response_bytes / w
concurrency = in_flight_requests
error_ratio = 4xx_or_5xx / requests
retry_ratio = repeated_same_request / requests
~~~

### 3.2 价格与促销特征

- price_poll_rate：同一 SKU 在窗口内的价格读取次数。
- snapshot_width：短期读取的唯一 SKU、类目和地区数量。
- coverage_rate：读取的 SKU 数 / 该主体正常可访问的 SKU 数估计。
- sequence_rate：商品 ID 中最长单调连续段长度 / 唯一 ID 数。
- pagination_depth：游标深度、跨页跳跃和页面大小。
- low_intent_ratio：详情读取数 / 加购、搜索、结算等后续意图信号。
- burst_after_change：价格或活动变更后短时间内的读取突增。
- cross_region_fanout：同一会话或账号短时间跨地区查询次数。
- price_response_bytes：单位时间返回的价格字段字节量。

连续 ID、低停留时间和没有加购行为只能作为弱信号。价格比较、无障碍浏览和移动网络也会产生这些现象。

### 3.3 优惠券专属特征

- coupon_id_attempts：单位窗口尝试的不同券 ID 数。
- coupon_fail_ratio：无效券、过期券、不适用券的比例。
- coupon_context_missing：没有商品、购物车、地区或账号上下文的券校验比例。
- same_coupon_multi_account：同一券在短时间被多个账号尝试。
- coupon_replay：相同请求摘要、nonce 或幂等键重复提交。
- enumeration_pattern：券 ID 的递增、固定长度、前缀分组和相邻尝试。
- qualification_fanout：同一账号或设备在多商品、多门店、多地区请求资格的数量。
- redeem_fail_burst：核销失败后仍持续爆发尝试。

券失败率高不一定是爬虫。活动规则变化、用户输入错误和客户端版本问题需要单独标注。

### 3.4 客户端、身份和网络特征

- UA、Client Hints、Accept-Language、时区与实际请求格式是否一致。
- Cookie 和挑战状态是否能形成连续会话；会话是否瞬间跨大量 IP。
- 账号年龄、验证状态、短期创建账号数、同设备账号扇出。
- IP/ASN 信誉、代理特征、同一出口的账号和设备数量。
- TLS/HTTP2 指纹与请求行为是否一致。指纹只做聚合信号，不当作唯一 ID。
- 页面资源加载、脚本执行和业务请求的比例；缺失只能增加不确定性。
- 相邻请求间隔均值、标准差和变异系数 CV = std(Δt)/(mean(Δt)+epsilon)。
- 路径分布熵 H = -sum(p_i * log2(p_i))，小样本时不评分。

## 4. 先执行硬规则，再执行风险评分

硬规则用于确定性安全约束：

- 身份、权限、签名、时间窗或请求体摘要校验失败。
- nonce 已消费，或幂等键被不同请求摘要复用。
- 明确超过账户、租户或导出额度。
- 券 ID 枚举达到经过基线校准的上限。
- 请求体、分页大小、查询深度或并发超过服务能力。
- 请求绕过受信任网关访问源站。
- 价格或券接口使用不属于当前账号、购物车、地区或会话的签名上下文。

硬规则也要有 TTL、审计和恢复路径。对共享 NAT、移动网络和公司出口，不能只依据一个 IP 永久封禁。

## 5. 具体风险评分算法

### 5.1 特征归一化

对匿名、登录、合作方和辅助技术分别建立基线。对每个特征 x_i 使用分位数归一化：

~~~text
z_i = clip((x_i - P50_i) / (P95_i - P50_i + epsilon), 0, 1)
~~~

长尾特征先取 log1p(x)。P50 和 P95 应来自最近一段仅包含低风险样本的时间窗，避免攻击流量污染基线。新客户端、缺失值和样本不足需要单独编码 missing_i，不能自动等于恶意。

### 5.2 可解释的初始分

以下结构只用于离线回放，权重和阈值必须按真实业务校准：

~~~text
R_raw = 100 * (
  0.18 * z_rate
+ 0.12 * z_concurrency
+ 0.15 * z_sensitive_ratio
+ 0.10 * z_seq_rate
+ 0.08 * z_coupon_enum
+ 0.08 * z_replay
+ 0.10 * z_client_mismatch
+ 0.08 * z_account_fanout
+ 0.06 * z_network_reputation
+ 0.05 * z_low_intent
)
~~~

加入两个业务交互项，并限制单项贡献：

~~~text
R_interaction = 8 * z_sensitive_ratio * z_seq_rate
              + 8 * z_coupon_enum * z_replay
R_raw = clip(R_raw + R_interaction, 0, 100)
~~~

用指数平滑减少一次突发造成的状态跳变：

~~~text
R_t = alpha * R_raw_t + (1 - alpha) * R_(t-1)
~~~

alpha 可从 0.2～0.4 的离线回放起步，再按检测延迟和误伤率调节。价格变更期、秒杀期和大促期使用独立基线。

### 5.3 监督模型和未知模式

有审核标签后，用逻辑回归或 LightGBM 预测 p_bot。训练特征只能使用决策时已经发生的数据，按时间和账号/设备分组切分，避免同一采集团伙同时出现在训练和测试中。用 Platt scaling 或 isotonic regression 做概率校准。

无标签流量可以用 Isolation Forest、LOF 或聚类生成 p_anomaly，仅作为辅助特征或审核排序。异常不等于恶意，不能直接封禁。模型输出至少包含：

- risk_score：0～100 的策略分。
- p_bot：经过校准的疑似自动化概率。
- reason_codes：例如 PRICE_POLLING、COUPON_ENUM。
- model_version、feature_time、policy_version。

## 6. 风险 × 接口等级的动作策略

| 风险分 | L0/L1 普通数据 | L2 价格/活动 | L3 优惠券/资格/导出 |
| --- | --- | --- | --- |
| <30 | 正常返回，普通配额 | 返回必要字段，短期配额 | 已授权才返回，常规核销 |
| 30–50 | 轻度限速 | 降低频率，要求 Cookie 或登录 | 要求购物车和资格上下文 |
| 50–70 | 多维令牌桶，限制分页 | 挑战、短时签名报价、字段裁剪 | 挑战、严格并发和失败配额 |
| 70–85 | 冷却和再次认证 | 暂停实时价格，返回通用活动说明 | 暂停券列表和资格，保留人工恢复 |
| ≥85 且命中硬规则 | 短期联合限制 | 403/429 和 10～30 分钟冷却 | 冻结券接口，调查后解封 |

响应使用统一错误结构和语义，避免通过“券不存在、券存在但不可用、价格已改变”等差异继续枚举。429 可返回带短随机抖动的 Retry-After。挑战通过后也只获得短期、绑定资源的通行状态。

## 7. 自适应令牌桶、并发和对象配额

### 7.1 按敏感度计费

为 IP、账号、设备、会话、ASN、接口和 SKU 分别维护令牌桶；一次请求同时检查相关桶，取最小剩余额度。成本起点可以是：

| 请求 | 成本 |
| --- | ---: |
| 普通搜索或详情 | 1 |
| 实时价格或活动价 | 3 |
| 券规则或资格 | 5 |
| 券校验或核销 | 8 |
| 批量导出或高成本聚合 | 10 |

成本只用于压测起点。对风险分 R，让补充速率逐渐降低：

~~~text
tokens(t) = min(C, tokens(t0) + (t-t0) * r0 * exp(-beta*R))
allow iff tokens(t) >= cost and in_flight < K(R)
if allow: tokens(t) = tokens(t) - cost
~~~

C 是突发容量，r0 是正常补充速率，beta 由 SLA 和业务损失调节，K(R) 是风险相关并发上限。分布式状态用 Redis/KeyDB Lua 在同一键槽内原子更新，TTL 至少覆盖从空桶恢复的时间。跨机房不能假设全局精确原子，应按区域预分配额度并记录最大超发量。

### 7.2 多维检查

~~~text
allow = min(
  bucket(account),
  bucket(session),
  bucket(device),
  bucket(ip_or_proxy),
  bucket(asn),
  bucket(endpoint),
  bucket(sku_or_campaign)
)
~~~

对同一 SKU 的热点保护使用单独熔断器：当 price_poll_rate 超过该 SKU 历史基线倍数时，提高查询成本或要求短时授权。这样不会因全站流量增加而封禁所有用户。

### 7.3 并发和重试

价格快照、券校验和导出设置独立 in-flight 上限、超时和总排队预算。并发槽位使用租约，处理进程崩溃后的释放。429 响应加入随机等待，合作方客户端使用指数退避。券枚举失败通常消耗更高成本，避免低成本试错。

## 8. 短时签名上下文与防重放

### 8.1 价格和促销报价令牌

对需要实时精确报价的接口，由服务端签发短时上下文，绑定：

~~~text
context = {
  subject_id_hash,
  session_hash,
  sku_or_query_hash,
  region,
  currency,
  member_tier,
  quantity,
  issued_at,
  expires_at,
  nonce,
  policy_version
}
signature = HMAC-SHA256(server_secret, canonical(context))
~~~

服务端验证签名、受众、资源、时间窗、请求体摘要和账号权限。高价值报价可设置一次性 nonce，或允许短期重复读取但严格计数。密钥只存放在服务端或 KMS，支持轮换和审计。浏览器里的 JS、Cookie 和签名参数都可能被观察，不能当作永久秘密。

### 8.2 防重放和幂等

- 时间戳限制可接受窗口，并允许受控的未来时钟偏差。
- 以 subject + nonce 原子登记，TTL 覆盖剩余有效期。
- 幂等键绑定账号、请求摘要和业务对象；同键不同摘要直接拒绝。
- 核销、库存扣减和优惠使用记录在同一事务或等价原子机制中完成。
- 游标绑定筛选条件、主体和版本，限制深度、页大小和并发，拒绝自增 offset 扫描。

### 8.3 券接口规则

券列表不应匿名返回全量券和完整适用规则。服务端根据账号、购物车、地区、商品、数量和活动状态实时计算资格，只返回当前页面完成交易所需的最小字段。券校验失败使用统一响应；记录失败率和枚举模式，但不要把存在性泄露到错误消息或缓存中。

## 9. 字段级保护与缓存策略

| 风险/权限 | 可返回 | 处理方式 |
| --- | --- | --- |
| 低风险公开页面 | 展示所需价格和活动摘要 | 正常缓存、普通配额 |
| 中风险 | 展示价格、脱敏券摘要 | 减小页大小，隐藏批量规则和全量列表 |
| 高风险但需要完成购物 | 通用活动说明或短时授权报价 | 需要登录、挑战和购物车上下文 |
| 明确违规或重放 | 非敏感公共字段或统一错误 | 冷却、审计、申诉 |

不要把价格、券规则、内部活动 ID 和资格计算输入放入初始 HTML、隐藏 DOM、前端环境变量或通用接口响应。前端隐藏字段只是显示控制，不是授权。

高敏感报价默认短 TTL、private 或不缓存。CDN 缓存不要复用带个人资格的响应。公开价格无法绝对防止复制，应使用限流、对象覆盖配额和审计降低规模，不要用随机假价格。

## 10. 价格轮询和券枚举检测器

### 10.1 价格轮询检测

定义主体 e 在窗口 w 的分数：

~~~text
POLL(e,w) =
  0.35 * norm(price_requests_same_sku)
+ 0.25 * norm(unique_sku)
+ 0.20 * norm(snapshot_frequency)
+ 0.10 * norm(sequence_rate)
+ 0.10 * norm(low_intent_ratio)
~~~

同一 SKU 高频读取、多 SKU 高覆盖却没有搜索或加购链路、价格变更后全站读取突增，都提高分数。sequence_rate 只能作弱特征。结果进入总风险分，不直接封禁。

### 10.2 优惠券枚举检测

~~~text
ENUM(e,w) =
  0.30 * norm(number_of_coupon_ids)
+ 0.25 * norm(failed_coupon_ratio)
+ 0.20 * norm(sequential_id_ratio)
+ 0.15 * norm(context_missing_ratio)
+ 0.10 * norm(replay_ratio)
~~~

ENUM 超过校准阈值时，先降低券接口配额、要求购物车和挑战；持续命中或同时出现重放硬规则时，冻结券资格和校验接口并进入调查。不要只按一个账号或一个 IP 封禁整个活动，避免共享设备误伤。

## 11. 关系图识别分布式采集

建立短期关系图，节点包括账号、会话、设备摘要、IP/ASN、SKU、活动和券；边表示在时间窗内使用、访问或共享。计算：

- 同一设备短期关联账号数。
- 同一账号跨多个网络的同步请求数。
- 多主体访问相同 SKU 顺序和时间点的相似度。
- 同一签名上下文或游标在多主体出现的次数。
- 账号—设备—IP 的密集子图和扇出。

集合相似度可用 Jaccard：

~~~text
J(A,B) = |A ∩ B| / |A ∪ B|
~~~

大规模候选可用 MinHash/LSH，再对关键结果精确复核。公共出口、企业代理和家庭共享网络是高连接度正常节点；图结果只能作为风险特征。复杂图分析异步执行，在线只读取短 TTL 的团伙信号。

## 12. 在线决策伪代码

~~~python
def decide(request):
    validate_size_and_protocol(request)
    identity = authenticate_if_required(request)
    endpoint = endpoint_policy(request.route)

    features = feature_store.update(
        request, windows=(10, 60, 900, 86400),
        keys=("account", "session", "device", "ip", "asn",
              "sku", "campaign", "coupon")
    )

    hard_hit = hard_rules(request, identity, endpoint, features)
    raw_score = calibrated_model.predict(features)
    anomaly = anomaly_detector.score(features)
    risk = smooth(raw_score, request.session.previous_risk)

    if hard_hit:
        risk = max(risk, 90)
    if anomaly.is_new_pattern:
        risk = min(100, risk + anomaly.bonus)

    action = policy(risk, endpoint.level, identity.trust)
    cost = endpoint.base_cost * risk_cost_multiplier(risk)

    if not multi_dimensional_buckets.consume(request, cost):
        return generic_429(retry_after_with_jitter(risk))

    if action.challenge and not verify_challenge(request):
        return mask_or_require_auth(endpoint)

    if endpoint.level >= 2:
        verify_signed_context(request)
        verify_nonce_or_idempotency(request)

    authorization = server_side_price_or_coupon_calculation(request, identity)
    return render_minimal_fields(authorization, action.field_policy)
~~~

硬性授权失败不能被模型判为正常覆盖；模型缺失或超时按接口风险走预设降级策略。所有动作写入 request_id、风险分桶、原因码、规则/模型版本和字段策略版本。

## 13. 训练、评估和上线

### 13.1 标签和事件

样本分为“确认批量采集、确认正常、未知”。标签来源可以是人工审核、合作方认证、业务损失、挑战结果、已知测试流量和后续追踪。验证码失败不是恶意的充分证据，挑战成功也不是正常的充分证据。

事件字段建议：

~~~text
request_id, server_time, route_template, endpoint_level,
account_hash, session_hash, device_bucket, ip_prefix_hash, asn_hash,
sku_hash, campaign_hash, coupon_hash, status, latency_ms, response_bytes,
risk_score_bucket, reason_codes, challenge_result, policy_version
~~~

不要记录完整 Authorization、Cookie、签名密钥、券明文或不必要的请求正文。为这些状态设置 7～30 天等有界 TTL，并按合规要求调整。

### 13.2 关键指标

按请求、会话、账号和攻击活动分别统计：

- 高风险精确率、召回率、PR-AUC。
- 真实用户误伤率、挑战通过率、购物转化率变化。
- 敏感字段拦截率、漏放价格/券字节量和 SKU 覆盖量。
- 价格接口 P95/P99 延迟、网关错误率和数据库成本。
- 券枚举发现时间、重复核销率和冻结恢复时间。
- 地区、浏览器、移动端、无障碍方式和共享出口的分群表现。

### 13.3 灰度顺序

1. 7～14 天 shadow，只打分不改变响应。
2. 先启用硬规则和额度保护，再对中风险启用轻挑战。
3. 按主体类型、地区和接口做小流量灰度。
4. 监控误伤、转化、延迟和敏感数据漏放，达到停止条件立即回滚。
5. 新模型与旧模型并行，记录差异，再逐步替换。
6. 白名单、合作方和搜索引擎身份都有最小权限、过期时间和审计。

总成本可以写成：

~~~text
Cost = lambda_FN * 漏放敏感数据
     + lambda_FP * 误伤正常用户
     + lambda_latency * 额外延迟
     + lambda_compute * 风控计算成本
~~~

不同接口使用不同成本权重；券核销和实时会员价通常比公开标题更重。

## 14. 故障和应急

- Redis 或特征服务故障：L0/L1 在全局容量保护下有限放行；L2/L3 保留身份、授权和硬额度，必要时暂缓精确报价或券操作。
- 模型超时：回退到最近稳定模型或规则基线，标记 degraded。
- 签名密钥泄露：撤销受影响 key，轮换密钥，缩短上下文有效期，检查重放日志。
- 新型分布式采集：先降低敏感接口单主体和对象配额，增加挑战，再更新规则和模型。
- 误伤集中出现：暂停新规则，按地区和客户端回放，恢复旧策略。
- 事件调查：使用 request ID、最小必要日志和策略版本取证，隔离账号和凭据，通知安全、业务和法务负责人。

外部错误响应保持稳定，避免泄露价格存在性、券存在性和具体命中规则。内部告警可以保留详细 reason code。

## 15. 容易失败的做法

| 做法 | 问题 | 应改为 |
| --- | --- | --- |
| 只封云机房 IP | 合法合作方和共享网络误伤，代理可分散 | 账号、会话、设备、对象覆盖和全局配额 |
| 只检查 User-Agent | 可伪造，浏览器升级也会变化 | 多信号一致性和业务流程 |
| 把前端签名或隐藏字段当秘密 | 客户端必须能读取，容易被重放 | 服务端密钥、短时上下文、nonce |
| 只靠 CAPTCHA | 自动化浏览器可能通过，用户体验受损 | 按风险分层挑战并保留服务端额度 |
| 用 CORS 防抓取 | 只限制浏览器跨源读取 | 认证、授权、配额和源站保护 |
| 返回完整券列表再前端过滤 | 对方直接读取响应获得全量数据 | 服务端按资格最小化计算和返回 |
| 返回随机价格或假优惠 | 伤害用户信任并可能引发合规问题 | 真实数据的配额、延迟、授权和审计 |
| 一个阈值覆盖所有接口 | 公开价格、券核销和导出成本不同 | 按接口等级、主体类型和损失校准 |

## 16. 推荐落地顺序

### 第 1 阶段：基础保护

- 完成接口和敏感字段清单。
- 源站只允许网关或 CDN 访问。
- 对账号、会话、IP、SKU 和接口启用令牌桶、并发上限和超时。
- 券校验和核销加入服务端资格计算、nonce、幂等键和原子核销。
- 记录最小审计事件，确认不记录密钥和完整券明文。

### 第 2 阶段：风险识别

- 建立 10 秒、1 分钟、15 分钟和 24 小时特征窗口。
- 上线价格轮询、商品遍历、券枚举、重放和账号扇出规则。
- 影子运行可解释评分，按匿名、登录和合作方分层基线。
- 让 L2/L3 响应支持字段策略、短时签名上下文和统一错误。

### 第 3 阶段：模型和关系图

- 用审核标签训练逻辑回归或 LightGBM，并做概率校准。
- 用 Isolation Forest 或聚类发现未知群体，复杂图分析异步化。
- shadow → 小流量灰度 → 全量，具备一键回滚和白名单过期。
- 回放大促、价格变更、移动网络、共享出口和无障碍访问样本。

### 第 4 阶段：持续运营

- 监控模型漂移、接口成本、漏放字节量和真实用户误伤。
- 轮换服务端密钥、挑战参数和策略版本。
- 为合作方提供独立 API、明确字段、租户配额和审计。
- 定期复核价格透明义务、隐私留存和消费者体验。

## 17. 参考资料

- [OWASP Automated Threats to Web Applications](https://owasp.org/www-project-automated-threats-to-web-applications/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [RFC 6585：Additional HTTP Status Codes](https://www.rfc-editor.org/rfc/rfc6585)
- [RFC 9421：HTTP Message Signatures](https://www.rfc-editor.org/rfc/rfc9421)
- [RFC 7519：JSON Web Token](https://www.rfc-editor.org/rfc/rfc7519)
- [Redis Lua scripting](https://redis.io/docs/latest/develop/programmability/eval-intro/)
- [scikit-learn：Outlier Detection](https://scikit-learn.org/stable/modules/outlier_detection.html)
- [scikit-learn：Probability Calibration](https://scikit-learn.org/stable/modules/calibration.html)

**实现顺序：先保护源站和接口授权，再做多维配额与对象级特征，然后用可解释评分决定限速、挑战、字段裁剪和短期拒绝。所有算法权重和阈值必须通过业务基线、离线回放和灰度数据校准；单一 IP、UA、指纹或验证码都不应作为唯一判定依据。**
