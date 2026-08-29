# LLM 输出 fixtures

本目录保存真实 LLM（OpenAI 兼容端点）parse / generate 的结构化输出样例，
用于 mock LLM 与真实输出的一致性回归（键集合 / 字段类型 / 嵌套结构一致）。

权威元数据见 [fixture_metadata.toml](fixture_metadata.toml)（机器可读，门禁以
TOML 为准；本 README 仅人工说明，不做正则扫描）。

## 文件

- `parse_sample.json`：parse（食材识别）输入与输出；
- `generate_sample.json`：generate（推荐生成）输入与输出。

## 升级 / 采集流程（硬性前置）

1. 升级 / 切换模型；
2. 配置 `LLM_API_KEY` 后运行 `uv run python scripts/capture_llm_fixtures.py`
   重新采集 parse / generate 输出并更新元数据；
3. 人工复核结构字段；
4. 若结构变化，同步调整 mock 与校验逻辑；
5. 全量 pytest + 一致性回归全绿后才允许上线。
