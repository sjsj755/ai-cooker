# AI 厨师（ai-cooker）

基于已有食材的智能菜谱推荐系统：输入家里已有的食材（口语化描述即可，如“冰箱里有个大土豆和仨鸡蛋”），系统通过 LLM 识别食材、混合检索菜谱库，推荐“缺料最少、最可行”的菜谱，并给出做法步骤、缺料提示与替代建议。

架构决策、核心流程图与兜底策略见 [docs/PLAN.md](docs/PLAN.md)，数据库表结构见 [docs/DB.md](docs/DB.md)。

## 目录

- [功能特性](#功能特性)
- [工作原理](#工作原理)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [API 一览](#api-一览)
- [测试与质量保障](#测试与质量保障)
- [部署](#部署)
- [相关文档](#相关文档)

## 功能特性

- **口语化输入，LLM 识别**：用户自由描述即可，LLM 结构化提取食材，再经“精确 → 别名 → 包含 → 向量相似”四级映射落到标准食材词典；未命中食材自动标记并给出缺料提示。实测识别准确率 0.944。
- **混合检索，召回更准**：BM25 关键词与 Chroma 向量双路召回、RRF 融合排序，再叠加缺料数、覆盖率、难度与时长评分；向量库故障自动降级 BM25，服务不中断。实测 recall@5 = 0.755。
- **LangGraph 工作流编排**：推荐主流程 parse → link → filter → retrieve → rank → generate 有状态编排，节点可插拔、可单测；条件边承载“重试”“提前结束”等兜底分支。
- **防幻觉、防注入、防 XSS**：菜谱标题、评分、缺料、难度、时长等事实字段一律从候选集回填（recipe_id 白名单），LLM 只写 tips；提示词采用固定系统指令 + JSON 数据化嵌入；前端渲染全用 `createElement`/`textContent`，禁止 `innerHTML`。
- **首次访问秒出结果**：缓存未命中时先以快路径返回 MySQL 原文（`ai_pending=true`），后台单飞任务补全 AI 文案，前端轮询状态后自动替换；TTL 缓存、启动预热与 BM25 索引落盘共同消除冷启动与重复查询开销。
- **模型可替换**：LLM 与 Embedding 均为自研 OpenAI 兼容封装（httpx 直调、分批、指数退避），通过 `LLM_BASE_URL` / `EMBEDDING_BASE_URL` 一行配置即可切换 DeepSeek、Qwen、OpenAI 或本地 Ollama。
- **反馈闭环**：匿名收藏 / 不喜欢（SHA-256 指纹 + 幂等 + 限流），支持导出与 LangSmith 追踪评测。
- **工程化与部署**：pytest + Playwright 冒烟 + k6 压测门禁；Docker Compose 全栈 + Caddy 自动 HTTPS + 生产安全加固 + 备份脚本，GitHub Actions 自动执行。

## 工作原理

推荐请求由 LangGraph 有状态工作流驱动，各节点实现解耦、可单独替换：

```mermaid
flowchart LR
    A["用户输入"] --> B["parse：LLM 结构化识别食材"]
    B --> C["link：四级映射到食材词典"]
    C --> D["filter：清洗去重并构造检索词"]
    D --> E["retrieve：BM25 + 向量 · RRF 融合"]
    E --> F["rank：缺料数 + 评分 · Top-5"]
    F --> G["generate：LLM 生成推荐文案"]
    G --> H["推荐结果"]
```

每层都有明确的兜底行为，避免 AI 链路单点故障拖垮整个服务：

- parse 失败：自动重试 1 次，仍失败则返回“未能识别，请补充描述”；
- 检索候选为空：提前结束，提示补充食材或放宽忌口；
- Chroma 故障：retrieve 自动降级为仅 BM25；
- LLM 超时 / 不可用：generate 降级直出 MySQL 原文（步骤、难度、时长完整，仅 tips 缺失并带 notice）；
- MySQL 不可用：返回 503 友好错误，不进入工作流。

快路径（默认开启，`RECOMMEND_FAST_FIRST_ENABLED=true`）：缓存未命中时先秒级返回 MySQL 原文，后台任务仅执行 generate 补全 AI 文案，成功后才写入长 TTL 缓存；前端通过 `POST /api/recipes/recommend/status` 轮询感知完成。

## 技术栈

| 层面 | 选型 |
| --- | --- |
| 后端 | Python 3.14（uv）· FastAPI · SQLAlchemy 2 + Alembic · LangGraph · httpx |
| 数据 | MySQL 8（InnoDB + utf8mb4）· Chroma（本地持久化向量库）· Redis（限流存储，可选） |
| AI | OpenAI 兼容 LLM / Embedding，可切换 DeepSeek / Qwen / OpenAI / Ollama |
| 前端 | 原生 HTML / CSS / JS，FastAPI 同源托管，无构建链 |
| 工程化 | pytest · Playwright · k6 · Docker Compose · Caddy · GitHub Actions |

## 目录结构

```text
ai-cooker/
├── app/
│   ├── api/              # FastAPI 路由：health / ingredients / recipes / recommend / feedback
│   ├── core/             # LLM 与 Embedding 封装、检索抽象、限流、安全、TTL 缓存、提示词
│   ├── crawlers/         # 采集适配器（每站点一个，当前：下厨房）
│   ├── db/               # SQLAlchemy 引擎与会话
│   ├── graph/            # LangGraph 工作流：state / nodes / prompts / workflow
│   ├── ingestion/        # 两阶段采集管线：JSON 落盘、入库、语义分块
│   ├── models/           # SQLAlchemy 模型
│   ├── retrieval/        # BM25、混合检索、缺料计算、评分与排序
│   ├── schemas/          # API 出入参 Pydantic 模型
│   └── vector_store.py   # Chroma 集合封装
├── frontend/             # 原生前端：/ 推荐页、/search.html 搜索页
├── migrations/           # Alembic 迁移
├── scripts/              # 采集、种子、评测、e2e、k6、备份等脚本
├── tests/                # pytest 用例与 fixture
├── docs/                 # 架构、数据库与各阶段实施文档
├── .env.example          # 环境变量模板
├── docker-compose.yml    # MySQL + Redis + app + Caddy
├── Dockerfile
└── Caddyfile
```

`data/`（Chroma 持久化、采集产物、BM25 索引）在运行时创建，已被 `.gitignore` 忽略。

## 快速开始

### 环境要求

- Python 3.14+ 与 [uv](https://docs.astral.sh/uv/)（仅使用 Docker 部署时可省略）；
- MySQL 8.x（本地开发用 Docker 或本机实例均可）。

### 1. 准备数据库

方式 A：Docker 启动 MySQL（不影响宿主机环境）

```bash
docker run -d --name ai-cooker-mysql \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=ai_cooker \
  -e MYSQL_USER=ai_cooker \
  -e MYSQL_PASSWORD=ai_cooker \
  -p 3306:3306 \
  mysql:8.4
```

> 3306 已被占用时改用 `-p 3307:3306`，并同步修改 `.env` 中的 `DATABASE_URL`。

方式 B：本机已有 MySQL，用 root 预建库与账号

```sql
CREATE DATABASE IF NOT EXISTS ai_cooker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ai_cooker'@'localhost' IDENTIFIED BY 'ai_cooker';
GRANT ALL PRIVILEGES ON ai_cooker.* TO 'ai_cooker'@'localhost';
FLUSH PRIVILEGES;
```

### 2. 初始化并启动

```bash
cp .env.example .env   # Windows PowerShell 使用：Copy-Item .env.example .env
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dictionary.py
uv run uvicorn app.main:app --reload
```

启动后访问：

- 推荐页：http://127.0.0.1:8000
- 搜索页：http://127.0.0.1:8000/search.html
- 交互式 API 文档：http://127.0.0.1:8000/docs（`DOCS_ENABLED=true` 时）

### 3. 准备菜谱数据（二选一）

合成数据（无网络、无需密钥，适合开发与联调）：

```bash
uv run python scripts/seed_synthetic_recipes.py --count 10000
```

真实采集（可选；采集遵守下厨房 robots.txt，`ingest` 阶段需配置 `EMBEDDING_*`）：

```bash
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --limit 5
uv run python scripts/crawl_recipes.py --site xiachufang --stage ingest
```

### 4. 无 LLM Key 本地联调

在 `.env` 中设置 `LLM_MOCK=true` 即可获得确定性、零网络调用的推荐结果；接入真实模型时改为 `LLM_MOCK=false`，并配置 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`。

### 5. 快速验证

```bash
curl http://127.0.0.1:8000/health/ready

curl -X POST http://127.0.0.1:8000/api/recipes/recommend \
  -H "Content-Type: application/json" \
  -d '{"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []}'
```

## 配置说明

所有配置项通过环境变量 / `.env` 注入（模板见 [.env.example](.env.example)，默认值与校验见 `app/config.py`）。常用分组如下：

| 分组 | 关键变量 | 说明 |
| --- | --- | --- |
| 数据库 | `DATABASE_URL` | SQLAlchemy 连接串，默认指向本机 3306 的 `ai_cooker` 库 |
| LLM | `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI 兼容端点，可切换 DeepSeek / Qwen / OpenAI / Ollama；`LLM_MOCK=true` 使用确定性 mock |
| Embedding | `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY` | 同上；阿里云百炼 compatible-mode 单批上限 20，需设 `EMBEDDING_BATCH_SIZE=20` |
| 推荐工作流 | `RECOMMEND_TOP_K` / `RECOMMEND_FAST_FIRST_ENABLED` / `RECOMMEND_CACHE_TTL_SECONDS` 等 | Top-N、快路径开关、结果与 parse 缓存 |
| 检索与评分 | `RETRIEVAL_*` / `SCORING_*` | BM25 / 向量权重与 RRF 融合参数（两路权重之和必须为 1） |
| 启动预热 | `WARMUP_ON_STARTUP` / `WARMUP_WAIT_SECONDS` / `BM25_CACHE_ENABLED` | 后台预构建索引、冷启动收敛 |
| 限流 | `RATE_LIMIT_ENABLED` / `RATE_LIMIT_STORAGE` / 各接口配额 | 默认关闭；生产多 worker 必须使用 `redis` 存储 |
| 安全与部署 | `BEHIND_PROXY` / `FORWARDED_ALLOW_IPS` / `ALLOWED_HOSTS` / `DOCS_ENABLED` / `FEEDBACK_SALT` | 可信代理 IP、Host 白名单、文档开关、匿名反馈盐 |

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（含 DB 连通） |
| GET | `/health/live` | 存活探针，恒 200 |
| GET | `/health/ready` | 就绪探针（DB + Chroma），故障返回 503 |
| GET | `/api/ingredients/search?q=` | 食材联想（LIKE + 向量补充） |
| GET | `/api/recipes/search?q=&ingredients=&exclude_tags=` | 混合检索（BM25 + 向量 + 缺料 / 评分） |
| GET | `/api/recipes/{id}` | 菜谱详情（含食材 / 调料 / 步骤） |
| GET | `/api/tags` | 忌口 / 口味标签列表 |
| POST | `/api/recipes/recommend` | 推荐：`{ingredients, exclude_tags}` → `{recipes, degraded, notice?, ai_pending?}` |
| POST | `/api/recipes/recommend/status` | 快路径状态轮询：AI 文案就绪后返回完整结果 |
| POST | `/api/feedback` | 匿名收藏 / 不喜欢：`{recipe_id, action: like\|dislike}` |

请求示例：

```json
{
  "ingredients": ["土豆", "鸡蛋", "番茄"],
  "exclude_tags": ["辣"]
}
```

推荐结果中每条 `recipes` 包含菜谱标题、匹配度、缺料清单、难度、时长、做法步骤、tips 与所需调料，来源字段均由 MySQL 原文回填。

## 测试与质量保障

运行全部测试：

```bash
uv run pytest
```

> 测试需要可用的 `ai_cooker_test` 库：本机可用 `scripts/init_test_db.sql` 预建，CI 由 GitHub Actions 内置 MySQL 服务提供。

- **自动化测试**：覆盖采集解析、入库、检索、LangGraph 工作流、API、前端静态契约、安全、限流与部署脚本；最近一次全量验收（2026-08-31）为 313 passed + 13 skipped。
- **端到端冒烟**：6 条 Playwright 脚本（`scripts/e2e/`）覆盖推荐、搜索、详情抽屉、忌口过滤、降级横幅与食材联想。
- **压测门禁**：k6 场景位于 `scripts/k6/`，CI 在 10k 合成语料上执行 search / detail / ingredients / tags / recommend / feedback / rate_limit 门禁（实测 search P95 120ms、detail 38ms、recommend mock 148ms，错误率 < 1%）。
- **离线评测**：`scripts/eval_retrieval.py`（召回基线，实测 recall@5 = 0.755）、`scripts/eval_recommend.py`（识别准确率基线 ≥ 0.85，实测 0.944）。
- **CI**：每次 push / PR 自动执行 uv 同步 → 迁移 / 种子 → pytest → Playwright → k6，见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

## 部署

Docker Compose 全栈部署（app + MySQL + Redis + Caddy），Caddy 自动申请 HTTPS：

```bash
cp .env.example .env
# 填写生产必填项：MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD / REDIS_PASSWORD /
# CADDY_DOMAIN（已解析到本机的域名）/ ALLOWED_HOSTS / FEEDBACK_SALT
docker compose up -d --build
```

- app 容器启动前自动等待 MySQL 并执行 Alembic 迁移（`scripts/docker-entrypoint.sh`）；
- `data/` 目录绑定挂载，Chroma 与 BM25 索引随卷持久化；
- 生产默认关闭 `/docs`、启用安全响应头与 Host 白名单；可信代理 IP 白名单保证反代后限流与反馈指纹按真实客户端 IP 计数；
- 运维脚本：[scripts/start.sh](scripts/start.sh)（启动前 fail-fast 校验）、[scripts/backup.sh](scripts/backup.sh)（MySQL 无锁备份 + Chroma 目录打包，支持 `--dry-run` 预演）；
- 部署细节、验证过程与运维说明见 [docs/P6_PLAN.md](docs/P6_PLAN.md)。

## 相关文档

| 文档 | 说明 |
| --- | --- |
| [docs/PLAN.md](docs/PLAN.md) | 架构决策、核心流程图、兜底策略矩阵、阶段总览 |
| [docs/DB.md](docs/DB.md) | 数据库表结构、ER 图与 DDL |
| [docs/P1_COLLECTION_DESIGN.md](docs/P1_COLLECTION_DESIGN.md) | 采集设计：robots 合规、限速、断点续采 |
| [docs/P1_PLAN.md](docs/P1_PLAN.md) · [docs/P2_PLAN.md](docs/P2_PLAN.md) · [docs/P3_PLAN.md](docs/P3_PLAN.md) | P1 采集入库 / P2 检索层 / P3 推荐工作流实施与验收 |
| [docs/P4_PLAN.md](docs/P4_PLAN.md) · [docs/P4_1_PLAN.md](docs/P4_1_PLAN.md) · [docs/P4_2_PLAN.md](docs/P4_2_PLAN.md) | 前端与交互优化各阶段实施与验收 |
| [docs/P5_PLAN.md](docs/P5_PLAN.md) · [docs/P6_PLAN.md](docs/P6_PLAN.md) | 验收、限流、反馈闭环与部署上线实施与验收 |
| [docs/EXTENSIONS.md](docs/EXTENSIONS.md) | 扩展点：新增采集站点、检索后端、模型供应商 |
