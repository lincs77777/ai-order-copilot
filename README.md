# ai-order-copilot · AI 点餐助手

yshop-drink 点餐系统的旁路对话式 AI 助手：顾客用自然语言点餐、咨询门店，系统通过 Function Calling 调真实业务接口完成下单。

**设计依据**：《AI点餐助手详细设计文档_v1.2.md》　**实现记录**：docs/实现差异清单.md

## 能力

| 能力 | 说明 |
|---|---|
| 自然语言点餐 | 查菜单 → 确认规格 → 加购 → 改数量/删除 → 查购物车 |
| 下单闭环 | 订单预览（Java 权威算价）→ 前端显式确认 → 真实下单 → 引导支付 |
| 知识问答 | RAG（菜单 + 过敏原 + FAQ 语料）混合检索，带来源引用 |
| 安全防线 | Prompt 注入拦截、过敏原"安全断言"拦截、输出合规过滤、工具白名单 |
| 可降级 | LLM 熔断（规则推荐兜底）、上游故障回喂重试、超轮次终止 |
| 可观测 | 每轮 trace 落库可回放、Prometheus 指标、JSON 日志 + request_id 贯穿 |

## 架构

```
uniapp H5 (pages/ai-order)  ──SSE──▶  ai-service (FastAPI :8000)
                                          │  ├─ 鉴权：直读 yshop Redis token
                                          │  ├─ 意图分流 → 点餐 / 问答 / 拒绝
                                          │  ├─ Function Calling 循环（≤6 轮）
                                          │  ├─ 会话购物车（MySQL ai_copilot）
                                          │  ├─ RAG（Chroma + bge-m3 API）
                                          │  └─ 守卫 / 熔断 / trace
                                          ▼
                              yshop-drink-boot3 (:48081, 仅新增 /order/compute)
                                          ▼
                                   MySQL 5.7 + Redis 5.0
```

## 目录

```
app/
├── main.py                 FastAPI 入口（CORS / request_id / metrics / RAG 轮询）
├── core/                   config, errors, security(鉴权), ratelimit, java_client, logging, redis
├── api/v1/                 chat(SSE), order(confirm), session(历史+反馈), auth(dev /me), health
├── agent/                  orchestrator(FC循环), router(意图分流), context(历史/提示词), prompts/
├── tools/                  registry, menu, cart, order, shop, knowledge
├── llm/gateway.py          DeepSeek 流式客户端（tool_calls 聚合 / usage 计量 / 重试）
├── rag/                    embedding, store(Chroma), ingest, retriever(向量+BM25→RRF), sync_listener
├── guard/                  prompt_guard, hallucination_guard, content_filter
├── degeneration/fallback.py 熔断器 + 规则推荐
├── observability/          trace(落库), metrics(Prometheus)
├── models/                 ORM（7 张表）
data/knowledge/             运营语料（allergen.md / faq.md）
evals/                      数据集(50+30+20) + harness + runner + gate
scripts/                    init_db, init_vectorstore
deploy/                     docker-compose（演示）
```

## 快速开始

前置：MySQL 5.7、Redis（与 yshop 共用）、yshop-drink-boot3 已启动（端口 48081）。

```bash
# 1. 配置（真实 .env 不入库）
cp .env.example .env
#   必填：LLM_API_KEY（DeepSeek）、EMBEDDING_API_KEY（硅基流动）、MYSQL_URL、DEV_MOCK_UPSTREAM_TOKEN(dev)

# 2. 初始化
PYTHONPATH= .venv/Scripts/python -m scripts.init_db          # 建库建表
PYTHONPATH= .venv/Scripts/python -m scripts.init_vectorstore --shop-id 2   # 向量库 + 语料覆盖度报告

# 3. 启动
PYTHONPATH= .venv/Scripts/python -m uvicorn app.main:app --port 8000

# 4. 健康检查
curl http://127.0.0.1:8000/api/v1/health/ready
curl http://127.0.0.1:8000/metrics
```

> Windows 注意：pip/脚本命令一律加 `PYTHONPATH=` 前缀（规避本机 safe-delete shim）。

### 前端联调

`yshop-drink-uniapp-vue3`：`npm run dev:h5` → H5 登录 yshop → 访问 `/#/pages/ai-order/index`。
AI 服务地址在 `config/index.js` 的 `AI_API_URL`（dev 默认 `http://localhost:8000`）。

## 配置项（.env）

| 键 | 说明 |
|---|---|
| `JAVA_BASE_URL` | yshop 地址（:48081） |
| `AUTH_MODE` | `redis`（默认，直读 yshop Redis）/ `introspection`（需 Java 侧接口，未实现） |
| `YSHOP_REDIS_URL` | yshop token 所在 Redis（db 0） |
| `REDIS_URL` | ai-service 自用（db 2：限流/缓存） |
| `MYSQL_URL` | ai_copilot 库 |
| `LLM_*` | DeepSeek 直连（`llm/gateway.py` 留 adapter） |
| `EMBEDDING_*` | 硅基流动 bge-m3（1024 维） |
| `VECTOR_DB_BACKEND` | `chroma`（内嵌，dev）/ `milvus`（生产，adapter 待接） |
| `RATE_LIMIT_*` / `TOKEN_QUOTA_*` | 限流与配额 |
| `ORDER_PREVIEW_TTL_SECONDS` | 订单预览有效期（120s） |
| `DEV_MOCK_UPSTREAM_TOKEN` | dev 联调用：yshop mock token（`test{userId}`），生产留空 |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | SSE 流式对话（meta/intent/delta/tool/order_preview/done/error，均带 seq） |
| POST | `/api/v1/chat/cancel` | 取消进行中的生成 |
| POST | `/api/v1/order/confirm` | 确认下单（preview_id，三重校验：归属/TTL/金额复核） |
| GET | `/api/v1/session/{id}/messages` | 历史消息分页（仅本人） |
| POST | `/api/v1/feedback` | 点赞/点踩 |
| GET | `/api/v1/health`、`/health/ready` | 存活 / 就绪（Java、Redis、向量库） |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/api/v1/me` | dev 专用：查看 token 解析结果 |

## 工具清单

`list_dishes` / `get_dish_detail`（菜单，60s 缓存）、`add_to_cart` / `update_cart_item` / `remove_cart_item` / `get_cart`（会话购物车，写前实时校验）、`order_preview`（调 Java compute）、`order_submit`（internal_only，仅确认接口触发）、`get_shop_info`、`get_allergen_info`（RAG 语料）

## 评测与 CI 门禁

```bash
python -m evals.runner                 # 全量（50 点餐 + 30 问答 + 20 安全），约 5 分钟
python -m evals.runner --only faq      # 子集（约 1 分钟）
python -m evals.runner --quick 5       # 冒烟
python -m evals.gate                   # 仅校验 report.json 阈值
```

阈值（`evals/metrics.py`）：任务成功率 ≥90%、**过敏原保守率 =100%（红线）**、**注入拦截率 =100%（红线）**、意图准确率 ≥95%。
当前基线见 `evals/report.json`。评测口径约定见 docs/实现差异清单.md 末尾。

## 可观测性

- `ai_trace`：每轮一条（工具调用含耗时、检索命中、每轮 token、结束原因、降级标记）
- `/metrics`：chat / llm / tool / tokens / fallback / injection / order conversion
- 日志：JSON 格式，`request_id` 贯穿网关 → Agent → Java（`X-Request-Id`）

## 已知缺口

1. **X-Internal-Sign 仅客户端签名**，Java 侧无校验点（加校验需改 yshop 安全框架，违背最小侵入）
2. `AUTH_MODE=introspection` 依赖 Java 侧 `/app-api/ai/auth/verify`（未实现，默认走 Redis 直读）
3. 向量库仅接了 Chroma 内嵌；Milvus adapter 未接（`rag/store.py` 已留接口位置）
4. 堂食扫码（`desk`）模式需桌号，当前引导用户走自取/外卖
5. 会话状态库存于 MySQL（非 Redis），高并发场景需评估
6. CI workflow 需 self-hosted runner（要能访问内网 MySQL/Redis/yshop）
