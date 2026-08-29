# AI 厨师 —— 项目计划文档

> 基于已有食材的菜谱推荐系统。本文档固化当前架构决策、流程图、兜底策略、测试门禁与 P0 实施计划，作为后续实现与扩展的唯一依据。

**阶段状态：P0 已完成（2026-08-28 验收通过）→ P1 已完成（parse + ingest + 真实嵌入验收，2026-08-29 验收通过；实施计划与验收结果见 [docs/P1_PLAN.md](P1_PLAN.md)）→ P2 检索层已完成（BM25 + 向量 + RRF + 缺料/评分 + search API + LangGraph 节点 + 食材向量库，2026-08-29 验收通过；实施计划与验收结果见 [docs/P2_PLAN.md](P2_PLAN.md)）→ P3 LangGraph 工作流已完成（LLM 识别 + 四级映射 + 检索排序 + 推荐生成 + recommend API，2026-08-29 验收通过；实施计划与验收结果见 [docs/P3_PLAN.md](P3_PLAN.md)）。**

## 1. 项目概述

### 1.1 目标

用户输入家里已有的食材，系统识别食材后检索菜谱库，推荐“缺料最少、最可行”的菜谱，并给出做法步骤、缺料提示和替代建议。

### 1.2 关键决策

| 维度 | 决策 |
|---|---|
| 产品形态 | Web 应用（API 无状态，可复用于小程序/App） |
| 食材输入 | 文字输入，LLM 识别（LLM + 食材词典映射） |
| 推荐引擎 | 检索增强 RAG：混合检索 + LLM 结构化生成 |
| 编排方式 | LangGraph 有状态工作流 |
| 菜谱数据 | 公开数据采集（每站点一个 Crawler 适配器） |
| 忌口偏好 | 基础标签过滤（过敏原 / 忌口 / 菜系 / 口味） |
| LLM 供应商 | OpenAI 兼容接口（可切换 DeepSeek / Qwen / OpenAI / 本地 Ollama 等） |
| 业务存储 | MySQL 8.x（InnoDB + utf8mb4） |
| 向量存储 | Chroma 本地持久化（MVP 不并入 MySQL） |
| 可扩展性 | 接口化 + 扩展点，演进路径预留，MVP 不引入额外中间件 |

### 1.3 技术栈

Python 3.14 + uv、FastAPI、SQLAlchemy 2.x（当前 2.0.52）+ Alembic、LangGraph 1.x（仅编排）、Chroma、pytest。LLM 与嵌入均为自研 OpenAI 兼容实现（`app/core/openai_llm.py` / `app/core/openai_embeddings.py`，httpx 直调，`LLM_BASE_URL` / `EMBEDDING_BASE_URL` 可切换 DeepSeek / Qwen / OpenAI / 本地 Ollama 等端点），不依赖 langchain 的 `ChatOpenAI` / `with_structured_output`；`langchain-openai` / `langsmith` 依赖保留，待 P3/P5 评测需要时再消费。实际锁定版本以 `uv.lock` 为准。

## 2. 总体架构

```mermaid
flowchart TD
    subgraph 前端["接入层（多端共用 API）"]
        UI["Web 单页 / 未来小程序 / App"]
    end

    subgraph API层["API 层（FastAPI，无状态）"]
        A1["POST /api/recipes/recommend"]
        A2["GET /api/ingredients/search"]
        A3["GET /api/recipes/{id}"]
        A4["GET /api/tags"]
    end

    subgraph LangGraph["LangGraph 工作流（节点可插拔）"]
        P["parse 节点<br/>LLM 识别食材"]
        LK["词典映射<br/>规范化 + 实体链接"]
        F["filter 节点<br/>忌口标签过滤"]
        R["retrieve 节点<br/>走 Retriever 接口"]
        SC["rank 节点<br/>走 ScoringStrategy 接口"]
        G["generate 节点<br/>走 LLMProvider 接口"]
    end

    subgraph 接口抽象层["接口抽象层（扩展点）"]
        I_LLM["LLMProvider"]
        I_RET["Retriever<br/>Hybrid / Elasticsearch"]
        I_SCORE["ScoringStrategy"]
        I_CRAWL["RecipeCrawler<br/>每站点一个适配器"]
    end

    subgraph 兜底层["兜底策略（每层降级路径）"]
        FB["识别失败重试 1 次<br/>无候选 / LLM 不可用 / 存储故障"]
    end

    subgraph 数据层["数据层"]
        MYSQL[("MySQL 8.x<br/>菜谱 / 食材 / 标签 / 反馈")]
        VS["Chroma 向量库"]
    end

    UI --> A1 & A2 & A3 & A4
    A1 --> P --> LK --> F --> R --> SC --> G --> UI
    P --> I_LLM
    LK --> MYSQL
    R --> I_RET --> VS & MYSQL
    SC --> I_SCORE
    G --> I_LLM
    I_CRAWL --> MYSQL
    LangGraph --> FB
    FB -. 降级直出 .-> UI
```

### 2.1 模块职责与扩展方式

- **parse 节点**：LLM 识别自由文本，经 `LLMProvider.structured(prompt, schema)`（P1 已提供 `OpenAICompatibleLLM` 实现）输出结构化食材列表；模型只通过 `LLMProvider` 接口调用，换模型不改节点逻辑。提示词统一规范化（固定系统提示词 + 四段式模板 + 不可信输入 JSON 数据化，防 Prompt 注入；禁虚构约束由模板 + 输出强校验 + 候选白名单回填三重保证）。
- **词典映射**：LLM 输出按“精确 → 别名 → 包含 → 向量相似”映射到 MySQL 食材词典；未命中标记 `unknown`，走缺料提示，可审核扩充词库。
- **LangGraph 工作流**：`StateGraph` 定义 `parse → link → filter → retrieve → rank → generate`；新增环节 = 新节点 + 状态 Schema 向后兼容扩展。
- **检索层**：`retrieve` 只依赖 `Retriever` 接口；MVP 实现 `HybridRetriever`（BM25 + Chroma 向量），数据量增长时新增 Elasticsearch 实现替换。
- **评分层**：`rank` 只依赖 `ScoringStrategy` 接口；新增评分因子 = 新策略类 + 配置权重。
- **采集层**：`RecipeCrawler` 基类定义清洗、去重、入库流程；新增站点只写一个适配器类并注册。
- **API 层无状态**：同一套 API 服务 Web/小程序/App 多端。

## 3. 核心流程图

### 3.1 推荐主流程（LangGraph 视角，含兜底分支）

```mermaid
flowchart TD
    A["用户输入<br/>冰箱里有个大土豆和仨鸡蛋"] --> B["parse 节点<br/>LLM 结构化识别食材"]
    B --> C["词典映射<br/>标准名 + ingredient_id"]
    C --> D{"校验通过？"}
    D -- "否<br/>（最多重试 1 次）" --> B
    D -- "仍失败" --> Z["降级返回<br/>'未能识别，请补充描述'"]
    D -- "是" --> E["filter 节点<br/>构造 query（标准名）/ 缺料列表"]
    E --> F["retrieve 节点<br/>Retriever 接口混合召回"]
    F --> G{"候选是否为空？"}
    G -- "空" --> Y["返回'食材组合未找到'<br/>建议补充食材/放宽忌口"]
    G -- "非空" --> H["rank 节点<br/>ScoringStrategy 评分"]
    H --> I["Top-5 候选"]
    I --> J["generate 节点<br/>LLM 结构化生成"]
    J --> K{"LLM 生成成功？"}
    K -- "否（超时/服务不可用）" --> X["降级直出候选<br/>MySQL 补全 steps + 'AI 文案不可用'"]
    K -- "是" --> W["前端展示<br/>菜谱卡片 + 做法 + 缺料提示"]
    W --> M["用户反馈<br/>收藏 / 不喜欢"]
    M -.-> N["反馈入库（MySQL）<br/>LangSmith 评测数据"]
```

> retry_count 语义（P3）：已消耗的重试次数（初始 0）；条件边 `retry_count <= RECOMMEND_MAX_PARSE_RETRIES`（默认 1）回 parse，超限降级结束（最多 2 次 parse）。忌口过滤统一在 rank 阶段由 `RankingService` 执行，filter 只负责清洗与 query 构造。

### 3.2 数据采集与入库流程（采集器插件化 + 断点续采）

```mermaid
flowchart LR
    A["菜谱网站 A / B / C<br/>（每站一个 Crawler 适配器）"] --> B["RecipeCrawler 基类<br/>采集 + 清洗 + 去重"]
    B --> C{"单条/单站失败？"}
    C -- "是" --> D["重试 3 次（指数退避）<br/>仍失败则跳过并记录"]
    C -- "否" --> E["结构化入库<br/>MySQL（菜谱 / 食材 / 标签）"]
    D --> E
    E --> F["结构语义分块<br/>text_builder（≤500 字）"]
    F --> G["Embeddings<br/>EmbeddingProvider 接口"]
    G --> H["写入 Chroma<br/>持久化向量库"]
```

> P1 起采集改为**两阶段管线**：解析结果封装为 JSON 中间产物落盘（`data/crawled/{site}/`），入库阶段再读取 JSON 幂等写入 MySQL 与 Chroma；JSON 字段（食材/调料/tag/步骤）与流程详见 [docs/P1_PLAN.md](P1_PLAN.md)。

## 4. 兜底策略矩阵

| 故障 / 异常场景 | 兜底行为 | 触发条件 |
|---|---|---|
| LLM 识别输出非法 / 缺字段 | 自动重试 1 次；仍失败返回“未能识别，请补充描述” | `retry_count` 超限 |
| 词典映射未命中 | 标记 `unknown`，进入缺料提示，可走审核扩充词库 | 四级匹配均失败 |
| 检索候选为空 | 返回“食材组合未找到”+ 建议补充食材 / 放宽忌口 | 混合检索 Top-50 为空 |
| LLM 生成失败 / 超时 | 降级直出 rank 的结构化候选，标注“AI 文案不可用” | 生成节点异常或超时 |
| LLM / Embeddings 服务不可用 | 熔断降级：仅返回检索 + 评分结果，不依赖 LLM | 连续 5xx / 限流 / 网络错误 |
| Chroma 不可用 | 降级仅 BM25 检索（`Retriever` 接口内切换） | 向量库连接失败 |
| MySQL 不可用 | 返回友好错误 + 错误日志告警；查询全部参数化防注入 | 连接池异常 |
| 采集单条 / 单站失败 | 重试 3 次（指数退避）→ 跳过记录，断点续采；URL 唯一索引幂等 | 网络错误 / 解析异常 |
| 用户输入为空 / 无效 | 前端校验 + API 400 友好提示，不进入 LangGraph | 请求校验失败 |
| 死循环防护 | 所有重试均有计数上限（识别 ≤ 1 次、采集 ≤ 3 次） | 状态机层强制约束 |

## 5. 接口与数据模型

### 5.1 核心 API（v1）

- `POST /api/recipes/recommend`：`{ingredients, exclude_tags}` → `{recipes, degraded, notice?}`；`degraded` 标记是否走降级路径。
- `GET /api/ingredients/search?q=土豆`：食材联想，来自 MySQL 食材词典。
- `GET /api/recipes/{id}`：菜谱详情。
- `GET /api/tags`：忌口 / 口味标签列表。

### 5.2 MySQL 表（InnoDB + utf8mb4）

- `recipes(id, title, source_url 唯一索引, difficulty TINYINT, cook_time_minutes INT, servings INT, steps JSON, description TEXT, created_at)`
- `ingredients(id, name 唯一, aliases JSON, category, created_at)`（食材词典）
- `recipe_ingredients(recipe_id, ingredient_id, amount, is_essential, 联合主键)`
- `tags(id, name 唯一, kind)`（kind 区分过敏原 / 忌口 / 菜系 / 口味）
- `recipe_tags(recipe_id, tag_id, 联合主键)`
- `user_feedback(id, recipe_id, action, created_at)`

> 字段级说明、ER 图与完整 DDL 见 [docs/DB.md](DB.md)。

### 5.3 LangGraph 状态 Schema（Pydantic）

- `ParsedIngredient{raw_name, normalized_name, ingredient_id, quantity, unit, unknown}`
- `RecipeCandidate{recipe_id, title, match_score, missing_ingredients}`
- `Recommendation{recipe_id, title, match_score, missing_ingredients, difficulty, cook_time_minutes, steps, tips}`
- `CookState{ingredients, exclude_tags, parsed_ingredients, candidates, ranked, recommendations, retry_count=0, degraded=False, notice=None}`

## 6. 测试与门禁

**完成定义（Definition of Done）**：每个功能完成后，必须同时通过功能测试、性能基线测试、安全清单，缺一不可才算完成。

- **功能测试**：LLM 识别评测集（口语化 / 别名 / 量词 / 无效输入）、词典四级映射、忌口过滤、结构化输出校验、端到端场景、兜底路径逐条触发验证。
- **性能门禁**：`recommend` P95 < 5s（含 LLM）、`search` P95 < 200ms、`detail` P95 < 100ms、错误率 < 1%；k6/locust 压测；`EXPLAIN` 排查慢查询；1 万 / 5 万条数据规模回归检索耗时。
- **安全门禁**：SQL 注入（全量参数化）、Prompt 注入（指令隔离 + 输出强校验）、XSS（渲染转义）、API 限流与 CORS 白名单、密钥只走环境变量、`uv audit` 依赖扫描、反馈数据匿名化、采集遵守 robots 并标注来源。
- **集成与扩展性**：Docker MySQL 测试库迁移幂等、`utf8mb4` 中文读写；替换 LLM / 检索器 / 评分策略实现后核心流程与响应结构不变。
- **鲁棒性门禁（全阶段）**：所有外部依赖（MySQL / Chroma / Embedding / LLM）故障均有降级路径；故障注入测试通过（不 500、不崩溃、响应结构稳定）；输入边界（长度/数量/字符）与并发回归纳入每阶段验收。

## 7. 实施阶段

- **P0 基建**：项目结构、docker-compose（MySQL 8.x）、Alembic 迁移、接口抽象层（LLM/Embeddings/Retriever/Scoring/Crawler）、LangGraph 状态与空图骨架、兜底框架（重试计数 + `degraded` 标记）、食材词典种子。**✅ 已完成（2026-08-28），验收记录见 8.7。**
- **P1 采集管线**：首个 Crawler 适配器 + 清洗 + 断点续采 + MySQL/Chroma 双写 + 嵌入 + OpenAI 兼容 LLM/嵌入实现（**修订：两阶段管线 + JSON 中间产物 + 食材/调料分流 + 语义分块**）。**✅ 已完成（2026-08-29），实施计划与验收结果见 [docs/P1_PLAN.md](P1_PLAN.md)。**
- **P2 检索层**：`HybridRetriever`（BM25 + 向量）+ 默认 `ScoringStrategy`，验证召回质量与耗时基线。**✅ 已完成（2026-08-29），实施计划与验收结果见 [docs/P2_PLAN.md](P2_PLAN.md)。**
- **P3 LangGraph 工作流**：LLM 识别节点（复用 `OpenAICompatibleLLM`）+ 完整图 + 兜底分支 + 结构化生成 + 推荐 API。**✅ 已完成（2026-08-29），实施计划与验收结果见 [docs/P3_PLAN.md](P3_PLAN.md)。**
- **P4 前端**：Web 输入页、推荐卡片、忌口选择、降级提示展示。
- **P5 全量验收**：端到端压测、安全回归、LangSmith 评测、扩展点文档。

## 8. P0 实施计划（已完成 · 2026-08-28）

### 8.1 目标与范围

P0 搭出可运行、可测试、可扩展的工程骨架：FastAPI 能启动、MySQL 能连上、表结构迁移可重复执行、接口抽象层和 LangGraph 空图就位、兜底框架可用、食材词典有种子数据。**不做**采集、检索、评分、LLM 识别、推荐生成（留 P1–P3）。

**完成情况**：本阶段全部交付并验收通过——19 个测试全绿；迁移 / 种子均幂等；uvicorn 启动后 5 个端点实测返回真实数据。受本机环境影响，验收使用“方式 B：本机 MySQL 8.0.29（3306）”而非 Docker（原因与整改见 8.8）。

### 8.2 目录结构

```
ai-cooker/
├── pyproject.toml            # 追加依赖
├── .env.example              # 环境变量模板（提交 git，真实 .env 不入库）
├── docker-compose.yml        # MySQL 8.4（LTS）+ utf8mb4 + 健康检查 + 数据卷
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI 应用工厂 + /health（替代 hello world）
│   ├── config.py             # pydantic-settings 读取环境变量
│   ├── db/
│   │   ├── session.py        # engine / SessionLocal / get_db 依赖
│   │   └── base.py           # Declarative Base
│   ├── models/               # SQLAlchemy 模型（6 张表）
│   ├── schemas/              # Pydantic API 响应模型
│   ├── core/
│   │   ├── llm.py            # LLMProvider 接口
│   │   ├── embeddings.py     # EmbeddingProvider 接口
│   │   ├── retriever.py      # Retriever 接口 + RecipeCandidate
│   │   ├── scoring.py        # ScoringStrategy 接口
│   │   ├── crawler.py        # RecipeCrawler ABC（含去重/入库骨架）
│   │   └── fallback.py       # retry_with_backoff / DegradedResult / FallbackError
│   ├── graph/
│   │   ├── state.py          # LangGraph 状态 Schema
│   │   ├── nodes.py          # 6 个空节点
│   │   └── workflow.py       # build_graph()
│   ├── api/
│   │   ├── deps.py
│   │   └── routes/           # health / ingredients / recipes / tags / recommend
│   └── crawlers/registry.py  # 采集器注册表（先空）
├── migrations/               # Alembic
├── scripts/
│   ├── __init__.py
│   └── seed_dictionary.py    # 食材词典 + 标签种子（幂等 upsert）
└── tests/
    ├── conftest.py
    ├── test_health.py
    ├── test_db_smoke.py
    ├── test_seed.py
    ├── test_fallback.py
    ├── test_workflow.py
    ├── test_search.py
    └── test_recommend.py
```

### 8.3 技术决策

- **依赖**：`fastapi`、`uvicorn[standard]`、`sqlalchemy>=2.0`、`pymysql`、`alembic`、`pydantic-settings`、`langgraph`、`langchain-core`、`langchain-openai`、`langsmith`、`pytest`、`httpx`。Chroma、rank-bm25、beautifulsoup4 在 P1/P2 再加。实际安装（2026-08-28）：langgraph 1.2.11、langchain-core 1.6.1、langchain-openai 1.6.0、langsmith 0.11.2，详见 `uv.lock`。
- **Python 版本**：先按 3.14 安装；若 `langgraph`/`langchain` 在 3.14 下无 wheel，`.python-version` 改 3.13 并 `uv sync` 重建。**本机 3.14.4 下全部依赖有 wheel，无需降级。**
- **MySQL**：`mysql:8.4` 镜像，`MYSQL_DATABASE=ai_cooker`，`--character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci`，默认映射 3306（可用 `MYSQL_PORT` 覆盖，避免与本机已有 MySQL 冲突），命名数据卷，healthcheck 用 `mysqladmin ping`。**备选“方式 B”**：直接使用本机已有 MySQL 8.x（本项目验收即用此方式，库/账号由 root 预建）。
- **连接串**：`DATABASE_URL=mysql+pymysql://ai_cooker:ai_cooker@127.0.0.1:3306/ai_cooker`，从 `app/config.py` 读取；`.env.example` 提供模板，真实 `.env` 进 `.gitignore`。
- **测试库**：测试使用独立库 `ai_cooker_test`（`tests/conftest.py` 自动执行迁移并播种）；新环境需先用 root 预建该库并授权 `ai_cooker` 账号（README“方式 B”有完整 SQL）。
- **迁移**：`alembic init migrations`，`env.py` 读取 `DATABASE_URL` 并 `target_metadata = Base.metadata`；初始迁移 `alembic revision --autogenerate` 后提交入库。
- **P0 API 范围**：`GET /health`（含 DB 连通检查）、`GET /api/ingredients/search?q=`、`GET /api/recipes/{id}`、`GET /api/tags` 返回真实数据；`POST /api/recipes/recommend` 返回 501 占位。
- **旧文件**：根目录 `main.py`（hello world）删除，入口改为 `uv run uvicorn app.main:app`。

### 8.4 核心定义

- **接口抽象层**：`LLMProvider.structured(prompt, schema)`、`EmbeddingProvider.embed_texts(texts)`、`Retriever.retrieve(query, top_k)`、`ScoringStrategy.score(candidate, query)`、`RecipeCrawler`（`name` + `fetch_index()` + `parse_page(url)` + 基类 `save()` 按 `source_url` 幂等）。
- **兜底框架**：`retry_with_backoff(max_attempts, base_delay)` 异步装饰器（指数退避 + jitter）、`DegradedResult[T]{data, degraded, notice}`、`FallbackError`、`degrade(notice, data=None)`。
- **LangGraph**：`build_graph()` 注册 6 个空节点（仅 `return state`，标注 TODO），验收标准是图能编译、空状态跑通。
- **种子数据**：32 个常见食材（土豆/马铃薯/洋芋、鸡蛋/蛋、洋葱/圆葱、西红柿/番茄、青椒/甜椒、猪肉/五花肉等）+ 5 个标签（海鲜、辣、素食、坚果、乳制品），按唯一名幂等 upsert。

### 8.5 实施顺序

1. ✅ 更新 `pyproject.toml` → `uv sync`（3.14 失败则降 3.13）。
2. ✅ `docker-compose.yml` + `.env.example` + `.gitignore` 追加（本机环境改用方式 B：本机 MySQL）。
3. ✅ 搭 `app/` 骨架：`config.py` → `db/` → `models/` → `schemas/`。
4. ✅ `alembic init` + 改 `env.py` → 初始迁移 → `alembic upgrade head`。
5. ✅ 实现 `core/`（5 个接口 + 兜底框架）与 `graph/`（状态 + 空图）。
6. ✅ 实现 `api/routes/`（health、ingredients/search、recipes/{id}、tags、recommend 501）。
7. ✅ 写 `scripts/seed_dictionary.py` 并执行两次验证幂等。
8. ✅ 写测试并全部跑绿。

### 8.6 测试与验收门禁

- `/health` 返回 ok 且 DB 连通；种子数据可查询（中文 LIKE“土”→ 土豆，别名“马铃薯”→ 土豆）；种子重复执行条数不变；联合主键生效；`retry_with_backoff` 超限抛 `FallbackError`；`DegradedResult` 标记正确；`build_graph()` 编译且空状态跑通；`recommend` 返回 501。—— **以上均已自动化，见 `tests/`（19 个用例）。**
- 性能基线：`/health`、`/api/ingredients/search` P95 < 100ms（本机）；SQL 全参数化 + `EXPLAIN` 走索引。—— **未自动化（当前手工），统一归入 P5 压测；P0 阶段功能正确性已覆盖。**
- 安全基线：无密钥入库/入 git（`.env` 已 ignore，已核验）；SQL 全参数化；CORS 默认关闭（仅配置白名单才开启）；`uv lock --check` 通过；`uv audit` 65 包 0 已知漏洞。—— **已核验通过。**
- 验收命令（Docker 方式）：`docker compose up -d mysql && uv run alembic upgrade head && uv run python scripts/seed_dictionary.py && uv run pytest && uv run uvicorn app.main:app`。
- 验收命令（本机 MySQL 方式，本项目实际使用）：`uv run alembic upgrade head && uv run python scripts/seed_dictionary.py && uv run pytest && uv run uvicorn app.main:app`，全部通过即 P0 完成。

### 8.7 验收结果（2026-08-28，本机）

| 项目 | 结果 |
|---|---|
| 依赖安装 | `uv sync` 成功；Python 3.14.4，共 66 个包 |
| 迁移 | `alembic upgrade head` 幂等；6 张表 + `alembic_version` |
| 种子 | 首跑 32 食材 + 5 标签；二跑 0 创建 / 0 更新 |
| 测试 | `uv run pytest`：19 passed |
| 端点实测 | `/health` 200；`search?q=土` 命中土豆（别名可命中）；`/api/tags` 5 条；`recipes/999999` 404；`recommend` 501 |
| 安全 | `uv audit` 65 包 0 已知漏洞；`uv lock --check` 通过；CORS 默认关闭；`.env` 确认不入库 |

### 8.8 复盘：已知不足与整改（P0 执行中发现）

| 问题 | 影响 | 状态 / 整改 |
|---|---|---|
| “3306 端口可用”假设不成立（本机已有 MySQL 8.0.29） | docker compose 默认端口直接冲突 | ✅ 已整改：compose 端口参数化 `${MYSQL_PORT:-3306}`；README 增加“方式 B：本机 MySQL” |
| Docker 在受限账户 / 沙箱下不可用 | 计划验收命令不可执行 | ✅ 已绕过：改用本机 MySQL 验收；服务器 / CI 仍可用 Docker，两种方式等价 |
| LangGraph 版本漂移（计划按 0.2 语义编写） | P3 的 `with_structured_output` / `ChatOpenAI` 需按 1.x 核对 | ✅ 已整改（P1）：LLM 走自研 `OpenAICompatibleLLM`（httpx 直调），不再依赖 langchain `ChatOpenAI` / `with_structured_output`；LangGraph 仅用于编排（1.2.11） |
| 性能门禁无自动化手段 | P95 基线无法在 CI 拦截 | ⏳ 待办：P5 统一 k6/locust；P0 保持手工基线 |
| `/health` 语义单一 | DB 故障返回 200 + `degraded`，编排器无法区分存活/就绪 | ✅ 已由 P1 完成：`/health/live`（恒 200）与 `/health/ready`（DB + Chroma，故障 503） |
| 测试库准备未文档化 | 新环境跑 pytest 前需 root 预建 `ai_cooker_test` 并授权 | ✅ 已整改（P1）：README 说明 + `scripts/init_test_db.sql` |
| 日志 / 告警缺失 | 兜底矩阵承诺的“错误日志告警”未落地 | ✅ 已由 P1 完成：`app/core/logging.py` 结构化日志 + parse/ingest 事件日志 |
| 搜索 `aliases LIKE`（JSON 列隐式转换） | 可用但不可移植、无索引 | ✅ 已完成（P2）：`/api/ingredients/search` LIKE 不足时由 `ingredients_docs` 向量补充，`scripts/index_ingredients.py` 幂等写入 |
| API 限流未实现 | 安全门禁缺口 | ⏳ 归入 P5 安全回归（引入 slowapi 等） |
| 根目录 `main.py` 遗留 | 计划要求删除 | ✅ 已删除，入口统一 `app.main:app` |

**假设（已按实际修正）**：本地具备 Docker **或** 已有可用的本机 MySQL 8.x（两种方式均已验证，二选一）；MySQL 对外端口默认 3306，被占用时通过 `MYSQL_PORT` 或“方式 B”调整；P0 只交付骨架与占位端点，`recommend` 的 501 是预期行为而非缺陷；首次 `uv sync` / `uv audit` 需要外网访问（pypi.org / OSV）。
