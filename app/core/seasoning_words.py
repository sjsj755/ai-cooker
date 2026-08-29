"""调料词表与分流判定：parse 阶段将用料条目分为食材/调料。

规则：名称规范化后，先精确匹配别名；未命中时按“最长别名 前缀/后缀 匹配”兜底
（如 葱花→葱、姜片→姜），避免“牛油果”含“油”被误判为调料。
命中返回归一后的调料名，未命中返回 None（归入食材）。
"""

from __future__ import annotations

from app.core.html_clean import clean_text

# (归一名称, 别名列表)
SEASONING_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("盐", ("盐", "食盐", "海盐", "岩盐", "低钠盐")),
    ("糖", ("糖", "白糖", "砂糖", "白砂糖", "细砂糖", "冰糖", "红糖")),
    ("酱油", ("酱油", "生抽", "老抽", "味极鲜", "蒸鱼豉油")),
    ("醋", ("醋", "香醋", "陈醋", "白醋", "米醋", "黑醋")),
    ("料酒", ("料酒", "黄酒", "花雕酒")),
    (
        "食用油",
        (
            "食用油",
            "油",
            "色拉油",
            "菜籽油",
            "花生油",
            "玉米油",
            "大豆油",
            "橄榄油",
            "猪油",
            "黄油",
            "植物油",
        ),
    ),
    ("香油", ("香油", "芝麻油", "麻油")),
    ("蚝油", ("蚝油",)),
    ("鸡精", ("鸡精",)),
    ("味精", ("味精",)),
    ("胡椒粉", ("胡椒粉", "白胡椒粉", "黑胡椒粉", "白胡椒", "黑胡椒")),
    ("辣椒粉", ("辣椒粉", "辣椒面", "辣椒碎")),
    ("花椒粉", ("花椒粉", "花椒面")),
    ("豆瓣酱", ("豆瓣酱", "郫县豆瓣", "豆瓣")),
    ("黄豆酱", ("黄豆酱",)),
    ("甜面酱", ("甜面酱",)),
    ("番茄酱", ("番茄酱", "蕃茄酱")),
    ("淀粉", ("淀粉", "生粉", "玉米淀粉", "红薯淀粉", "土豆淀粉")),
    ("十三香", ("十三香",)),
    ("五香粉", ("五香粉",)),
    ("孜然粉", ("孜然粉",)),
    ("小苏打", ("小苏打", "食用小苏打")),
    ("泡打粉", ("泡打粉",)),
    ("蜂蜜", ("蜂蜜",)),
    ("葱", ("葱", "小葱", "香葱", "大葱", "葱花", "葱白", "葱段", "葱末")),
    ("姜", ("姜", "生姜", "老姜", "姜片", "姜丝", "姜末")),
    ("蒜", ("蒜", "大蒜", "蒜头", "蒜末", "蒜片", "蒜蓉")),
    ("八角", ("八角", "大料")),
    ("桂皮", ("桂皮",)),
    ("香叶", ("香叶",)),
    ("花椒", ("花椒",)),
    ("干辣椒", ("干辣椒", "干辣椒段", "干辣椒面")),
    ("芝麻", ("芝麻", "白芝麻", "黑芝麻", "熟芝麻")),
]

# 别名 → 归一名称；子串兜底按别名长度降序取最长命中
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in SEASONING_GROUPS:
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canonical

_ALIASES_BY_LENGTH = sorted(_ALIAS_TO_CANONICAL, key=len, reverse=True)


def normalize_name(name: str | None) -> str:
    """名称规范化：清洗空白（保留内部空格，仅首尾 trim）。"""
    return clean_text(name)


def classify_seasoning(name: str | None) -> str | None:
    """返回归一后的调料名；不是调料返回 None（归入食材）。"""
    norm = normalize_name(name)
    if not norm:
        return None
    exact = _ALIAS_TO_CANONICAL.get(norm)
    if exact:
        return exact
    for alias in _ALIASES_BY_LENGTH:
        if norm.startswith(alias) or norm.endswith(alias):
            return _ALIAS_TO_CANONICAL[alias]
    return None
