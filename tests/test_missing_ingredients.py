"""缺料计算：调料排除、纯精确匹配、别名完整命中与兜底。"""

from app.retrieval.missing import MissingIngredientsCalculator
from tests.helpers import add_recipe, delete_recipe


def _calc(recipe_ids, available):
    return MissingIngredientsCalculator().for_recipes(recipe_ids, available)


def test_seasoning_excluded_and_full_coverage(seeded_db):
    url = "https://test.missing/1"
    try:
        rid = add_recipe(
            "土豆炒青椒",
            url,
            ingredients=["土豆", "青椒"],
            seasonings=["蚝油", "盐"],
        )
        info = _calc([rid], ["土豆", "青椒"])[rid]
        assert info.essential_total == 2
        assert info.missing_ingredients == []
    finally:
        delete_recipe(url)


def test_exact_match_only_no_substring(tmp_path, seeded_db):
    url = "https://test.missing/2"
    try:
        rid = add_recipe("凉拌油麦菜", url, ingredients=["油麦菜"])
        # “油”不得命中“油麦菜”
        info = _calc([rid], ["油"])[rid]
        assert info.missing_ingredients == ["油麦菜"]
        # “椒”不得命中“青椒”
        rid2 = add_recipe("青椒炒肉", "https://test.missing/3", ingredients=["青椒"])
        info2 = _calc([rid2], ["椒"])[rid2]
        assert info2.missing_ingredients == ["青椒"]
    finally:
        delete_recipe(url)
        delete_recipe("https://test.missing/3")


def test_alias_exact_match(seeded_db):
    url = "https://test.missing/4"
    try:
        rid = add_recipe("土豆丝", url, ingredients=["土豆"])
        info = _calc([rid], ["马铃薯"])[rid]  # 土豆别名：马铃薯
        assert info.missing_ingredients == []
    finally:
        delete_recipe(url)


def test_essential_total_zero_for_seasoning_only(seeded_db):
    url = "https://test.missing/5"
    try:
        rid = add_recipe("凉拌", url, seasonings=["盐", "醋"])
        info = _calc([rid], ["盐"])[rid]
        assert info.essential_total == 0
        assert info.missing_ingredients == []
    finally:
        delete_recipe(url)


def test_unknown_available_does_not_cover(seeded_db):
    url = "https://test.missing/6"
    try:
        rid = add_recipe("土豆炖牛肉", url, ingredients=["土豆", "牛肉"])
        info = _calc([rid], ["未知食材"])[rid]
        assert info.missing_ingredients == ["土豆", "牛肉"]
    finally:
        delete_recipe(url)
