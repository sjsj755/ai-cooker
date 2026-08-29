# AI 厨师（ai-cooker）

基于已有食材的菜谱推荐系统：用户输入家里已有的食材，系统识别食材后检索菜谱库，推荐“缺料最少、最可行”的菜谱，并给出做法步骤、缺料提示和替代建议。

架构决策、流程图、兜底策略与实施计划见 [docs/PLAN.md](docs/PLAN.md)；数据库表结构、ER 图与 DDL 见 [docs/DB.md](docs/DB.md)。

代码仓库：[github.com/sjsj755/ai-cooker](https://github.com/sjsj755/ai-cooker)（master）

## 技术栈

Python 3.14 + uv · FastAPI · SQLAlchemy 2.x + Alembic · LangGraph · MySQL 8.x（InnoDB + utf8mb4）· pytest

## 当前阶段：P0 已完成 → P1 已完成（parse + ingest）→ P2 检索层（已完成）

P1 采集管线实施计划见 [docs/P1_PLAN.md](docs/P1_PLAN.md)，设计文档见 [docs/P1_COLLECTION_DESIGN.md](docs/P1_COLLECTION_DESIGN.md)；P2 检索层实施计划见 [docs/P2_PLAN.md](docs/P2_PLAN.md)。

### P1（parse）交付物

- `app/crawlers/xiachufang.py`：下厨房适配器（PC/移动详情解析 + explore/分类/sitemap URL 发现），已注册 registry
- `app/core/seasoning_words.py`：33 组调料词表 + 别名归并（盐/油/生抽→酱油 等）与食材/调料分流
- `app/core/html_clean.py`：去 script/style/控制字符、空白归一
- `app/ingestion/json_store.py`：JSON 落盘（schema_version=1）、判重、`state.json` 断点、`failed.jsonl`
- `scripts/crawl_recipes.py`：`--site xiachufang --stage parse [--source explore|category|sitemap] [--limit N] [--dry-run] [--force] [--delay N]`
- 4 个真实页面 fixture + 26 个离线测试（全量 45 个通过）
- 真实抓取已验收：`parse --limit 5` 产出含 `ingredients/seasonings/tags/steps` 的 JSON；站点限流/反爬观测见设计文档 §15

### P1（ingest）交付物

- `app/ingestion/pipeline.py`：扫描 JSON → 严格校验 → MySQL 幂等入库（调料新建行 `category='调料'`，同名归并防主键冲突）→ 分块 → 嵌入 → 先删旧块再 Chroma upsert（防孤儿块）；无效信封移 `invalid/`、单条失败写 `failed.jsonl`、连续 5 次失败熔断退出码 3
- `app/core/openai_embeddings.py`：OpenAI 兼容真实嵌入（httpx 异步、分批、指数退避；缺 key 启动即退出码 3）
- `app/vector_store.py`：Chroma `recipe_docs` 集合（cosine、确定性 ID 幂等、维度冲突明确报错、关闭匿名遥测）
- `app/ingestion/text_builder.py`：结构单元分块——标题+描述/用料/每条步骤为不可切单元，贪心合并至 500 字、无字符 overlap，超长步骤按句号回退切分；块元数据含 `unit_type`/`step_start`/`step_end`
- 健康检查：`/health/live`（恒 200）、`/health/ready`（DB + Chroma，故障 503）
- `scripts/init_test_db.sql`：测试库预建脚本
- `app/core/openai_llm.py`：OpenAI 兼容 LLM 实现（P3 消费）——`structured(prompt, schema)` 输出经 JSON 提取 + pydantic 强校验；`LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 可切 DeepSeek / Qwen / OpenAI / Ollama 等端点
- 全量 81 个测试通过；真实 JSON 入库验收：7 条入 MySQL；**真实嵌入验收（2026-08-29）**：7 条 → 19 个语义块写入生产 `data/chroma`（阿里云百炼 `qwen3.7-text-embedding`，1024 维），重跑 0 新增、集合 19→19 稳定，`/health/live`、`/health/ready` 实测 200

采集使用：

```powershell
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --limit 5
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --dry-run
uv run python scripts/crawl_recipes.py --site xiachufang --stage ingest
# 真实嵌入需配置 EMBEDDING_API_KEY；本机验收用阿里云百炼 qwen3.7-text-embedding（1024 维），换服务商改 EMBEDDING_BASE_URL / EMBEDDING_MODEL
# P3 LLM 兼容：LLM_BASE_URL / LLM_MODEL / LLM_API_KEY（如 DeepSeek / Qwen / Ollama；密钥留空则不带鉴权头）
```

### P2（检索）交付物

- `app/retrieval/`：`BM25Corpus`（中文 bigram 分词、语料缓存探针 `(COUNT(*), MAX(id), MAX(updated_at))`、双缓冲 + 锁、重建失败四态）、`HybridRetriever`（BM25 + Chroma 双路，块级 RRF 证据均值 `rrf()` 原语、向量距离阈值 0.5、四态降级）、`MissingIngredientsCalculator`（调料排除、可用食材纯精确匹配）、`DefaultScoringStrategy`（融合归一 + 覆盖率 + 难度/时长微调）、`RankingService`（缺料数优先字典序排序）
- `GET /api/recipes/search`：`q` + `ingredients` + `exclude_tags` + `limit`，注册于 `/{recipe_id}` 之前；响应 `SearchResponse{recipes, degraded, notice}`；空结果带提示、MySQL 故障 503
- LangGraph：`CookState.query` + `retrieve_node`（`state.query` 为唯一检索文本）/ `rank_node`（Top-5）
- 食材联想向量库：`scripts/index_ingredients.py` 幂等写入 `ingredients_docs`；`/api/ingredients/search` LIKE 不足时向量补充合并去重，失败回退 LIKE-only
- `scripts/cleanup_orphan_chunks.py`（`--dry-run`）、`scripts/seed_synthetic_recipes.py`、`scripts/eval_retrieval.py`（50 用例 recall@5/coverage、单路 vs 混合、1k/5k 性能基线）
- 表结构变更：`recipes.updated_at`（DDL 级 `ON UPDATE CURRENT_TIMESTAMP(3)`，迁移 `b2e7f1c4a9d3`）
- 全量 134 个测试通过；真实环境验收（2026-08-29）：`GET /api/recipes/search?q=土豆 鸡蛋&ingredients=土豆,鸡蛋` 混合检索 `degraded=false`、缺 0 料排最前；无意义查询返回空 + notice；评测 recall@5=0.755（≥0.7）、混合 ≥ 单路；1k 语料构建 113ms/查询 P95 23ms、5k 构建 397ms/查询 P95 6.4ms

> 阿里云百炼 compatible-mode 的 embedding 单批上限 20：使用百炼时在 `.env` 设 `EMBEDDING_BATCH_SIZE=20`（已按此验收）。

当前文档记录的 P0 交付物：

- FastAPI 应用工厂 + `/health`（含 DB 连通检查）
- `docker-compose.yml`（MySQL 8.4）+ `.env.example` + `.gitignore`
- 6 张表 SQLAlchemy 模型与 Alembic 初始迁移（可重复执行）
- 接口抽象层：`LLMProvider` / `EmbeddingProvider` / `Retriever` / `ScoringStrategy` / `RecipeCrawler`（P1 已提供 OpenAI 兼容实现 `OpenAICompatibleEmbeddings` / `OpenAICompatibleLLM`）
- 兜底框架：`retry_with_backoff`（指数退避 + jitter）、`DegradedResult`、`FallbackError`、`degrade()`
- LangGraph 空图：`parse → link → filter → retrieve → rank → generate`，可编译、空状态跑通
- API：`GET /api/ingredients/search`、`GET /api/recipes/{id}`、`GET /api/tags` 返回真实数据；`POST /api/recipes/recommend` 返回 501 占位（P3 实现）
- 食材词典种子：32 个常见食材 + 5 个标签，幂等 upsert

## 快速开始

### 方式 A：Docker MySQL（推荐）

```powershell
cp .env.example .env
docker compose up -d mysql
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dictionary.py
uv run pytest
uv run uvicorn app.main:app --reload
```

### 方式 B：本机已有 MySQL

1. 用 root 创建库与账号：

```sql
CREATE DATABASE IF NOT EXISTS ai_cooker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ai_cooker'@'localhost' IDENTIFIED BY 'ai_cooker';
GRANT ALL PRIVILEGES ON ai_cooker.* TO 'ai_cooker'@'localhost';
FLUSH PRIVILEGES;
```

2. 复制 `.env.example` 为 `.env`（默认连接串即指向本机 3306），随后执行与方式 A 相同的迁移、种子、测试命令。

> 说明：P0 验收在本机使用方式 B（本机 MySQL 8.0.29 已监听 3306，与 docker-compose 端口冲突，二选一即可）。

## API 一览

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/health` | 健康检查（含 DB） | P0 完成 |
| GET | `/api/ingredients/search?q=` | 食材联想（LIKE + 向量补充） | P0 完成 / P2 增强 |
| GET | `/api/recipes/search?q=&ingredients=&exclude_tags=` | 混合检索（BM25 + 向量 + 缺料/评分） | P2 完成 |
| GET | `/api/recipes/{id}` | 菜谱详情 | P0 完成（空库返回 404） |
| GET | `/api/tags` | 标签列表 | P0 完成 |
| POST | `/api/recipes/recommend` | 推荐（LangGraph 工作流） | 501 占位，P3 实现 |

交互式文档：`http://127.0.0.1:8000/docs`

## 已知限制与待办

详见 [docs/PLAN.md](docs/PLAN.md) 第 8.8 节“复盘：已知不足与整改”。要点：

- API 限流与全量压测尚未自动化，归入 P5（P0/P1 手工基线、P2 检索 1k/5k 基线已记录）；
- 测试库 `ai_cooker_test` 需 root 预建并授权；
- 首次 `uv sync` / `uv audit` 需要外网访问。
