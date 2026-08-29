"""语义单元分块：类型不混块、单元不切开、超长单元句号回退。"""

from app.core.crawler import CrawledIngredient, CrawledRecipe
from app.ingestion.text_builder import (
    UNIT_HEADER,
    UNIT_INGREDIENTS,
    UNIT_STEPS,
    build_recipe_document,
    chunk_recipe,
)


def _recipe() -> CrawledRecipe:
    return CrawledRecipe(
        title="土豆炒鸡蛋",
        source_url="https://www.xiachufang.com/recipe/1/",
        description="简单家常菜",
        ingredients=[CrawledIngredient(name="土豆", amount="两个")],
        seasonings=[CrawledIngredient(name="盐", amount="适量", is_essential=False)],
        steps=[{"instruction": "土豆切片", "minutes": None}],
    )


def test_document_contains_all_fields():
    doc = build_recipe_document(_recipe())
    assert "土豆炒鸡蛋" in doc
    assert "简单家常菜" in doc
    assert "土豆：两个" in doc
    assert "盐：适量" in doc
    assert "步骤1：土豆切片" in doc


def test_units_not_mixed_and_merged_by_type():
    recipe = CrawledRecipe(
        title="混合菜",
        source_url="https://www.xiachufang.com/recipe/2/",
        description="描述文字",
        ingredients=[CrawledIngredient(name="土豆", amount="两个")],
        seasonings=[CrawledIngredient(name="盐", amount="适量")],
        steps=[
            {"instruction": "切丝", "minutes": None},
            {"instruction": "翻炒", "minutes": None},
            {"instruction": "出锅", "minutes": None},
        ],
    )
    chunks = chunk_recipe(recipe)
    types = [c.unit_type for c in chunks]
    assert UNIT_HEADER in types
    assert UNIT_INGREDIENTS in types
    assert UNIT_STEPS in types
    # 同一块内不混类型
    for chunk in chunks:
        if chunk.unit_type == UNIT_HEADER:
            assert "用料" not in chunk.text and "步骤" not in chunk.text
        elif chunk.unit_type == UNIT_INGREDIENTS:
            assert "步骤" not in chunk.text
        else:
            assert "用料" not in chunk.text
    # 步骤块合并后序号连续
    step_chunks = [c for c in chunks if c.unit_type == UNIT_STEPS]
    assert step_chunks[0].step_start == 1
    assert step_chunks[-1].step_end == 3
    assert all(len(c.text) <= 500 for c in chunks)


def test_chunk_size_respected_when_merging():
    recipe = CrawledRecipe(
        title="长菜谱",
        source_url="https://www.xiachufang.com/recipe/3/",
        steps=[
            {"instruction": f"第{i}步：" + "翻炒食材。" * 20, "minutes": None}
            for i in range(6)
        ],
    )
    chunks = chunk_recipe(recipe, chunk_size=100)
    assert chunks
    assert all(len(c.text) <= 100 for c in chunks)
    step_chunks = [c for c in chunks if c.unit_type == UNIT_STEPS]
    assert step_chunks
    assert all(c.unit_type == UNIT_STEPS for c in step_chunks)


def test_long_step_sentence_split_fallback():
    long_step = "先处理食材。" * 60  # 360 字符，超过 chunk_size=200
    recipe = CrawledRecipe(
        title="长步骤",
        source_url="https://www.xiachufang.com/recipe/4/",
        steps=[{"instruction": long_step, "minutes": None}],
    )
    chunks = chunk_recipe(recipe, chunk_size=200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    step_chunks = [c for c in chunks if c.unit_type == UNIT_STEPS]
    assert len(step_chunks) > 1
    assert all(c.step_start == 1 and c.step_end == 1 for c in step_chunks)


def test_empty_recipe_yields_no_chunks():
    assert chunk_recipe(CrawledRecipe(title="", source_url="https://x/")) == []
