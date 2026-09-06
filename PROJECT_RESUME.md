# 项目经历：AI 厨师 —— 基于食材的智能菜谱推荐系统

**项目类型**：个人全栈项目（AI 应用） · **代码仓库**：[github.com/sjsj755/ai-cooker](https://github.com/sjsj755/ai-cooker)

## 一句话简介

用户输入家里已有的食材（口语化描述即可，如"冰箱里有个大土豆和仨鸡蛋"），系统通过 LLM 识别食材、混合检索菜谱库，推荐"缺料最少、最可行"的菜谱，并给出做法步骤、缺料提示与替代建议。

## 技术栈

| 层面 | 技术 |
|---|---|
| 后端 | Python 3.14 · FastAPI · SQLAlchemy 2.x + Alembic · LangGraph |
| 数据 | MySQL 8（InnoDB + utf8mb4）· Chroma 向量库 · Redis |
| AI | 自研 OpenAI 兼容 LLM / Embedding 封装（可切换 DeepSeek / Qwen / OpenAI / 本地 Ollama） |
| 前端 | 原生 HTML / CSS / JS（同源托管，无构建链） |
| 工程化 | pytest（284 用例）· Playwright 冒烟 · k6 压测 · Docker Compose + Caddy · GitHub Actions |

## 核心工作与设计取舍（做了什么 · 为什么 · 效果）

**1. LLM 食材识别 + 词典四级映射**
用户输入是自由文本，先由 LLM 结构化提取食材，再经"精确 → 别名 → 包含 → 向量相似"四级映射落到标准食材词典。这样既保留口语化输入的体验，又保证后续检索、缺料计算基于统一标准名。实测 18 条识别用例准确率 0.944。

**2. LangGraph 有状态工作流编排**
推荐主流程 parse → link → filter → retrieve → rank → generate 用 LangGraph 编排，节点可插拔、可单测；条件边天然支持"解析失败重试 1 次""候选为空提前结束""LLM 失败降级直出结构化候选"等兜底分支，避免 AI 链路一处故障导致整服务不可用。

**3. BM25 + 向量混合检索（RRF 融合）**
菜谱检索既有精确关键词需求（菜名、食材名），又有语义需求（"下饭""清淡"）。BM25 零成本、可解释，向量检索补语义召回，两者 RRF 融合排序，再叠加缺料数、难度、时长评分。实测 recall@5 = 0.755；向量库故障自动降级 BM25，服务不中断。

**4. 自研 OpenAI 兼容 LLM / Embedding 层**
不绑定单一厂商：`LLM_BASE_URL` / `EMBEDDING_BASE_URL` 一行配置即可切换 DeepSeek / Qwen / OpenAI / Ollama；httpx 直调、分批 + 指数退避，比 langchain 封装更轻、可控、易测试；模型升级有 fixture 一致性回归门禁，避免静默失败。

**5. 两阶段采集管线（解析 / 入库解耦）**
爬虫解析结果先落 JSON 中间产物（断点续采 + 失败清单），再幂等写入 MySQL 与 Chroma。爬虫被限流或反爬时不会丢中间数据，可随时断点重跑、定向重试失败 URL。

**6. 防幻觉与防注入**
LLM 生成推荐文案时，菜谱标题、评分、缺料、难度、时长等事实字段一律从候选集回填，LLM 只能写步骤和贴士（recipe_id 白名单校验）；提示词采用固定系统指令 + 用户输入 JSON 数据化嵌入，注入文本无法改写指令。

**7. 前端工程化约定**
原生三件套 + 无状态渲染（`createElement` + `textContent`，禁止 innerHTML，从源头防 XSS）；任务级 AbortController 管理请求（幂等重试、超时、防重复提交）；详情抽屉统一状态机（缓存 / 焦点恢复 / 滚动锁定）。

**8. 部署与运维**
Docker Compose 全栈（app + MySQL + Redis + Caddy 自动 HTTPS）+ 就绪门控 + 自动迁移；可信代理 IP 右到左解析，保证反代后限流与反馈指纹按真实客户端 IP 计数；GitHub Actions CI 跑 pytest + Playwright + k6 门禁。

## 项目成果（量化）

- **端到端跑通"采集 → 入库 → 检索 → 生成"全链路**：真实抓取下厨房菜谱，真实 LLM/嵌入（DeepSeek + 阿里云百炼）验收通过。
- **测试门禁**：284 个自动化测试全绿 + 6 条 Playwright 冒烟，覆盖功能、兜底降级、安全、性能。
- **检索性能**：5k 语料查询 P95 6.4ms；k6 10k 语料压测 search P95 120ms / detail 38ms / 推荐（mock LLM）148ms，错误率 <1%；50k 规模基线 P95 423ms 留痕。
- **性能优化**：推荐结果 TTL 缓存使重复推荐从 17.5s 降至 8ms；启动后台预热消除 30-60s 冷启动；BM25/向量双路并行，热态检索耗时减半。
- **安全加固**：API 限流（recommend 10/min）、SQL / Prompt 注入防护、XSS 静态扫描、匿名反馈防刷（SHA-256 指纹 + 幂等）、生产关闭文档接口、Host 白名单、安全响应头、依赖漏洞审计。
- **部署验证**：Docker Compose + Caddy 自动 HTTPS 方案落地，公网 HTTPS 实测通过。

## 面试可讲的技术亮点

- 每个阶段都有"完成定义"：功能测试 + 性能基线 + 安全清单，缺一不可，工程化意识完整。
- 对 AI 应用工程化的理解：降级矩阵、防幻觉回填、模型可替换、Prompt 注入防护。
- 不依赖重型框架，关键组件（LLM / Embedding / 检索 / 评分）均为自研接口化实现，可插拔、可测试。
