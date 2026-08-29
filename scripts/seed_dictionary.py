"""食材词典 + 标签种子：按唯一名幂等 upsert，可重复执行。

用法：uv run python scripts/seed_dictionary.py
"""

import sys
from pathlib import Path

# 允许以 scripts/xxx.py 方式直接运行（确保项目根目录在 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Ingredient, Tag

INGREDIENTS: list[dict] = [
    {"name": "土豆", "aliases": ["马铃薯", "洋芋"], "category": "蔬菜"},
    {"name": "鸡蛋", "aliases": ["蛋", "鸡子"], "category": "蛋类"},
    {"name": "洋葱", "aliases": ["圆葱", "葱头"], "category": "蔬菜"},
    {"name": "西红柿", "aliases": ["番茄", "洋柿子"], "category": "蔬菜"},
    {"name": "青椒", "aliases": ["甜椒", "灯笼椒"], "category": "蔬菜"},
    {"name": "猪肉", "aliases": ["五花肉", "猪五花"], "category": "肉类"},
    {"name": "牛肉", "aliases": ["牛腩", "肥牛"], "category": "肉类"},
    {"name": "鸡肉", "aliases": ["鸡腿肉", "鸡胸肉"], "category": "肉类"},
    {"name": "羊肉", "aliases": ["羊排", "羊腿肉"], "category": "肉类"},
    {"name": "大米", "aliases": ["米饭", "稻米"], "category": "主食"},
    {"name": "面粉", "aliases": ["小麦粉"], "category": "主食"},
    {"name": "面条", "aliases": ["挂面"], "category": "主食"},
    {"name": "豆腐", "aliases": ["老豆腐", "嫩豆腐"], "category": "豆制品"},
    {"name": "白菜", "aliases": ["大白菜", "黄芽白"], "category": "蔬菜"},
    {"name": "菠菜", "aliases": [], "category": "蔬菜"},
    {"name": "西兰花", "aliases": ["绿菜花"], "category": "蔬菜"},
    {"name": "胡萝卜", "aliases": ["红萝卜"], "category": "蔬菜"},
    {"name": "黄瓜", "aliases": ["青瓜"], "category": "蔬菜"},
    {"name": "茄子", "aliases": [], "category": "蔬菜"},
    {"name": "蘑菇", "aliases": ["口蘑", "香菇"], "category": "菌菇"},
    {"name": "虾", "aliases": ["虾仁", "基围虾"], "category": "海鲜"},
    {"name": "鱼", "aliases": ["草鱼", "鲫鱼"], "category": "海鲜"},
    {"name": "葱", "aliases": ["小葱", "香葱"], "category": "调味"},
    {"name": "姜", "aliases": ["生姜", "老姜"], "category": "调味"},
    {"name": "蒜", "aliases": ["大蒜", "蒜头"], "category": "调味"},
    {"name": "辣椒", "aliases": ["尖椒", "小米辣"], "category": "调味"},
    {"name": "酱油", "aliases": ["生抽", "老抽"], "category": "调料"},
    {"name": "盐", "aliases": [], "category": "调料"},
    {"name": "食用油", "aliases": ["花生油", "菜籽油"], "category": "调料"},
    {"name": "白糖", "aliases": ["白砂糖"], "category": "调料"},
    {"name": "牛奶", "aliases": ["鲜奶"], "category": "乳制品"},
    {"name": "花生", "aliases": ["花生米"], "category": "坚果"},
]

TAGS: list[dict] = [
    {"name": "海鲜", "kind": "过敏原"},
    {"name": "坚果", "kind": "过敏原"},
    {"name": "乳制品", "kind": "过敏原"},
    {"name": "素食", "kind": "忌口"},
    {"name": "辣", "kind": "口味"},
]


def _upsert_ingredients(session: Session) -> dict[str, int]:
    created = updated = 0
    for item in INGREDIENTS:
        row = session.scalar(select(Ingredient).where(Ingredient.name == item["name"]))
        if row is None:
            session.add(Ingredient(**item))
            created += 1
        else:
            changed = False
            if row.aliases != item["aliases"]:
                row.aliases = item["aliases"]
                changed = True
            if row.category != item["category"]:
                row.category = item["category"]
                changed = True
            if changed:
                updated += 1
    return {"created": created, "updated": updated}


def _upsert_tags(session: Session) -> dict[str, int]:
    created = updated = 0
    for item in TAGS:
        row = session.scalar(select(Tag).where(Tag.name == item["name"]))
        if row is None:
            session.add(Tag(**item))
            created += 1
        else:
            if row.kind != item["kind"]:
                row.kind = item["kind"]
                updated += 1
    return {"created": created, "updated": updated}


def seed(session: Session) -> dict[str, dict[str, int]]:
    """幂等播种；重复执行 created/updated 均为 0。"""
    stats = {
        "ingredients": _upsert_ingredients(session),
        "tags": _upsert_tags(session),
    }
    session.commit()
    return stats


def main() -> None:
    with SessionLocal() as session:
        print(seed(session))


if __name__ == "__main__":
    main()
