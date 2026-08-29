# P1 数据采集阶段设计文档（仅 parse）

> 状态：**已实施（parse，2026-08-29 验收；ingest 亦已完成，实施与验收见 [docs/P1_PLAN.md](P1_PLAN.md) §11）**。本文档为 P1 采集阶段的设计依据与实施记录，覆盖：下厨房适配器、HTML 解析与清洗、食材/调料分流、JSON 落盘、断点续采、CLI；ingest（入库 MySQL/Chroma）不在此展开，JSON 格式已按 [docs/P1_PLAN.md](P1_PLAN.md) §3.3 预留消费语义。
>
> 依据：真实页面抓取与 DOM 检查（2026-08-28，详见 §2）。真实抓取为验收项，测试离线可跑。

## 1. 目标与范围

### 1.1 目标

交付一条**仅解析（parse）**的采集管线：

```
索引页拉取（URL 发现）→ 逐条抓详情页 → HTML 清洗 → 结构化解析
→ 食材/调料分流 → 规范化 → CrawledRecipe 封装 → JSON 落盘
```

产出物：

1. `app/crawlers/xiachufang.py`：下厨房适配器（PC 与移动端两套选择器，注册到 `registry`）；
2. `app/core/seasoning_words.py`：调料词表 + 分流判定；
3. `app/ingestion/json_store.py`：JSON 读写、判重、失败清单、断点状态；
4. `scripts/crawl_recipes.py`：`--stage parse` CLI；
5. `tests/fixtures/` 下 4 个真实页面 fixture + 离线测试；
6. `data/crawled/{site}/` 运行期中间产物（gitignore，不入库）。

### 1.2 范围外（后续阶段）

- ingest：扫描 JSON → MySQL/Chroma（已在 P1 内完成，实施与验收见 P1_PLAN §11；本文档不展开）；
- 检索 / 评分 / 推荐（P2/P3）；
- LLM 食材识别与四级词典（P3）；
- 多站点适配器（本期只有下厨房）。

## 2. 真实页面调研记录（2026-08-28）

### 2.1 抓取方式说明

本会话未暴露应用内浏览器控制工具（`node_repl` MCP 未挂载，浏览器插件桥接无法建立），因此改用与爬虫同源的 **HTTP 直连抓取真实页面**（Chrome/iPhone 版 UA），页面内容与浏览器打开一致，且更接近未来 `httpx` 抓取的实际形态。已覆盖 PC 与移动端、详情页与索引页两类页面。

### 2.2 页面清单与可访问性结论

| URL | 类型 | HTTP | 说明 |
|---|---|---|---|
| `https://www.xiachufang.com/recipe/104100931/` | PC 详情页 | 200 | 稀碎土豆丝；完整 DOM |
| `https://m.xiachufang.com/recipe/107802306/` | 移动详情页 | 200 | 牛油果生椰抹茶；Nuxt SSR 完整 DOM |
| `https://www.xiachufang.com/explore/` | PC 索引页 | 200 | 本周最受欢迎菜谱；分页 `/explore/?page=N` 正常 |
| `https://m.xiachufang.com/category/40076/` | 移动分类页 | 200 | 家常菜分类；列表完整 |
| `https://www.xiachufang.com/category/40076/`、`/category/40078/` | PC 分类页 | **418** | 全站 PC `/category/*` 被反爬拦截（持续复现），本轮无法抓取 |
| `https://hanwuji.xiachufang.com/category/40076/` | 镜像分类页 | 200 | 备用，非默认白名单域名 |

**结论：**

- PC 详情页、移动详情页、PC explore、移动分类页均可稳定抓取，作为四类 fixture 与解析目标；
- PC `/category/*` 目前被 418 拦截，**索引采集不依赖 PC 分类页**（详见 §8）；
- 移动分类页 `?page=N` 可访问（200）但被 `robots.txt` 禁止（见 §2.4），不做分页爬取；
- `sitemap.xml` 存在且每日更新（`/sitemap/recipe_0..9.xml.gz`），可作为合规的全量 URL 发现源。

### 2.3 页面 DOM 结构速览

| 信息 | PC 详情页 | 移动详情页 | PC explore 索引 | 移动分类索引 |
|---|---|---|---|---|
| 标题 | `h1.page-title` | `h1.recipe-name` | `.info .name a` | `a.recipe-96-horizon .name` |
| 描述 | `div.desc.mt30` | `section.recipe-desc` | — | — |
| 用料行 | `div.ings table tr`：`td.name a` + `td.unit` | `section#ings .ing-line`：`.ing-name` + `.ing-amount` | `.info .ing`（预览，不用） | — |
| 步骤 | `div.steps ol li.container p.text` | `section#steps .recipe-steps .step`：`p.step-text` | — | — |
| 分类/标签 | `.recipe-tags .recipe-cats a` | 无标签容器（见 §4.5） | — | — |
| 统计 | `.recipe-stats .time/.pv`（发布时间/收藏） | `.recipe-stats`（浏览/做过） | `.info .stats`（热度，不用） | `.stat`（评分/做过，不用） |
| 菜谱链接 | — | — | `li > a[href^="/recipe/"]` | `a.recipe-96-horizon[href^="/recipe/"]` |
| 分页 | — | — | `div.pager a[href*="page="]` | 无（见 §2.4） |
| 结构化数据 | 百度 `ld+json`（Recipe） | `schema.org` `ld+json`（Recipe） | — | — |

### 2.4 robots.txt 合规要点（抓取时已核对）

`https://www.xiachufang.com/robots.txt`（2026-08-28）关键规则：

| 规则 | 对设计的影响 |
|---|---|
| `Crawl-delay: 10` | **默认请求间隔 10s**（修正 P1_PLAN §4.3 中 1s 的初稿值） |
| `Disallow: /search/` | 禁止爬搜索页，URL 发现不走搜索 |
| `Disallow: /recipe/*/?` | 详情页仅允许无查询串 URL（`/recipe/{id}/` 合规） |
| `Disallow: /recipe/*/printable/` 等子路径 | 只抓 `/recipe/{id}/` 标准详情页 |
| `Disallow: /category/*/?`（`Allow: /category/*/?ref=*`） | 分类页只抓无查询串首页；不抓 `?page=`；`ref=` 变体可作补充源（待评估） |
| `Disallow: /category/*/pop/`、`/recent/`、`/time/` | 移动分类页自带的 `/pop/?page=N` 分页链接受 robots 限制，**不使用** |
| `Disallow: /cook/*/recipe_list/` | 用户菜谱列表不可作为索引源 |
| `Sitemap: https://www.xiachufang.com/sitemap.xml` | 官方声明，可用作 URL 发现源 |

## 3. Fixture 清单（真实页面，离线测试数据源）

| 文件 | 来源 URL | 抓取日期 | 大小 | 内容要点 |
|---|---|---|---|---|
| `tests/fixtures/xiachufang_recipe.html` | `https://www.xiachufang.com/recipe/104100931/` | 2026-08-28 | ~41 KB | PC 详情：标题/描述/4 用料（含油、盐）/2 步骤/2 分类标签/ld+json |
| `tests/fixtures/xiachufang_m_recipe.html` | `https://m.xiachufang.com/recipe/107802306/` | 2026-08-28 | ~147 KB | 移动详情：标题/描述/3 用料/5 步骤/schema.org ld+json |
| `tests/fixtures/xiachufang_index.html` | `https://www.xiachufang.com/explore/` | 2026-08-28 | ~66 KB | PC 索引：25 个菜谱链接 + `div.pager` 分页（page=2..5） |
| `tests/fixtures/xiachufang_m_index.html` | `https://m.xiachufang.com/category/40076/` | 2026-08-28 | ~25 KB | 移动分类：19 个菜谱链接，无分页链接 |

约定：

- fixture 为**未经修改的原始响应 HTML**，保留真实标记（含 `data-v-*`、占位图 base64）；测试只断言结构与抽取结果，不依赖易变数据（如具体菜名之外的评分/热度）；
- 解析测试以 fixture 为唯一输入，**离线可跑、不联网**；
- fixture 变更需在 PR 中说明（站点改版时更新并同步本文档选择器表）。

## 4. 页面解析规格（选择器与抽取逻辑）

### 4.1 PC 菜谱详情页 `www.xiachufang.com/recipe/{id}/`

| 字段 | 选择器 / 逻辑 | 示例值 |
|---|---|---|
| `title` | `h1.page-title` 文本 | 稀碎土豆丝 |
| `source_url` | `link[rel=canonical]`，兜底请求 URL 去 query/fragment | `https://www.xiachufang.com/recipe/104100931/` |
| `description` | `div.desc.mt30`；缺失时回退 ld+json `description` | 土豆丝有人喜欢… |
| `ingredients` | `div.ings table tr`：`td.name a`（名称）+ `td.unit`（用量） | 土豆/两个、青椒/3个 |
| `seasonings` | 上一步结果经 §6 分流 | 油/稍微放多一点点、盐/适量 |
| `tags` | `.recipe-tags .recipe-cats a` 文本（站点“相关分类”） | 素菜、家常菜 |
| `steps` | `div.steps ol li.container p.text` 文本；**不拆分内部编号** | “1，走了一里的山路…2，辣椒切滚刀块” |
| `difficulty / cook_time_minutes / servings` | 页面无此信息 → `null`（见 §4.6） | null |

### 4.2 移动端菜谱详情页 `m.xiachufang.com/recipe/{id}/`

| 字段 | 选择器 / 逻辑 | 示例值 |
|---|---|---|
| `title` | `h1.recipe-name` | 牛油果生椰抹茶 |
| `description` | `section.recipe-desc` | 优质植物脂肪+抗炎… |
| `ingredients` | `section#ings .recipe-ingredient a.ing-line`：`.ing-name` + `.ing-amount` | 抹茶粉/10g |
| `seasonings` | 分流同上 | — |
| `tags` | 页面无标签容器：优先 JSON-LD `recipeCategory`（单个），再考虑 `keywords` 中非“做法”短语（见 §4.5） | 下午茶 |
| `steps` | `section#steps .recipe-steps .step`：`p.step-text` 为指令，`div.sub-title`（步骤 N）仅作序号校验，**序号不入 instruction** | 牛油果在可以不用力… |
| `difficulty / cook_time_minutes / servings` | 无 → `null` | null |

### 4.3 PC 索引页 `www.xiachufang.com/explore/?page=N`

- 菜谱 URL：`div.recipe-215-horizontal a[href^="/recipe/"]`，正则校验 `/recipe/\d+/`；
- 下一页：`div.pager a[href*="page="]`（含“下一页”），顺序翻页直至无下一页或达到 `--limit`；
- 每页去重（同页/跨页可能出现重复推荐）。

### 4.4 移动端分类页 `m.xiachufang.com/category/{id}/`

- 菜谱 URL：`a.recipe-96-horizon[href^="/recipe/"]`；
- 仅抓无查询串首页（robots 合规）；`?page=` 与 `/pop/?page=` 均不使用；
- 分类页作为**补充索引源**（每分类 1 页），主源为 explore / sitemap。

### 4.5 JSON-LD 结构化数据（fallback 与补充）

两类详情页均内嵌 `script[type="application/ld+json"]` Recipe：

- PC：百度 cambrian 格式；移动：schema.org 格式；字段近似（`name`/`description`/`recipeIngredient`/`recipeInstructions`/`keywords`/`recipeCategory`）；
- **主解析以 DOM 为准**，JSON-LD 仅用于：DOM 缺标题/描述时兜底；移动端标签缺失时取 `recipeCategory`；
- `recipeIngredient` 是“用量+名称”粘连字符串（如 `两个土豆`、`10g抹茶粉`），**不作为主数据源**；如需兜底使用，按 §5.4 拆分量/名；
- `recipeInstructions` 为编号拼接文本（`0.…,1.…` 或 `1.… 2.…`），仅作步骤缺失时兜底，且按分隔符拆条。

### 4.6 字段缺失策略

真实页面（PC/移动）均**不提供难度、时长、份数**：

- `difficulty`、`cook_time_minutes`、`servings` 在 `CrawledRecipe` 中保持可空，解析不出即 `null`；
- `steps[].minutes` 同样为 `null`（P1 不做“按文本估时”启发式，避免脏数据；保留字段供后续阶段填充）；
- 解析器对缺字段不报错、不丢弃整条，落盘 JSON 保留 `null`。

## 5. HTML 清洗与文本规范化

1. **去噪**：解析前移除 `<script>`、`<style>`、`<noscript>`、`<svg>`；抽取文本时仅对已知容器抽取，不做全页文本；
2. **控制字符**：删除 `\x00-\x08`、`\x0b`、`\x0c`、`\x0e-\x1f`；统一 `\r\n`/`\r` → `\n`；
3. **空白**：连续空白折叠为单空格，条目文本 trim；全角空格 `\u3000` → 空格；
4. **量词/别名（parse 阶段最小集）**：
   - 数量词不剥离：`amount` 保留原始串（`两个`、`3个`、`10g`、`适量`），P1 不做单位换算；
   - 名称统一去首尾标点、去括号注释（如“土豆（中等大小）” → 名称“土豆”），完整原文不落盘；
   - 别名映射表（`app/core/seasoning_words.py` 内，可配置）：`油→食用油`、`色拉油→食用油`、`细砂糖/白糖→糖` 等最小集；映射只做**词表内别名归并**，不做食材实体归一（P3 词典负责）。
5. **HTML 实体**：bs4 自动解码；残留实体按 `get_text()` 结果处理。

### JSON-LD 兜底拆分（仅 fallback 用）

对粘连字符串“用量+名称”：正则 `^([\d.]+(?:g|ml|L|个|颗|根|片|勺|克|毫升|千克|斤|两)?|约?\d+\s*[-~]?\s*\d*\s*克?|少许|适量|若干)(.+)$` 拆出 `amount` 与 `name`；拆不出时整串作 `name`、`amount=None`。该启发式不用于主路径，故不追求高准确率。

## 6. 食材 / 调料分流

### 6.1 词表

`app/core/seasoning_words.py` 内置约 30 组（词 + 别名 + 归一名称）：

盐（食盐/海盐）、糖（白糖/冰糖/红糖/细砂糖）、酱油（生抽/老抽/味极鲜）、醋（香醋/陈醋/白醋/米醋）、料酒（黄酒）、食用油（油/菜籽油/花生油/玉米油/橄榄油）、香油（芝麻油）、蚝油、鸡精、味精、胡椒粉（白胡椒/黑胡椒）、辣椒粉（辣椒面）、花椒粉、豆瓣酱（郫县豆瓣）、黄豆酱、甜面酱、番茄酱、淀粉（玉米淀粉/红薯淀粉/土豆淀粉）、十三香、五香粉、孜然粉、小苏打、泡打粉、蜂蜜、葱、姜、蒜（及 姜片/蒜末/葱花 等形态）、八角、桂皮、香叶、花椒、干辣椒、芝麻。

### 6.2 判定规则

1. 名称规范化（trim、全角→半角、小写）后，**最长词表前缀/包含匹配**；
2. 命中 → `seasonings`，`is_essential=false`；
3. 未命中 → `ingredients`，`is_essential=true`；
4. 无法判断（空名）→ 丢弃并记 debug 日志；含“可选”标注的条目仍按默认分流（P1 不引入额外状态）。

边界与取舍（写入测试）：

- 葱/姜/蒜归调料（按 P1_PLAN §6 词表约定；后果：如“小葱拌豆腐”的主料葱会被当调料——本期接受，P3 词典可修正）；
- “油”命中别名 → 调料；“淀粉”无论勾芡还是腌制均归调料；
- “干辣椒”归调料（香料），`辣椒`（鲜）不命中词表 → 食材。

## 7. 数据模型与 JSON 格式（schema_version = 1）

沿用 P1_PLAN §3.3，`CrawledRecipe` 增加 `seasonings`（默认空表，向后兼容）：

```json
{
  "schema_version": 1,
  "site": "xiachufang",
  "crawled_at": "2026-08-28T21:00:00+08:00",
  "discovered_from": "https://www.xiachufang.com/explore/",
  "recipe": {
    "title": "稀碎土豆丝",
    "source_url": "https://www.xiachufang.com/recipe/104100931/",
    "difficulty": null,
    "cook_time_minutes": null,
    "servings": null,
    "description": "土豆丝有人喜欢…",
    "ingredients": [
      {"name": "土豆", "amount": "两个", "is_essential": true},
      {"name": "青椒", "amount": "3个", "is_essential": true}
    ],
    "seasonings": [
      {"name": "油", "amount": "稍微放多一点点", "is_essential": false},
      {"name": "盐", "amount": "适量", "is_essential": false}
    ],
    "tags": ["素菜", "家常菜"],
    "steps": [
      {"instruction": "1，走了一里的山路买的土豆洗净去皮切丝\n（直接炒就不用浸在水里了）\n2，辣椒切滚刀块", "minutes": null},
      {"instruction": "1，热锅、热油放入青椒，翻炒片刻加入土豆丝\n2，加入土豆丝大火翻炒，熟了出锅。", "minutes": null}
    ]
  }
}
```

要点：

- `discovered_from` 为可选的发现页 URL，便于审计与断点排查；
- `schema_version` 缺失/不符按“无效文件”处理（invalid 流程为 ingest 阶段，本期在 `json_store` 预留校验入口）；
- 与 ingest 的消费契约：ingredients 与 seasonings 统一写 `recipe_ingredients`，调料写 `ingredients` 时 `category='调料'`（P1_PLAN §4.1，本期不实现，仅格式保证）。

## 8. URL 发现与索引采集（robots 合规）

`fetch_index` 支持三类源（CLI `--source`，默认 `explore`）：

1. **explore（默认）**：`/explore/?page=N` 顺序翻页；robots 允许；每页 25 条；
2. **category**：白名单内固定分类 id 列表 × 每分类**首页**（无 query）；PC 分类 418 → 使用移动端 `m.xiachufang.com/category/{id}/`；仅补充少量样本；
3. **sitemap（可选，`--source sitemap`）**：解析 `sitemap.xml` → `sitemap/recipe_{0..9}.xml.gz` → 解压提取菜谱 URL；适合全量采集，需评估量级与频率（默认不启用）。

统一处理：

- URL 归一：host 允许 `www.xiachufang.com` / `m.xiachufang.com`，路径 `/recipe/{id}/`；白名单外或重定向出域即拒绝（防 SSRF）；
- 去重：内存 set + 落盘文件存在性双重判断；
- 请求间隔默认 **10s**（robots Crawl-delay），`--delay` 可调但小于 10s 时打印警告（默认策略：不低于 10s）。

## 9. 断点续采、幂等与失败处理

目录约定（运行期，`data/` 入 gitignore）：

```
data/crawled/xiachufang/
  {sha256(source_url)}.json     # 成功解析的菜谱（判重键）
  state.json                    # 索引断点：各 source 已翻页数、已发现/已落盘计数、last_run
  failed.jsonl                  # 单条失败：{ts, url, stage, error, retries}
  invalid/                      # 校验失败文件（预留，ingest 阶段启用）
```

规则：

- **resume（默认开）**：`{hash}.json` 已存在即跳过（`--force` 覆盖重爬）；
- **索引断点**：`state.json` 记录 `source → pages_done/next_page`，中断后重启从 `next_page` 继续，不重抓已完成页；
- **单条失败**：`retry_with_backoff(max_attempts=3)`（复用 P0 兜底）→ 仍失败追加 `failed.jsonl` 并落 ERROR 日志，**跳过不中断整批**；
- **并发**：单进程内串行（P1_PLAN §12 约定，不引入队列/Redis）。

## 10. 目录与文件布局（本期新增）

```
app/crawlers/xiachufang.py      # 适配器：fetch_index / parse_page / 两套选择器
app/core/seasoning_words.py     # 调料词表 + alias + classify_seasoning()
app/core/html_clean.py          # 清洗：去 script/style、控制字符、空白归一
app/ingestion/json_store.py     # JSON 读写、判重、state.json、failed.jsonl
scripts/crawl_recipes.py        # CLI（--stage parse）
tests/test_xiachufang_parser.py # fixture 解析测试（PC/移动详情 + 两类索引）
tests/test_seasoning_split.py   # 分流测试
tests/test_json_store.py        # round-trip / resume / force / failed.jsonl
tests/test_crawl_cli.py         # httpx MockTransport 端到端（离线）
tests/fixtures/*.html           # 4 个真实页面 fixture
```

配置新增（`app/config.py` / `.env.example`）：

```text
CRAWLER_DELAY_SECONDS=10.0        # 对齐 robots Crawl-delay: 10
CRAWLER_TIMEOUT_SECONDS=10.0
CRAWLER_RETRY=3
CRAWL_ALLOWED_DOMAINS=["www.xiachufang.com","m.xiachufang.com"]
CRAWLER_UA=ai-cooker-p1-bot/0.1 (+contact-url; contact: dev@example.com)
CRAWL_OUTPUT_DIR=./data/crawled
LOG_LEVEL=INFO
```

## 11. CLI 设计

```powershell
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse `
  [--source explore|category|sitemap] [--limit N] [--dry-run] `
  [--resume] [--force] [--delay SECONDS] [--out-dir data/crawled]
```

语义：

| 参数 | 说明 |
|---|---|
| `--site` | 适配器名（本期仅 `xiachufang`） |
| `--stage parse` | 本期仅 parse；`ingest` 校验报“未实现”退出码 2 |
| `--limit N` | 本批最多落盘 N 条新菜谱（已存在不计入） |
| `--dry-run` | 完整执行抓取+解析但不写文件、不更新 state；输出每条摘要（标题/URL/食材数/调料数） |
| `--resume` | 默认开；跳过已落盘 hash |
| `--force` | 覆盖重爬已落盘条目 |
| `--delay` | 请求间隔秒；<10 警告 |

退出码：`0` 全部成功；`1` 部分失败（failed.jsonl 有记录）；`2` 参数/未实现错误；`3` 网络/robots 阻断。

日志：结构化 JSON 行，事件 `crawl.{site}.parse.{started|index_page|page_ok|page_failed|recipe_saved|done}`，ERROR 级记录失败详情。

## 12. 合规与安全

- **robots**：启动时抓取并缓存 `robots.txt`，白名单校验通过才继续；不爬 §2.4 禁止路径；
- **请求节流**：默认 10s 间隔（对齐 Crawl-delay）；UA 明示爬虫用途；
- **域名白名单**：`www` + `m` 两个域；重定向后仍校验目标域，防 SSRF；只抓 `http(s)`；
- **数据**：只落盘菜谱公开字段，不含 cookie/密钥；`EMBEDDING_API_KEY` 等仅环境变量（本期未用）；
- **HTML 清洗**：抽取文本无 `<script>/<style>` 残留、无控制字符（测试断言）。

## 13. 测试策略（离线）与验收

### 13.1 离线测试（pytest，无网络）

| 用例 | 断言 |
|---|---|
| PC 详情解析 | 标题“稀碎土豆丝”、4 用料、2 步骤、2 标签、描述非空、`source_url` 为规范 URL |
| 移动详情解析 | 标题“牛油果生椰抹茶”、3 用料、5 步骤（无“步骤 N”前缀）、`recipeCategory` 标签回退 |
| 分流 | 土豆/青椒→ingredients；油/盐/葱姜蒜→seasonings；空名丢弃 |
| 索引解析 | explore 25 条 URL + next page；移动分类 19 条 URL |
| JSON round-trip | 含 seasonings 全字段序列化→文件→`model_validate` 无损；缺 `schema_version` 判无效 |
| resume/force | 已存在跳过；`--force` 重爬；state.json 断点续传 |
| 失败路径 | 3 次重试后写 `failed.jsonl`，批处理不中断 |
| CLI 端到端 | `httpx.MockTransport`：`parse --limit 3` 产 3 文件；`--dry-run` 零副作用；重跑 0 新增 |
| 清洗 | 抽取文本无 script/style、无控制字符 |
| 性能基线 | fixture 纯解析 ≥ 10 页/s（本机，记录耗时不设硬门禁） |

### 13.2 真实抓取验收（需外网）

```powershell
uv sync
uv run pytest
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --limit 5
# 预期：5 个 JSON 落盘 data/crawled/xiachufang/，含 ingredients/seasonings/tags/steps
uv run python scripts/crawl_recipes.py --site xiachufang --stage parse --limit 5
# 预期：重跑 0 新增（resume）
```

## 14. 与 P1_PLAN 的差异与开放问题

| P1_PLAN 初稿 | 本设计调整 | 原因 |
|---|---|---|
| `CRAWLER_DELAY_SECONDS=1.0` | 默认 10.0 | 实测 robots `Crawl-delay: 10` |
| `CRAWL_ALLOWED_DOMAINS=["www.xiachufang.com"]` | 增加 `m.xiachufang.com` | 移动端为正式解析目标 |
| 索引 fixture `xiachufang_index.html` | 采用 `/explore/` 页；分类页用移动端 | PC `/category/*` 实测 418 |
| 分类分页采集 | 不做（仅首页） | robots 禁止 `/category/*/?` 与 `/pop/` |
| `steps[].minutes` | 本期一律 `null` | 页面无时长信息，不做估时启发式 |
| fixture 数量 2 | 4（PC/移动详情 × PC/移动索引） | 两类域×两类页全覆盖 |

开放问题（待评审确认）：

1. 全量采集是否启用 sitemap 源（数据量、频率、去重成本）；
2. 葱姜蒜/干辣椒等边界词归调料的口径是否接受（P3 词典再细分）；
3. 移动端标签仅能取 JSON-LD `recipeCategory`（单标签）是否满足 P2 标签诉求；
4. `ref=` 分类页变体是否作为补充索引源（需小批量验证内容差异）。

## 15. 实施补充（2026-08-29 真实抓取观测）

实现与真实抓取验收过程中的实测结论（已固化进代码与测试）：

- **会话 warmup**：直连详情页会被 302 到 `/auth/humancheck_captcha/`；先抓一次首页建立 cookie 会话可大幅降低概率（CLI 启动即 warmup）。
- **按 URL 定向反爬**：部分菜谱（如 107339380 / 107562259）即使带 cookie、Referer、长间隔仍稳定触发人机验证，且移动端对部分 ID 同样拦截（旋转式防护）。处置：人机验证页视为单条失败写入 `failed.jsonl` 并继续；PC 被拦时自动回退移动端同 URL 兜底（`source_url` 保持发现页 URL）。
- **429 限流**：短时间连续请求后站点返回 429（含首页）。处置：429 重试前固定等待 30s；连续 5 条反爬失败中止整批（退出码 3），避免持续冲击。
- **解析护栏**：无标题页面（验证页/异常页）抛 `PageParseError`，杜绝把验证页当菜谱落盘。
- **数据质量规范化**：调料别名归并后按归一名称去重（生抽+老抽 → 酱油，用量拼接）；标签只取 `.recipe-tags .recipe-cats a[href^='/category/']`，排除混入的相关菜谱名；空用量存 `null` 而非空串。
- **测试规模**：新增 26 个离线测试（分流/清洗/json_store/解析/CLI MockTransport 端到端），全量 45 个通过。

## 16. RAG 分块与向量库优化（2026-08-29 实施）

- **语义边界分块**：弃用纯字符 `RecursiveCharacterTextSplitter`，改为结构单元分块——标题+描述、用料块、每条步骤均为不可切单元，同类型单元贪心合并至 500 字上限，**绝不跨类型混块、单元内不切开**；仅当单条步骤超过上限时按句末标点/换行回退切分（仍超长才硬切）。移除字符 overlap。
- **块元数据**：每块带 `unit_type`（header/ingredients/steps）与步骤块 `step_start`/`step_end`，供 P2 按类型过滤与展示。
- **写入前清理旧块**：Chroma 由“纯 upsert”改为“嵌入成功后 `delete(where=source_url)` → `upsert`”，正常重跑也清理，避免菜谱内容变化/块数变少后残留孤儿块；嵌入失败时旧块仍在，无空窗。
- 空步骤指令（如“合集”类菜谱）不产生步骤块，仅保留标题/描述/用料块；实测 7 条真实菜谱 → 19 个语义块，重跑块数与集合大小稳定。
