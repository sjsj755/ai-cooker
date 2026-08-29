# 扩展点文档（P5）

> 本文件汇总 AI 厨师各模块的扩展点，供后续阶段（登录 / PWA / 国际化 / Docker /
> CI 生产化 / 新采集站点）按既定接口扩展，不破坏现有契约。

## 1. 采集适配器（新增站点）

- 扩展点：`app/crawlers/registry.py` + `app/core/crawler.py` 的 `RecipeCrawler` 基类。
- 新增站点实现 `fetch_index()` / `parse_page()` / `name` 后注册到 registry，
  采集管线（`scripts/crawl_recipes.py`）按站点分发；`robots.txt` 与请求延迟
  （`CRAWLER_DELAY_SECONDS`）由基类统一执行。
- 站点间差异被 `CrawledRecipe` / `CrawledIngredient` 中间结构隔离，入库走
  `RecipeCrawler.save()` 同一路径（幂等：`source_url` 唯一）。

## 2. 检索后端（替换 / 增加召回路）

- 扩展点：`app/core/retriever.py` 的 `Retriever` 接口（`retrieve(query, top_k)`），
  现有实现 `app/retrieval/hybrid.py`（BM25 + Chroma 双路 + RRF 融合）。
- 增加 Elasticsearch / Meilisearch 召回：实现 `Retriever` 后在
  `RankingService` 装配时替换 `retriever` 即可；缺料 / 忌口 / 评分 / 排序与召回解耦。
- 评分策略：实现 `app/core/scoring.py` 的 `ScoringStrategy` 并注入 `RankingService`。

## 3. LLM / 嵌入供应商（模型切换）

- LLM：`app/core/llm.py` 的 `LLMProvider`（`structured(prompt, schema)`）。
  `OpenAICompatibleLLM` 通过 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 切换
  DeepSeek / Qwen / OpenAI / 本地 Ollama 等 OpenAI 兼容端点；`LLM_MOCK=true` 时
  `get_llm_provider()` 返回确定性 `MockLLMProvider`（CI / 压测）。
- 嵌入：`app/core/embeddings.py` 的 `EmbeddingProvider`；`EMBEDDING_BASE_URL` 可切
  百炼 / OpenAI；`EMBEDDING_BATCH_SIZE` 对齐供应商单批上限。
- **模型升级硬性前置**：切换模型 → 重跑 `scripts/capture_llm_fixtures.py` →
  人工复核结构 → 结构变化则同步 mock 与校验 → 全量 pytest + 一致性回归全绿才允许上线
  （见 `tests/fixtures/llm_responses/README.md`）。

## 4. 限流存储与多 worker

- 扩展点：`app/core/rate_limit.py` 的 `build_limiter()`；`RATE_LIMIT_STORAGE=memory|redis`。
- memory 模式各进程独立计数（压测 / 单进程可用）；生产多 worker 必须
  `RATE_LIMIT_STORAGE=redis` + `RATE_LIMIT_REDIS_URL`（`scripts/start.sh` 强校验）。
- 新增桶：给对应路由加 `@_route_limit("N/minute")` 装饰器即可（recommend / feedback
  已有独立桶，其余默认 100/min）。

## 5. 反馈匿名化与盐轮换

- 指纹 = `SHA-256(IP + FEEDBACK_SALT)`，64 位十六进制，不落明文 IP。
- 轮换流程（默认“导出归档 + 清空”）：`scripts/export_feedback.py --out <归档> --truncate`
  → 更新 `FEEDBACK_SALT` → 重启；需要保留历史统计时改选 `salt_version` 方案（须显式
  按盐版本过滤并补对应用例与部署须知标注）。
- 幂等：唯一索引 `(recipe_id, client_fingerprint, action)`；切换 action 允许新增行。

## 6. 前端复用

- 页面脚本（`recommend.js` / `search.js`）持有业务状态，`ui.js` 只做无状态渲染；
  新页面直接复用 `renderChipInput` / `renderTagsPicker` / `renderCards` /
  `renderDetailDrawer` 与 `createDetailDrawerManager`。
- 请求层 `api.js` 提供任务级幂等 registry（`createTaskRegistry`），新任务类型
  （如反馈 `feedback`）默认 5s 超时，重任务可传更大 `timeoutMs`。
- 安全约定：一律 `createElement` + `textContent` 渲染，禁止 `innerHTML` /
  `insertAdjacentHTML` / `document.write` / `eval`（静态扫描测试兜底）。

## 7. 评测与可观测

- 检索 / 识别评测：`scripts/eval_retrieval.py` / `scripts/eval_recommend.py`
  支持 `--trace`（配 `LANGSMITH_API_KEY` 上传 runs，无 key 跳过）。
- 反馈指标与归档：`scripts/export_feedback.py`（JSONL + like/dislike 分布 +
  按菜谱聚合）。
- k6 压测：`scripts/k6/`（`common.js` 阈值模板 + 场景脚本），10k 硬门禁、
  50k 留痕（summary JSON + 硬件配置记录于 docs/P5_PLAN.md §8）。

## 8. 部署

- 生产入口：`scripts/start.sh`（Linux/bash）——启动前强校验 `FEEDBACK_SALT` 非空、
  `RATE_LIMIT_STORAGE=redis` 配 URL、多 worker 必配 Redis；`--check` 供编排预检。
- 非 bash 编排：须在部署编排中实现与 `start.sh` 等价校验，或在容器入口调用 `start.sh`。
- Windows 开发 / CI 直跑 `uv run uvicorn` 不受影响；压测 / 规模回归按 P5_PLAN §7
  假设默认 BM25 路径（无 embedding key 时天然降级）。

## 9. 后续阶段（范围外）

- 登录 / 用户体系（反馈表可外键关联用户，匿名指纹方案可平滑升级）；
- PWA / 国际化 / 主题；Docker 镜像与 CI 流水线生产化（本阶段仅产出部署须知）；
- 新采集站点适配器（见 §1）；真实 LLM 压测硬门禁（当前只记基线）。
