"""JSON 落盘：round-trip、判重、断点状态、失败清单。"""

from app.core.crawler import CrawledIngredient, CrawledRecipe
from app.ingestion.json_store import (
    JsonStore,
    build_envelope,
    source_hash,
    validate_envelope,
)

URL = "https://www.xiachufang.com/recipe/104100931/"


def _recipe() -> CrawledRecipe:
    return CrawledRecipe(
        title="稀碎土豆丝",
        source_url=URL,
        description="测试",
        ingredients=[CrawledIngredient(name="土豆", amount="两个")],
        seasonings=[
            CrawledIngredient(name="食用油", amount="少许", is_essential=False)
        ],
        tags=["素菜"],
        steps=[{"instruction": "切丝", "minutes": None}],
    )


def test_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    store.write_recipe("xiachufang", URL, build_envelope(_recipe(), site="xiachufang"))
    data = store.read_recipe("xiachufang", URL)
    ok, err = validate_envelope(data)
    assert ok, err
    restored = CrawledRecipe.model_validate(data["recipe"])
    assert restored.seasonings == _recipe().seasonings
    assert restored.ingredients == _recipe().ingredients
    assert restored.steps == _recipe().steps


def test_schema_version_required():
    envelope = build_envelope(_recipe(), site="xiachufang")
    del envelope["schema_version"]
    ok, err = validate_envelope(envelope)
    assert not ok
    assert "schema_version" in err


def test_exists_and_overwrite(tmp_path):
    store = JsonStore(tmp_path)
    store.write_recipe("xiachufang", URL, build_envelope(_recipe(), site="xiachufang"))
    assert store.exists("xiachufang", URL)
    updated = _recipe().model_copy(update={"title": "新版"})
    store.write_recipe("xiachufang", URL, build_envelope(updated, site="xiachufang"))
    assert store.read_recipe("xiachufang", URL)["recipe"]["title"] == "新版"


def test_failed_jsonl(tmp_path):
    store = JsonStore(tmp_path)
    store.append_failed("xiachufang", {"url": URL, "stage": "parse", "error": "x"})
    store.append_failed("xiachufang", {"url": URL, "stage": "parse", "error": "y"})
    lines = (tmp_path / "xiachufang" / "failed.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2


def test_state_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    store.save_state("xiachufang", {"sources": {"explore": {"next_page": 3}}})
    state = store.load_state("xiachufang")
    assert state["sources"]["explore"]["next_page"] == 3
    assert source_hash(URL)
