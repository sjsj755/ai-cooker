# P5 全量验收 + 用户反馈闭环 —— 实施计划

> 阶段状态：**P5 已完成（2026-08-29 验收通过：240 测试全绿 + 6 条 Playwright 冒烟 + k6 10k 门禁通过 + 50k 基线留痕；验收结果见第 8 节）。** 前置：P0（2026-08-28）、P1（2026-08-29）、P2（2026-08-29）、P3（2026-08-29）、P4（2026-08-29，提交 `6930e52`）、P4.1 与 P4.2（2026-08-29，提交 `da8b78e`；200 测试全绿 + 6 条 Playwright 冒烟）均已完成并验收。本文档依据 [docs/PLAN.md](PLAN.md) §6/§7、P1–P4.2 实际落地接口及用户评审意见（v6）编写，是 P5 的唯一实施依据。

## 1. 目标与范围

### 1.1 目标

- **k6 端到端压测**：search / detail / ingredients / tags / recommend（mock LLM）/ feedback 场景脚本化，10k 档硬门禁 + 50k 档基线留痕（不可跳过）。
- **性能门禁拆分**：无 LLM 路径与含 LLM 路径分开设闸；CI 用 mock LLM 保证确定性，真实 LLM 只记基线。
- **安全回归**：slowapi 限流（多 worker 一致 + 启动 fail-fast）、全量安全门禁清单（SQL 注入、Prompt 注入、XSS、CORS、密钥、uv audit）。
- **用户反馈闭环**：匿名收藏/不喜欢 API + 前端按钮 + 防刷（严格限流 + 哈希指纹幂等）。
- **LangSmith 评测消费**：eval 脚本支持 `--trace`，无 key 跳过；反馈数据导出与指标。
- **部署须知**：`scripts/start.sh` fail-fast（FEEDBACK_SALT / Redis）、非 bash 编排等价校验、盐轮换流程、50k 留痕。
- **扩展点文档**：`docs/EXTENSIONS.md`。

### 1.2 范围外（留给后续阶段）

- 登录 / 用户体系、PWA、国际化。
- Docker 镜像与 CI 流水线生产化（本阶段仅产出部署须知与 `start.sh`，不实现镜像/编排）。
- 真实 LLM 压测硬门禁（只记基线）。
- 新采集站点（扩展点已具备，不做新适配器）。

## 2. 前置条件（P0–P4.2 实际现状）

- 后端 API：`POST /api/recipes/recommend`、`GET /api/ingredients/search?q=&limit=`、`GET /api/tags?kind=`、`GET /api/recipes/search?q=&ingredients=&exclude_tags=&limit=`、`GET /api/recipes/{id}`（RecipeOut 自 P4.2 起含 `ingredients` / `seasonings`）。
- 反馈表：`user_feedback(id, recipe_id FK ON DELETE SET NULL, action like|dislike, created_at)` 已存在，**无 API、无前端接入**。
- 配置：`app/config.py` 现有 LLM / Embedding / 检索 / 采集 / 前端配置；**无限流、无 LANGSMITH、无 FEEDBACK_SALT 配置**。
- 依赖：`langsmith>=0.1` 已保留待 P3/P5 评测消费；**无 slowapi、无 redis、无 k6**。
- 运行时装配：`get_llm_provider()`（`app/graph/nodes.py`）无 `LLM_API_KEY` 时返回 None → 节点走降级；`LLM_MOCK` 在此接入；`get_embedding_provider()` 无 key 时返回 None → 检索天然降级 BM25。
- 测试基线：全量 200 用例全绿 + 6 条 Playwright 冒烟全绿。
- 造数：`scripts/seed_synthetic_recipes.py --count N --reset`（MySQL 侧幂等写入；规模回归默认 BM25 路径，向量路径不随 MySQL 行数变化、沿用 P2 基线）。
- 评测脚本：`scripts/eval_retrieval.py`（recall@5/coverage、单路 vs 混合、1k/5k 基线）、`scripts/eval_recommend.py`（18 条识别用例，实测 0.944 ≥ 0.85）。
- k6：本机 `winget install k6` 或 GitHub release；受限环境用 `docker run --rm -i grafana/k6 run - < scripts/k6/xxx.js` 兜底。

## 3. 设计决策

### 3.1 限流：slowapi + 多 worker 一致性 + 启动 fail-fast

- 配置：`RATE_LIMIT_ENABLED=false`（默认，不打扰本地/测试/压测）、`RATE_LIMIT_STORAGE=memory|redis`（默认 memory）、`RATE_LIMIT_REDIS_URL`（默认空）。
- 桶：recommend 10/min、feedback 20/min、其余默认 100/min；429 响应为友好 JSON（`{"detail": "请求过于频繁，请稍后重试"}`）。
- `RATE_LIMIT_STORAGE=redis` 但未配 `RATE_LIMIT_REDIS_URL` → 应用启动报错（配置校验 fail-fast）。
- **集成 slowapi 时先核实其配置是否原生支持 fail-fast；若不支持，在 `app/main.py` 的 lifespan 启动阶段对 Redis 执行 `ping()` 健康检查，失败即抛异常阻止应用启动（而非降级）**，并在部署须知记录。
- 部署须知：`uvicorn --workers N` / `gunicorn -k uvicorn.workers` 多 worker 下 memory 模式各进程独立计数、阈值按 worker 数放大，**生产多 worker 必须配 Redis**。
- 压测时 `RATE_LIMIT_ENABLED=false` 避免限流干扰测量；429 效果用单测 + 专用 k6 场景（`rate_limit.js`）验证。

### 3.2 FEEDBACK_SALT 生产 fail-fast（无校验盲区）

- 新增生产入口 `scripts/start.sh`（Linux/bash）：启动 uvicorn 前显式校验 `FEEDBACK_SALT` 已设置且非空，否则打印错误并 `exit 1`（**不是 WARN**——开发盐公开可知，生产误用开发盐将导致攻击者可反推全部指纹）。
- `start.sh` 同时校验：workers>1 时必须配置 `RATE_LIMIT_REDIS_URL`，否则 `exit 1`。
- 部署须知追加：**若 CI/CD 使用非 bash 环境，须在部署编排中手动实现与 `start.sh` 等价的校验逻辑，或在容器入口点中调用 `start.sh`**，确保生产不存在“校验盲区”。
- 开发/CI 直跑 `uv run uvicorn` 不受影响；生产只允许经 `start.sh` 或等价编排启动。

### 3.3 FEEDBACK_SALT 轮换流程（部署须知）

- 指纹 = SHA-256(IP + FEEDBACK_SALT)，盐轮换会使历史指纹全部失效。
- **默认方案：导出归档 + 清空**——轮换前执行 `scripts/export_feedback.py` 导出全量数据为归档快照（记录导出时间与条数）→ 清空反馈表 → 更新 `FEEDBACK_SALT` → 重启。此方案无版本过滤问题。
- **备选 `salt_version` 方案（仅当需保留历史统计时）**：迁移新增 `salt_version` 列并写默认值；**必须让 `scripts/export_feedback.py` 及所有相关统计查询（like/dislike 分布、按菜谱聚合等）显式按当前盐版本过滤**，并在部署须知中标注此项修改。
- 实施默认采用“导出归档 + 清空”；若改选 salt_version 方案，须补过滤逻辑、对应用例与部署须知标注。

### 3.4 mock LLM（结构真实性，非占位串）

- `LLM_MOCK=false` 默认；开启时 `get_llm_provider()` 返回 `MockLLMProvider`（`app/core/mock_llm.py`，实现 LLMProvider 接口，零网络 IO、确定性、时延 <1ms）。
- **parse 输出**：字段齐全的 `IngredientExtractionList`（raw_name / normalized_name / quantity / unit），量词剥离、种子字典别名映射；同输入恒同输出。
- **generate 输出**：`RecommendationSet`（title + 2~3 条含 minutes 的 steps + 自然语言段落 tips），**必须走与真实输出完全相同的 pydantic 强校验与防幻觉回填逻辑**（title/分数/缺料/难度/时长以候选集回填，LLM 只写 steps/tips），不得写死占位串、不得绕过校验。

### 3.5 LLM fixture 元数据与升级流程

- 真实 LLM 输出样例存 `tests/fixtures/llm_responses/`：
  - **`fixture_metadata.toml`（权威机器可读元数据）**：采集日期、LLM 模型版本（provider + model）、采集脚本与参数、脱敏说明、schema_version；由 Python 标准库 tomllib 解析并断言必填字段。
  - `README.md`：人工可读说明，指向 toml（**不做正则扫描 Markdown**）。
- 新增 `scripts/capture_llm_fixtures.py`：配 `LLM_API_KEY` 时调用真实 provider 采集 parse/generate 输出落盘并更新元数据；无 key 跳过并提示。
- **模型升级流程（硬性前置）**：升级/切换模型 → 重跑采集脚本生成新 fixture 与元数据 → 人工复核结构字段 → 若结构变化则同步调整 mock 与校验逻辑 → 全量 pytest + 一致性回归全绿后才允许上线。此流程保证不因升级静默失败，也不阻塞合理升级。

### 3.6 反馈防刷与幂等

- `POST /api/feedback`：recipe 不存在 404；action ∉ {like, dislike} 422；限流 20/min per IP（独立桶）。
- 迁移：`user_feedback.client_fingerprint VARCHAR(64)`（SHA-256(IP + FEEDBACK_SALT)，不落明文 IP）+ 唯一索引 `(recipe_id, client_fingerprint, action)`。
- 幂等：同 (recipe, fingerprint, action) 重复提交 → 200 幂等、不新增行；切换 action 允许新增一行。
- 匿名：无用户表、无明文 IP；写入为单条 INSERT、事务最小化，防止恶意高频调用灌入虚假数据并耗尽连接池。

### 3.7 性能门禁（拆分）

| 路径 | 门禁 |
|---|---|
| search（10k 档） | P95 < 200ms，错误率 < 1% |
| detail / ingredients / tags / health | P95 < 100ms，错误率 < 1% |
| recommend（mock LLM） | P95 < 5s，错误率 < 1% |
| recommend（无 LLM 降级直出） | P95 < 300ms |
| recommend（真实 LLM） | 只记基线（上限对齐前端 30s 超时） |
| feedback | P95 < 200ms，错误率 < 1% |
| search（50k 档） | 记录基线（硬门禁：必须留痕，见 §6.3） |

### 3.8 k6 场景划分

- `scripts/k6/`：`common.js`（BASE_URL、默认 options 与阈值）、`search.js`、`search_scale.js`（`RECIPES_SCALE=10k|50k`）、`detail.js`、`ingredients.js`、`tags.js`、`recommend_mock.js`、`recommend_real.js`（可选，仅记录基线）、`feedback.js`、`rate_limit.js`。
- thresholds 内置脚本（k6 `thresholds` 字段）；`--summary-export` 输出 JSON 供留痕。

### 3.9 评测与导出

- `eval_retrieval.py` / `eval_recommend.py` 增加 `--trace`：配置 `LANGSMITH_API_KEY` 时上传 runs；无 key 跳过并提示（与现有“无 key 降级”模式一致）。
- `scripts/export_feedback.py`：JSONL 导出（含导出时间、条数）+ like/dislike 分布 + 按菜谱聚合指标；兼作盐轮换归档工具。

### 3.10 前端反馈

- 推荐卡 / 搜索卡操作行新增“收藏 / 不喜欢”两个按钮：提交成功 disabled + `aria-pressed`；失败走现有错误/重试逻辑；无登录；请求走 `POST /api/feedback`（task registry 新增 `feedback` 任务类型，默认 5s 超时）。

## 4. 关键变更（接口 / 文件 / 配置 / 依赖）

### 4.1 接口

- 新增 `POST /api/feedback`：请求 `FeedbackIn{recipe_id: int, action: Literal["like","dislike"]}` → `FeedbackOut{id: int}`；404 / 422 / 429 语义见 §3.6。
- 其余 API 零变更（向后兼容）。

### 4.2 新增 / 修改文件

```
app/config.py                          # M RATE_LIMIT_* / LLM_MOCK / FEEDBACK_SALT / LANGSMITH 配置与校验
app/main.py                            # M lifespan：Redis ping() fail-fast（slowapi 无原生 fail-fast 时）
app/api/routes/feedback.py             # A 反馈路由（注册入现有 router）
app/schemas/feedback.py                # A FeedbackIn / FeedbackOut
app/core/mock_llm.py                   # A MockLLMProvider（LLMProvider 接口）
app/graph/nodes.py                     # M get_llm_provider 接入 LLM_MOCK
migrations/                            # A user_feedback.client_fingerprint + 唯一索引（salt_version 备选）
scripts/start.sh                       # A 生产入口 fail-fast（FEEDBACK_SALT / Redis）
scripts/capture_llm_fixtures.py        # A 真实 LLM 输出采集 + fixture_metadata.toml 更新
tests/fixtures/llm_responses/          # A fixture_metadata.toml + README.md + 样例 JSON
scripts/k6/                            # A common.js + 场景脚本（见 §3.8）
scripts/export_feedback.py             # A 导出 / 指标 / 归档
scripts/eval_retrieval.py              # M --trace
scripts/eval_recommend.py              # M --trace
frontend/js/{ui,recommend,search}.js   # M 反馈按钮 + aria-pressed + feedback 任务类型
frontend/css/style.css                 # M 反馈按钮样式
tests/                                 # A/M 见 §6
docs/EXTENSIONS.md                     # A 扩展点文档（实施后期）
docs/DB.md                             # M user_feedback 新列说明（实施时同步）
docs/P5_PLAN.md                        # A 本文档
docs/PLAN.md / README.md               # M 阶段状态同步
```

### 4.3 配置新增（app/config.py / .env.example）

```text
RATE_LIMIT_ENABLED=false
RATE_LIMIT_STORAGE=memory              # memory | redis
RATE_LIMIT_REDIS_URL=                  # storage=redis 时必填
RATE_LIMIT_DEFAULT_PER_MINUTE=100
RATE_LIMIT_RECOMMEND_PER_MINUTE=10
RATE_LIMIT_FEEDBACK_PER_MINUTE=20
LLM_MOCK=false
FEEDBACK_SALT=                         # 生产必填（start.sh 强校验）
LANGSMITH_API_KEY=                     # 可选；无 key 时 --trace 跳过
```

### 4.4 依赖

- 新增：`slowapi`（主依赖）；`redis`（可选——`RATE_LIMIT_STORAGE=redis` 时必需，实施时视 uv 依赖组策略放入主依赖或可选组）。
- 外部：k6（非 pip；README / 部署须知给安装与 docker 兜底命令）。
- 标准库：tomllib（Python 3.14 自带，无新增依赖）。

## 5. 实施顺序

1. **限流与启动 fail-fast**：config → slowapi 集成（桶、429）→ lifespan Redis ping（slowapi 无原生 fail-fast 时）→ `start.sh` + 子进程用例 → 部署须知草稿。
2. **mock LLM 与 fixture**：`mock_llm.py` → `get_llm_provider` 接入 → `capture_llm_fixtures.py` → fixture 目录（toml + README + 样例）→ 结构一致性回归 + toml 元数据断言。
3. **反馈闭环**：迁移（fingerprint + 唯一索引）→ feedback 路由 / schema / 幂等 / 限流桶 → 前端按钮 + aria-pressed + 冒烟更新 → `export_feedback.py` + 盐轮换部署须知。
4. **评测消费**：eval 脚本 `--trace` → 反馈指标。
5. **k6 压测**：脚本与阈值 → 10k 造数跑门禁 → 50k 造数跑基线并留痕（§8 记录数值 + 硬件配置 + 截图）→ `rate_limit` 场景。
6. **收尾**：`docs/EXTENSIONS.md` → 全量 pytest + 6 条冒烟 + k6 联跑 → 回填 §8 → 同步 docs/PLAN.md 与 README.md 为“已完成”。

## 6. 测试与验收门禁

### 6.1 功能测试

- feedback：合法 like/dislike 写入返回 id；非法 action 422；recipe 不存在 404；同 (recipe, fingerprint, action) 重复 200 幂等且不新增行；切换 action 新增行；`client_fingerprint` 为 64 位十六进制且不含明文 IP。
- 限流：`RATE_LIMIT_ENABLED=true` 时超桶 429 且 JSON 友好；recommend / feedback 独立桶；memory 与 redis（mock）两种存储单测；`storage=redis` 无 URL → 启动报错。
- lifespan：Redis ping 失败（monkeypatch / 不可达地址）→ 应用启动抛异常而非降级；成功 → 正常启动。
- mock LLM：零网络调用（断言 httpx 未被调用）、同输入同输出、parse/generate 输出通过真实校验与回填逻辑。
- 一致性回归：tomllib 解析 `fixture_metadata.toml` 断言必填字段（采集日期 / 模型版本 / 采集脚本 / schema_version）；mock 输出与真实 fixture 键集合、字段类型、嵌套结构一致；README 存在。
- 前端静态契约：反馈按钮存在、`aria-pressed` 同步、请求路径 `/api/feedback`、无危险 DOM API。
- export_feedback：JSONL 可解析、含时间戳与条数、like/dislike 分布与按菜谱聚合正确。

### 6.2 启动与生命周期

- bash 可用环境下子进程用例：未设 FEEDBACK_SALT → exit 1；workers>1 未设 RATE_LIMIT_REDIS_URL → exit 1；配置齐全 → 正常启动。
- 无 bash 环境：跳过脚本用例并在 §8 记录原因；部署须知检查项覆盖“非 bash 编排等价校验或容器入口调用 start.sh”。

### 6.3 压测门禁（k6）

- 前置：`uv sync` → `alembic upgrade head` → `seed_dictionary.py` → `seed_synthetic_recipes.py --count 10000` → 启动服务（`RATE_LIMIT_ENABLED=false`）。
- 命令：`k6 run scripts/k6/{search,search_scale,detail,ingredients,tags,recommend_mock,feedback,rate_limit}.js`；阈值内置。
- 50k 档：`seed_synthetic_recipes.py --count 50000` → `k6 run scripts/k6/search_scale.js`（`RECIPES_SCALE=50k`）。
- **50k 不可跳过**：受限环境无法执行时，实施者必须在 §8 备注栏记录本地/预发布环境的 50k 基线数值与硬件配置（CPU / 内存 / 磁盘 / MySQL 版本），并附 k6 摘要 / 报告截图（如 `.tmp_bridge/p5_k6_50k_*`）；无记录与截图视为 P5 压测门禁未通过；可在受限环境外执行但必须留痕。

### 6.4 安全回归清单

- SQL 注入：全量参数化复查 + 注入 payload 回归。
- Prompt 注入：模板隔离 + 输出强校验回归复跑。
- XSS：前端静态扫描（无 innerHTML / insertAdjacentHTML / document.write / eval）。
- 限流：429 + 多 worker 部署检查项。
- FEEDBACK_SALT：start.sh 强校验 + 非 bash 等价校验 + 盲区检查。
- CORS 白名单默认关闭；密钥仅环境变量；`uv audit`（chromadb 5 项已知漏洞记录在案：2026 年新披露、暂无修复版本，本地单用户部署风险有限，检索可降级 BM25 不阻断）。

### 6.5 验收命令

```powershell
uv sync
uv run alembic upgrade head
uv run python scripts/seed_dictionary.py
uv run python scripts/seed_synthetic_recipes.py --count 10000
uv run pytest
# 启动服务后 6 条冒烟
E2E_BASE_URL=http://127.0.0.1:8000 uv run python scripts/e2e/smoke_*.py
# k6 压测（RATE_LIMIT_ENABLED=false）
k6 run scripts/k6/search.js
k6 run scripts/k6/detail.js
k6 run scripts/k6/ingredients.js
k6 run scripts/k6/tags.js
k6 run scripts/k6/recommend_mock.js
k6 run scripts/k6/feedback.js
k6 run scripts/k6/rate_limit.js
# 50k 留痕
uv run python scripts/seed_synthetic_recipes.py --count 50000
k6 run scripts/k6/search_scale.js   # RECIPES_SCALE=50k，输出 summary JSON + 截图
# 反馈导出
uv run python scripts/export_feedback.py --out data/feedback_archive.jsonl
```

## 7. 假设

- fixture 元数据用 TOML，Python 3.11+ 标准库 tomllib 解析，无新增依赖；README 仅人工说明，门禁以 TOML 为准。
- `start.sh` 面向 Linux 生产环境；Windows 开发/CI 直跑 uvicorn；非 bash 编排由运维按部署须知实现等价校验；bash 不可用环境跳过脚本用例并留痕。
- slowapi 若原生支持 fail-fast 则优先使用其配置；否则按 lifespan ping 方案实施（实施时二选一，均写入部署须知）。
- 盐轮换默认“导出归档 + 清空”，无版本过滤问题；仅在需保留历史统计时改选 salt_version 方案，且必须显式过滤并标注部署须知。
- Redis 仅生产多 worker 必需；受限环境压测可用 memory + 单进程；50k 可在受限环境外执行但必须留痕。
- LangSmith 无 key 跳过；限流默认关闭、生产开启；反馈哈希不落明文 IP，保持匿名。
- fixture 升级流程为模型变更的强制前置。
- 规模回归默认 BM25 路径（无 embedding key 时天然降级）；混合路径沿用 P2 基线，不新增 embedding mock。

## 8. 验收结果（实施后回填）

> 验收时间：2026-08-29；环境：Windows 11 教育版 10.0.26200 / i7-13700H（14C/20T）/
> RAM 15.7GB / MySQL 8.0.29 / k6 v2.2.0 / Python 3.14 + uv。
>
> 安全回归补充：`uv audit` 实测 chromadb 1.5.9 存在 5 项已知漏洞
> （CVE-2026-45829 / 45830 / 45831 / 45833 + PYSEC-2026-311，均暂无修复版本）——
> 与 §6.4 预案一致：本地单用户部署风险有限，检索可降级 BM25 不阻断；其余 126 包无漏洞。
> SQL 注入 / Prompt 注入 / XSS / CORS / 密钥扫描由 tests/test_security_regression.py 等回归覆盖。

| 项目 | 结果 |
|---|---|
| 限流（slowapi / 桶 / 429 / 多 worker / fail-fast） | ✅ 完成：slowapi 0.1.10；recommend 10/min、feedback 20/min、其余默认 100/min；429 友好 JSON；`RATE_LIMIT_STORAGE=redis` 无 URL → Settings 校验抛错；lifespan Redis ping fail-fast；多 worker 校验见 `start.sh`；子进程端到端 + 单测覆盖（tests/test_rate_limit.py） |
| start.sh（FEEDBACK_SALT / Redis / 非 bash 等价校验） | ✅ 完成：FEEDBACK_SALT 空 → exit 1；storage=redis 无 URL → exit 1；workers>1 无 Redis → exit 1；`--check` 预检；非 bash 等价校验要求写入部署须知 §9。⚠ 本机 bash 不可用（WSL 未配置），脚本子进程用例 5 条跳过，已记录原因 |
| lifespan Redis ping fail-fast | ✅ 完成：ping 失败 → 启动抛 RuntimeError 而非降级；成功 → 正常启动（单测覆盖） |
| mock LLM（结构真实性 / 确定性 / 零网络） | ✅ 完成：`app/core/mock_llm.py`；parse 量词剥离 + 种子词典别名映射、同输入同输出；generate 事实字段由候选集回填；断言 httpx 未被调用；经真实 pydantic 校验与防幻觉回填（tests/test_mock_llm.py） |
| fixture 元数据与一致性回归（toml / capture 脚本 / 升级流程） | ✅ 完成：`tests/fixtures/llm_responses/`（toml + README + parse/generate 样例）；`scripts/capture_llm_fixtures.py` 无 key 跳过；tomllib 必填字段断言 + mock 与 fixture 键集/类型/嵌套一致（tests/test_llm_fixtures.py） |
| 反馈 API（幂等 / 防刷 / 匿名指纹 / 限流桶） | ✅ 完成：`POST /api/feedback`（404 / 422 / 200 幂等 / 切换 action 新增行）；指纹 64 位十六进制不含明文 IP；迁移新增 `client_fingerprint` + 唯一索引；独立 20/min 桶（tests/test_feedback_api.py + 子进程 429 端到端） |
| 前端反馈按钮与冒烟 | ✅ 完成：推荐卡 / 搜索卡“收藏 / 不喜欢”按钮 + aria-pressed + disabled + `feedback` 任务类型（5s 超时）；smoke_recommend_happy 增加反馈断言；6 条 Playwright 冒烟全绿 |
| LangSmith `--trace` / export_feedback 指标 | ✅ 完成：eval_retrieval / eval_recommend 支持 `--trace`（有 key 用 traceable 上传、无 key 跳过）；`scripts/export_feedback.py` JSONL 导出（时间戳/条数/like-dislike 分布/按菜谱聚合/--truncate 清空）；单测覆盖 |
| k6 门禁（10k 硬门禁 + 各场景） | ✅ 通过（k6 v2.2.0；BM25-only 路径；`RATE_LIMIT_ENABLED=false`）：search(5 VU) p95=120.7ms；search_scale 10k p95=115.0ms；detail p95=37.6ms；ingredients p95=24.9ms；tags p95=22.0ms；recommend_mock p95=148.4ms；feedback p95=42.3ms；各场景错误率 <1%；rate_limit 场景 30 连发中 10 次 429。summary JSON：`.tmp_bridge/p5_k6_10k/*.json` |
| **50k 留痕**（基线数值 / 硬件配置 / 截图路径 / 执行环境） | ✅ 记录（不可跳过）：50k 档 search_scale（5 VU × 30s）p95=423.6ms / avg=307.1ms / max=439.8ms / 错误率 0%（370 iterations）；summary：`.tmp_bridge/p5_k6_50k/search_scale_50k.json` + `summary.txt`；执行环境：Windows 11 教育版 10.0.26200 / i7-13700H（14C/20T）/ RAM 15.7GB / D: 空闲 67.3GB / MySQL 8.0.29（recipes=50007）/ BM25-only（EMBEDDING_API_KEY 置空）；headless 无 GUI 截图，以 k6 summary 文本 + JSON 留痕 |
| 盐轮换演练（导出归档 / 清空或 salt_version 过滤） | ✅ 完成：`export_feedback.py --truncate` 导出归档 + 清空演练（JSONL 可解析 / 时间戳 / 条数 / 分布 / 聚合 / 清空均有测试）；轮换流程见部署须知 §9 |
| 扩展点文档 docs/EXTENSIONS.md | ✅ 完成 |
| 全量测试（pytest 数量 / 6 条冒烟） | ✅ 240 passed + 5 skipped（start.sh bash 用例：本机 bash 不可用）+ 6 条 Playwright 冒烟全绿 |

## 9. 部署须知

### 9.1 启动与校验（无盲区）

- 生产只允许经 `scripts/start.sh` 或等价编排启动；`start.sh` 启动前强校验：
  1. `FEEDBACK_SALT` 已设置且非空（否则 `exit 1`，不是 WARN）；
  2. `RATE_LIMIT_STORAGE=redis` 时必须配 `RATE_LIMIT_REDIS_URL`；
  3. `WORKERS>1` 时必须配 Redis 限流（memory 模式各进程独立计数，无法跨进程一致）。
- 非 bash 编排（CI/CD / Windows 服务）必须在部署编排中实现与 `start.sh` 等价的校验
  逻辑，或在容器入口点调用 `start.sh`；容器入口可先执行 `./scripts/start.sh --check`
  做预检再启动。
- Windows 开发 / CI 直跑 `uv run uvicorn` 不受影响（限流默认关闭）。

### 9.2 限流与多 worker

- 默认 `RATE_LIMIT_ENABLED=false`（本地 / 测试 / 压测）；生产开启后：
  `RATE_LIMIT_STORAGE=memory` 单进程可用；多 worker 必须 `redis` +
  `RATE_LIMIT_REDIS_URL`（应用 lifespan 启动时对 Redis `ping()` 健康检查，
  失败即拒绝启动，不降级）。
- 压测时保持 `RATE_LIMIT_ENABLED=false` 避免限流干扰测量；429 效果用单测 +
  `scripts/k6/rate_limit.js` 单独验证。

### 9.3 FEEDBACK_SALT 轮换流程（默认“导出归档 + 清空”）

1. 执行 `uv run python scripts/export_feedback.py --out data/feedback_archive.jsonl`
   导出全量反馈为归档快照（记录导出时间与条数）；
2. 执行 `--truncate` 清空反馈表；
3. 更新 `FEEDBACK_SALT`（独立随机盐，禁止使用开发盐）；
4. 重启服务。

> 若需保留历史统计，改选 `salt_version` 方案：迁移新增 `salt_version` 列并写默认值，
> `export_feedback.py` 及所有相关统计查询必须显式按当前盐版本过滤，并同步对应用例。

### 9.4 k6 压测与 50k 留痕

- 安装：`winget install k6` 或 GitHub release；受限环境可用
  `docker run --rm -i grafana/k6 run - < scripts/k6/xxx.js` 兜底。
- 前置：`uv sync` → `alembic upgrade head` → `seed_dictionary.py` →
  `seed_synthetic_recipes.py --count N`（10k 门禁 / 50k 留痕）；
  规模回归默认 BM25 路径（无 embedding key 时天然降级），
  压测服务建议 `LLM_MOCK=true` + `RATE_LIMIT_ENABLED=false`。
- 门禁命令与阈值见 §6.3/§6.5；每个场景用 `--summary-export` 输出 JSON 留痕。
- **50k 不可跳过**：必须在 §8 记录基线数值 + 硬件配置 + summary JSON/截图，
  无记录视为门禁未通过。

### 9.5 性能优化说明（P5 落地）

- `BM25Corpus`：语料构建后探针（COUNT/MAX）短 TTL（默认 1s），内容未变不再
  每请求全量重载 MySQL 语料行；自定义 loader（测试隔离语料）不受影响。
- `HybridRetriever`：corpus / Chroma 实例级缓存；BM25-only 时不触碰 Chroma（省 count）。
- `MissingIngredientsCalculator`：缺料两查询合并为一次会话（单连接 checkout）。
- SQLAlchemy 连接池：`pool_size=10 / max_overflow=20`（10 并发 VU 场景）。
