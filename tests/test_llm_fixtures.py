"""P5 fixture 一致性回归：toml 元数据必填字段 + mock 输出与 fixture 结构一致。"""

import json
import tomllib
from pathlib import Path

from app.core.mock_llm import MockLLMProvider
from app.core.retriever import RecipeCandidate
from app.graph.prompts import generate_prompt, parse_prompt
from app.schemas.recommend import IngredientExtractionList, RecommendationSet

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_responses"


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_fixture_metadata_required_fields():
    meta = tomllib.loads((FIXTURES / "fixture_metadata.toml").read_text(encoding="utf-8"))
    assert isinstance(meta["schema_version"], int) and meta["schema_version"] >= 1
    assert isinstance(meta["collection_date"], str) and meta["collection_date"]
    assert meta["model"]["provider"]
    assert meta["model"]["name"]
    assert meta["capture"]["script"]
    assert "capture_llm_fixtures.py" in meta["capture"]["script"]
    assert (FIXTURES / "README.md").is_file()


def _assert_nested_structure(sample: dict, actual: dict) -> None:
    """递归断言键集合与叶子类型一致（列表逐元素；dict 仅比较键与类型）。"""
    assert isinstance(sample, dict) and isinstance(actual, dict)
    assert set(sample.keys()) == set(actual.keys()), (
        set(sample.keys()),
        set(actual.keys()),
    )
    for key, sample_value in sample.items():
        actual_value = actual[key]
        if isinstance(sample_value, dict):
            _assert_nested_structure(sample_value, actual_value)
        elif isinstance(sample_value, list):
            assert isinstance(actual_value, list), key
            assert len(sample_value) == len(actual_value), key
            for s_item, a_item in zip(sample_value, actual_value):
                _assert_nested_structure(s_item, a_item)
        else:
            assert type(actual_value) is type(sample_value), (key, type(actual_value), type(sample_value))


def test_mock_parse_matches_fixture_structure():
    fixture = _load_json("parse_sample.json")
    provider = MockLLMProvider()
    outputs = []
    for raw in fixture["inputs"]:
        outputs.append(
            _run(
                provider.structured(parse_prompt([raw]), IngredientExtractionList)
            ).model_dump()
        )
    _assert_nested_structure({"outputs": fixture["outputs"]}, {"outputs": outputs})


def test_mock_generate_matches_fixture_structure():
    fixture = _load_json("generate_sample.json")
    candidates = [
        RecipeCandidate(
            recipe_id=c["recipe_id"],
            title=f"候选菜谱{c['recipe_id']}",
            match_score=0.01,
        )
        for c in fixture["inputs"]["candidates"]
    ]
    provider = MockLLMProvider()
    output = _run(
        provider.structured(
            generate_prompt(candidates, ["土豆"], []),
            RecommendationSet,
        )
    ).model_dump()
    _assert_nested_structure(fixture["output"], output)
