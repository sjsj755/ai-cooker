# P4.2 推荐页详情抽屉 + 食材/调料区分 + 感知性能优化 —— 实施计划

> 阶段状态：**P4.2 已完成并验收（2026-08-29，200 测试全绿 + 6 条 Playwright 冒烟通过）**。前置：P0（2026-08-28）、P1（2026-08-29）、P2（2026-08-29）、P3（2026-08-29）、P4（2026-08-29，提交 `6930e52`）、P4.1（2026-08-29）均已完成并验收。本文档依据用户需求与 [docs/P4_1_PLAN.md](P4_1_PLAN.md) 现有实现编写，是 P4.2 的唯一实施依据；第 8 节已回填验收结果。

## 1. 目标与范围

### 1.1 目标

- **推荐页复刻搜索页的“查看详情”抽屉**：推荐卡片新增「所需调料」行与「查看详情」按钮，点击弹出抽屉（ESC / 遮罩 / × 关闭、滚动锁定、焦点恢复与搜索页一致）。
- **食材 / 调料区分展示**：全站详情（推荐页 + 搜索页抽屉）区分「所需食材」与「调料」，卡片展示所需调料、抽屉展示完整用料（名称 + 用量）。
- **感知性能优化**：打开抽屉立即渲染骨架 + 加载圆环（复用 `.is-loading` 动画），数据返回后再替换内容；推荐卡折叠改为「数据未变时增量切换 `hidden` / `aria-expanded`」，仅数据变化时才全量重建 DOM。
- **消除重复逻辑**：抽取 `createDetailDrawerManager.js`，两页共用的抽屉状态机（缓存 / 防重发 / 切换 abort / 焦点恢复 / 清空）统一由工厂管理。

### 1.2 范围外（留给后续阶段）

- 用户反馈 / 收藏闭环、登录、PWA、国际化（沿用 P5 划分）；
- 单页化 / 组件框架等大重构；
- 全量浏览器回归与压测（P5）。

## 2. 前置条件（P4 / P4.1 实际现状）

- 前端：`frontend/{index.html, search.html, css/style.css, js/api.js, js/ui.js, js/recommend.js, js/search.js}`；P4.1 已落地设计令牌、推荐卡做法折叠（全量重建 + `data-toggle-id` 焦点保持）、详情抽屉（滚动锁定 + 焦点恢复）、favicon。
- 后端：`POST /api/recipes/recommend` 返回 `Recommendation{recipe_id, title, match_score, missing_ingredients, difficulty, cook_time_minutes, steps, tips}`；`GET /api/recipes/{id}` 返回 `RecipeOut`（无用料字段）；`recipe_ingredients JOIN ingredients` 按 `category='调料'` 区分调料（真实库 246 行关联、125 行调料）；缺料计算已排除调料。
- 测试基线：P4.1 全量 194 测试通过 + 6 条 Playwright 冒烟通过。

## 3. 设计决策

### 3.1 抽屉状态机抽取（createDetailDrawerManager.js）

- 新增 `frontend/js/createDetailDrawerManager.js`：`createDetailDrawerManager({ registry, drawerRoot })` → `{ open(recipe), close(), clear() }`，依赖全局 `Api` / `UI`（与 api.js / ui.js 现有全局契约一致）。
- `open`：同卡防重发（`openedDetailId` 比对）→ 捕获 `document.activeElement` 为触发元素 → `registry.abort("detail")` 中断在途详情 → 缓存命中直接渲染；未命中**立即渲染加载骨架**再异步取数。
- 数据返回：`openedDetailId` 比对防乱序后 `UI.renderDetailDrawer(...)` 替换内容；失败销毁骨架后 `UI.renderError(drawerRoot, { onRetry })`（重试仅当目标仍是当前卡片）。
- `close`：置空 + `isConnected` 防御恢复触发焦点（作为抽屉 `onClose` 回调）；`clear`：abort `detail` + 重置缓存 / 状态 + 清空容器 + 移除 `drawer-open`（供页面“清空”调用）。
- **监听生命周期**：ui.js 抽屉挂载助手返回 `{ body, close, destroy }`，`destroy` 幂等（解绑 keydown + 清空 + 解锁）；管理器在「骨架 → 内容」替换与关闭时调用，杜绝 keydown 监听累积。

### 3.2 加载骨架（感知性能优化）

- ui.js 新增 `renderDrawerLoading(container, recipe, options)`：复用抽屉外壳（标题 / 关闭按钮 / `drawer-open` 滚动锁定 / ESC / 遮罩 / 关闭聚焦），body 内渲染 `.drawer-loading`（`.spinner` 圆环 + “正在加载详情…”）。
- `.spinner` 与 `.btn.is-loading::after` 共用 `@keyframes spin`（样式层统一圆环动画）。
- 打开抽屉瞬间即出现骨架（消除等待焦虑），详情 GET 返回后整屏替换为完整内容；期间 ESC / 遮罩 / × 仍可关闭。

### 3.3 折叠增量更新（与 P4.1 全量重建决策的关系）

- P4.1 的“每次折叠切换都全量重建”在长步骤卡片下产生不必要的 DOM 重建开销（5 张 × 20 步可能接近 20ms）。
- P4.2 优化：recommend.js 新增 `lastRenderedResults`（渲染时记录 `results` 数组引用）；`toggleSteps` 计算 `expandedCardId` 后做**浅比较（引用相等，折叠不产生新数组）**：
  - 数据未变 → 仅遍历现有 `.card-toggle` / `steps-{id}` 同步 `aria-expanded` 与 `hidden`（一次只展开一张、无中间态），再按 `data-toggle-id` `focus({ preventScroll: true })`；
  - 数据变化（提交 / 清空必然产生新数组）→ 仍走 `renderCards()` 全量重建。
- ui.js 保持无状态契约（增量 DOM 手术归属页面脚本）；aria 同步与焦点保持语义与 P4.1 一致。

### 3.4 食材 / 调料契约与后端回填

- 新增输出 Schema `IngredientItem{name, amount?}`（`app/schemas/recipes.py`）。
- `GET /api/recipes/{id}`（`RecipeOut`）新增 `ingredients` / `seasonings`（默认空列表）；`get_recipe` 查询 `recipe_ingredients JOIN ingredients`，按 `category=='调料'` 拆分为 食材 / 调料，`order_by(ingredient_id)` 保证确定性输出。
- `Recommendation` 新增 `seasonings: list[IngredientItem] = []`；`generate_node` 成功路径（`_validate_recommendations`）与降级路径（`_degrade_recommendations`）均通过 `_load_seasonings(recipe_ids)` 一次查询回填，**以 MySQL 为准、不信 LLM 输出**；MySQL 失败沿用 `RetrievalUnavailableError → 503` 语义。
- 调料判定与缺料计算一致：`ingredients.category == '调料'`（`调味` / `香辛料` 等其它分类不算调料）。
- **索引门禁（EXPLAIN 实测）**：单条与 `IN` 批量 `recipe_ingredients JOIN ingredients` 查询均走 `PRIMARY`——`recipe_ingredients` 用组合主键 `(recipe_id, ingredient_id)` 前缀（ref / range），`ingredients` 用主键 eq_ref，**无需新增索引**；实施时已复跑确认，结果记录于 §8。

### 3.5 卡片与抽屉展示

- 推荐卡结构：头行（标题 + 徽章）→ 元信息行（难度 / 时长）→ 缺料行 → **所需调料行**（`.seasonings` chips，仅名称，空则不渲染）→ 操作行（`.card-actions`：「做法」折叠 + 「查看详情」抽屉）→ 常驻 `hidden` 展开区（步骤 + 贴士）。
- 详情抽屉结构：描述 → 元信息（难度 / 时长 / 份数）→ **所需食材**（`.drawer-ingredients`）→ **调料**（`.drawer-seasonings`）→ 做法步骤 → 来源外链；空列表不渲染区块；条目显示名称 + 可选用量（`.drawer-amount`）。
- 搜索页卡片不变（无步骤、保留“查看详情”）；搜索页抽屉因共用渲染函数自动获得食材 / 调料区块。

## 4. 关键变更（接口 / 文件）

### 4.1 接口

- `POST /api/recipes/recommend`：`Recommendation` 新增 `seasonings: list[{name, amount?}]`（向前兼容）。
- `GET /api/recipes/{id}`：`RecipeOut` 新增 `ingredients` / `seasonings`（向前兼容）。
- 无 Schema 破坏性变更、无表结构 / 迁移变更。

### 4.2 新增 / 修改文件

```
app/schemas/recipes.py                     # M IngredientItem + RecipeOut.ingredients/seasonings
app/graph/state.py                         # M Recommendation.seasonings
app/graph/nodes.py                         # M _load_seasonings + 成功/降级路径回填
app/api/routes/recipes.py                  # M get_recipe 加载并拆分食材/调料
frontend/js/createDetailDrawerManager.js   # A 抽屉状态机工厂
frontend/js/ui.js                          # M 抽屉挂载助手 / renderDrawerLoading / 卡片调料行 / 抽屉用料区块
frontend/js/recommend.js                   # M 接入管理器 + 折叠增量更新（lastRenderedResults）
frontend/js/search.js                      # M 删除内联抽屉状态机，接入管理器
frontend/index.html / search.html          # M 加载管理器脚本；index.html 新增抽屉容器
frontend/css/style.css                     # M .spinner / .drawer-loading / .seasonings / .card-actions
tests/helpers.py                           # M add_recipe 支持 (name, amount) 调料
tests/test_generate.py                     # M 成功/降级调料回填
tests/test_recommend_api.py                # M 键集 + 调料内容 + LLM 覆盖
tests/test_search_api.py                   # M 详情接口食材/调料拆分 + 空列表
tests/test_frontend.py                     # M 抽屉管理器 / 骨架 / 调料 / 增量折叠静态契约
scripts/e2e/smoke_recommend_happy.py       # M 调料行 + 抽屉加载骨架 + 详情区块断言
docs/P4_2_PLAN.md                          # A 本计划（§8 回填验收）
```

### 4.3 配置 / 依赖

- 无新增配置与依赖（全部原生 JS，无第三方资源）。

## 5. 实施顺序

1. 后端 Schema / 状态 / 节点回填 / 详情接口（含 EXPLAIN 索引门禁复跑）。
2. `createDetailDrawerManager.js` + ui.js 抽屉挂载助手与加载骨架。
3. ui.js 卡片调料行 / 抽屉用料区块；recommend.js 接入管理器 + 折叠增量更新；search.js 接入管理器。
4. HTML 脚本加载与抽屉容器；CSS 新样式。
5. 测试（helpers / generate / recommend_api / search_api / frontend 契约）与冒烟更新。
6. 全量 pytest + 6 条冒烟联跑 + 浏览器视觉截图 → 回填 §8 并同步 docs/PLAN.md 与 README.md。

## 6. 测试与验收门禁

### 6.1 功能测试

- generate 成功路径：LLM 伪造 `seasonings` 时以 MySQL 回填为准（含 name/amount 断言）。
- generate 降级路径：MySQL 原文直出时同样回填调料。
- recommend API：`set(rec)` 精确键集含 `seasonings`；200 与降级用例断言调料内容。
- 详情接口：`ingredients` / `seasonings` 按 `category=='调料'` 拆分正确、无关联行返回空列表、既有字段不受影响。
- 前端静态契约：`createDetailDrawerManager.js` 含 `detailCache` / `openedDetailId` / `detailTrigger` / `isConnected` / `drawer-open` 且两页均实例化；ui.js 含 `renderDrawerLoading` / `spinner` / `drawer-ingredients` / `drawer-seasonings`；recommend.js 含 `lastRenderedResults`（增量折叠）与 `preventScroll`；index.html 含 `detail-drawer-root`。

### 6.2 冒烟

- `smoke_recommend_happy`：卡片 `.seasonings` 可见；点击「查看详情」（浏览器侧延迟详情 GET 1.5s）先断言 `.drawer-loading`，随后断言抽屉食材 / 调料区块可见；保留折叠 / aria / 焦点 / 提交 disabled 断言。
- 其余 5 条（autocomplete / exclude_tags / degraded / search / search_detail）不受影响。

### 6.3 验收命令

```powershell
uv run pytest                              # 全量用例全绿（200 passed）
uv run uvicorn app.main:app --port 8002    # 浏览器手测清单全过
E2E_BASE_URL=http://127.0.0.1:8002 uv run python scripts/e2e/smoke_*.py   # 6 条冒烟全绿
```

## 7. 假设

- 「调料」严格按 `ingredients.category == '调料'` 判定（与缺料计算一致）。
- 卡片调料 chips 只显示名称；用量（amount）仅在详情抽屉展示。
- 详情抽屉数据一律走 `GET /api/recipes/{id}`（管理器统一缓存 + 同卡防重发），即使推荐响应已带 steps 也不在抽屉里直接复用。
- 折叠浅比较以 `results` 数组引用相等为准（提交 / 清空必然产生新数组，天然触发全量重建，无陈旧 DOM 风险）。
- 抽屉骨架与错误态不改变现有“不白屏 + 可重试”语义；`createDetailDrawerManager.js` 依赖全局 `Api` / `UI`，不引入新依赖。

## 8. 验收结果（实施后回填）

| 项目 | 结果 |
|---|---|
| 后端契约（recommend / detail） | ✅ `Recommendation.seasonings` 与 `RecipeOut.ingredients/seasonings` 均落地且向前兼容（默认空列表）；`_load_seasonings` 在成功与降级路径回填、以 MySQL 为准（LLM 伪造调料被覆盖，API 实测断言）；详情接口按 `category=='调料'` 拆分、无关联行返回空列表 |
| 索引门禁（EXPLAIN） | ✅ 实施时复跑单条与 `IN` 批量查询：`recipe_ingredients` 均 `key=PRIMARY`（组合主键前缀 ref / range）、`ingredients` `key=PRIMARY`（eq_ref），无需新增索引 |
| createDetailDrawerManager.js | ✅ 工厂封装 `detailCache` / `openedDetailId` / `detailTrigger` / 防重发 / 切换 abort / 焦点恢复 / 清空；recommend.js 与 search.js 均实例化；`destroy` 生命周期杜绝 keydown 监听累积 |
| 抽屉加载骨架（感知性能） | ✅ `renderDrawerLoading` 复用抽屉外壳 + `.spinner` 圆环（与 `.is-loading` 共用 `@keyframes spin`）；打开即出骨架、数据返回后替换内容，ESC / 遮罩 / × 加载期间可关闭；冒烟断言 `.drawer-loading` 先于内容出现 |
| 折叠增量更新（浅比较） | ✅ recommend.js 以 `lastRenderedResults === results` 引用浅比较：数据未变仅切换 `hidden` / `aria-expanded` 并按 `data-toggle-id` 恢复焦点，数据变化才全量重建；aria 同步与焦点保持语义与 P4.1 一致 |
| 食材 / 调料展示 | ✅ 推荐卡新增「所需调料」行（`.seasonings` chips，空不渲染）；详情抽屉新增「所需食材」（`.drawer-ingredients`）与「调料」（`.drawer-seasonings`）区块（名称 + 用量）；搜索页抽屉自动获得同样展示 |
| 匹配度展示（交付后修复） | ✅ `match_score` 为 RRF 融合分（绝对量纲极小，上界约 0.016），此前直接 `×100` 导致真实数据全部显示 1%；`renderCards` 改为按本批最高分归一为相对百分比（0-100）再渲染徽章，推荐 / 搜索两页共用，无需改后端契约 |
| 测试 | ✅ 全量 `uv run pytest`：200 passed（P4.1 194 + 新增 6：generate 成功/降级回填、详情拆分、详情空列表、index 抽屉容器等）；前端静态契约与 API 键集同步更新 |
| Playwright 冒烟（6 条） | ✅ 6 条全绿（指向 P4.2 验证实例 8002）：recommend_happy 含调料行、抽屉加载骨架、食材/调料区块、折叠/焦点/disabled 断言；autocomplete / exclude_tags / degraded / search / search_detail 不受影响 |
| 视觉验收 | ✅ 截图 `.tmp_bridge/p4_2_*.png`（推荐卡 / 展开 / 抽屉加载骨架 / 抽屉详情 / 搜索页抽屉）已生成供浏览器复核 |
| 性能 / 鲁棒性 / 安全门禁 | ✅ 无新增请求与第三方资源；折叠增量更新消除长步骤卡片重复重建；抽屉骨架与错误态保持“不白屏 + 可重试”；静态安全扫描仍无 `innerHTML` 等危险 DOM API |
