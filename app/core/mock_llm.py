"""MockLLMProvider：确定性、零网络的结构化 LLM 假实现（P5，``LLM_MOCK=true``）。

与真实 ``OpenAICompatibleLLM`` 走完全相同的调用链：``structured(prompt, schema)``
返回经 pydantic 强校验的实例；节点层防幻觉回填逻辑（``_validate_recommendations``
/ ``_degrade_recommendations``）对 mock 输出同样生效——事实字段一律以候选集回填，
不写死占位串、不绕过校验、无网络 IO、同输入恒同输出、时延 <1ms。

- parse：从提示词 JSON 数据块取 ingredients，按标点切分 → 量词剥离 →
  种子词典别名映射（未命中保留原文）；
- generate：从提示词 JSON 数据块取 candidates 的 recipe_id 集合，
  输出 ``RecommendationSet``（steps 含 minutes、tips 为自然语言段落）；
  title / match_score / missing_ingredients / difficulty / cook_time_minutes
  由节点层以候选集回填，mock 不编造。
"""

from __future__ import annotations

import copy
import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app.graph.state import Recommendation
from app.schemas.recommend import (
    IngredientExtraction,
    IngredientExtractionList,
    RecommendationSet,
)
from scripts.seed_dictionary import INGREDIENTS

T = TypeVar("T", bound=BaseModel)

# 量词前缀：数字（含中文数字/半）+ 可选计量单位/量词，剥离后得到食材名
_QUANT_PREFIX_RE = re.compile(
    r"^(?:[0-9０-９一二两三四五六七八九十半]+)"
    r"(?:个|颗|根|斤|两|克|公斤|g|kg|毫升|ml|升|块|片|只|条|头|瓣|勺|把|份|"
    r"袋|盒|瓶|碗|杯|支|枚|粒|段|束|扎|包|桶|罐|板|听|捆)?"
)
_SPLIT_RE = re.compile(r"[\s,，、;；。]+|和|与|以及")

# 种子词典别名映射：标准名 / 别名 → 标准名（确定性、离线）
_ALIAS_MAP: dict[str, str] = {}
for _item in INGREDIENTS:
    _ALIAS_MAP[_item["name"]] = _item["name"]
    for _alias in _item.get("aliases") or []:
        _ALIAS_MAP[_alias] = _item["name"]

_GENERATE_STEPS = [
    {"instruction": "将食材按菜谱步骤处理切配，备好所需调料", "minutes": 10},
    {"instruction": "中小火烹饪至熟透，调味后出锅装盘", "minutes": 10},
]
_GENERATE_TIPS = (
    "小贴士：按家中现有食材灵活调整用量，火候以中小火为宜，"
    "出锅前尝味再补盐，避免过咸。"
)


def _extract_json_object(text: str) -> dict:
    """从提示词中提取首个平衡花括号 JSON 对象（复用真实 provider 的健壮性）。"""
    start = text.find("{")
    if start < 0:
        raise ValueError("提示词中未找到 JSON 数据块")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("提示词中 JSON 对象未闭合")


def _split_ingredients(raw_items: list[str]) -> list[tuple[str, str | None]]:
    """标点切分 + 量词剥离 + 别名映射；返回 (名称, 数量或 None)。"""
    result: list[tuple[str, str | None]] = []
    for raw in raw_items:
        for token in _SPLIT_RE.split(raw or ""):
            token = token.strip()
            if not token:
                continue
            quant = ""
            match = _QUANT_PREFIX_RE.match(token)
            if match:
                quant = match.group(0)
                token = token[match.end() :].strip()
            if not token:
                continue
            name = _ALIAS_MAP.get(token, token)
            result.append((name, quant or None))
    return result


class MockLLMProvider:
    """确定性结构化 LLM：零网络 IO，parse / generate 均走 pydantic 强校验。"""

    async def structured(self, prompt: str, schema: type[T]) -> T:
        if schema is IngredientExtractionList:
            return self._parse(prompt)
        if schema is RecommendationSet:
            return self._generate(prompt)
        raise ValueError(f"MockLLMProvider 不支持 schema: {schema.__name__}")

    def _parse(self, prompt: str) -> IngredientExtractionList:
        data = _extract_json_object(prompt)
        raw_items = data.get("ingredients") or []
        items = [
            IngredientExtraction(name=name, quantity=quantity, unit=None)
            for name, quantity in _split_ingredients(raw_items)
        ]
        return IngredientExtractionList(items=items)

    def _generate(self, prompt: str) -> RecommendationSet:
        data = _extract_json_object(prompt)
        candidates = data.get("candidates") or []
        recipe_ids = [
            int(c["recipe_id"])
            for c in candidates
            if isinstance(c, dict) and isinstance(c.get("recipe_id"), int)
        ]
        # 事实字段以候选集回填为准（节点层强制），mock 只提供 steps/tips 文案
        return RecommendationSet(
            recommendations=[
                Recommendation(
                    recipe_id=rid,
                    title="",
                    match_score=0.0,
                    missing_ingredients=[],
                    difficulty=None,
                    cook_time_minutes=None,
                    steps=copy.deepcopy(_GENERATE_STEPS),
                    tips=_GENERATE_TIPS,
                    seasonings=[],
                )
                for rid in recipe_ids[:3]
            ]
        )
