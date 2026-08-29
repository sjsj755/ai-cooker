"""食材/调料分流：词表命中与边界。"""

from app.core.seasoning_words import SEASONING_GROUPS, classify_seasoning


def test_exact_hits():
    assert classify_seasoning("盐") == "盐"
    assert classify_seasoning("酱油") == "酱油"
    assert classify_seasoning("油") == "食用油"
    assert classify_seasoning("糖") == "糖"


def test_alias_hits():
    assert classify_seasoning("生抽") == "酱油"
    assert classify_seasoning("玉米淀粉") == "淀粉"
    assert classify_seasoning("白胡椒粉") == "胡椒粉"


def test_prefix_suffix_hits():
    assert classify_seasoning("葱花") == "葱"
    assert classify_seasoning("姜片") == "姜"
    assert classify_seasoning("蒜末") == "蒜"


def test_non_seasonings():
    for name in ["土豆", "鸡蛋", "猪肉", "牛油果", "抹茶粉", "厚椰乳", "青椒"]:
        assert classify_seasoning(name) is None, name


def test_boundary():
    assert classify_seasoning("干辣椒") == "干辣椒"
    assert classify_seasoning("辣椒") is None
    assert classify_seasoning("番茄") is None
    assert classify_seasoning("番茄酱") == "番茄酱"
    assert classify_seasoning("香油") == "香油"


def test_empty_and_whitespace():
    assert classify_seasoning("") is None
    assert classify_seasoning("  盐  ") == "盐"
    assert classify_seasoning(None) is None


def test_group_count():
    assert len(SEASONING_GROUPS) >= 30
