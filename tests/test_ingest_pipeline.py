"""ingest 管线端到端：测试库 MySQL + 临时 Chroma + FakeEmbeddings（离线）。"""

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.config import Settings
from app.core.crawler import CrawledIngredient, CrawledRecipe
from app.core.embeddings import EmbeddingProvider
from app.db.session import SessionLocal
from app.ingestion.json_store import JsonStore, build_envelope
from app.ingestion.pipeline import run_ingest
from app.ingestion.text_builder import chunk_recipe
from app.models import Ingredient, Recipe
from app.vector_store import ChromaStore

SUFFIX = uuid.uuid4().hex[:8]


class FakeEmbeddings(EmbeddingProvider):
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embed service down")
        return [[float(j), float(j + 1), 0.0, 1.0] for j in range(len(texts))]


def _recipe(url: str, title: str) -> CrawledRecipe:
    return CrawledRecipe(
        title=title,
        source_url=url,
        description="测试描述",
        ingredients=[CrawledIngredient(name="土豆", amount="两个")],
        seasonings=[CrawledIngredient(name="蚝油", amount="1勺", is_essential=False)],
        tags=["素菜"],
        steps=[{"instruction": "切丝翻炒", "minutes": None}],
    )


def _write(store: JsonStore, site: str, recipe: CrawledRecipe) -> None:
    store.write_recipe(site, recipe.source_url, build_envelope(recipe, site))


def _chroma(tmp_path: Path) -> ChromaStore:
    return ChromaStore(path=str(tmp_path / "chroma"))


def _recipe_count(urls: list[str]) -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count()).select_from(Recipe).where(Recipe.source_url.in_(urls))
        )


def test_ingest_end_to_end_idempotent(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    recipes = [
        _recipe(f"https://www.xiachufang.com/recipe/{SUFFIX}900001/", "测试一"),
        _recipe(f"https://www.xiachufang.com/recipe/{SUFFIX}900002/", "测试二"),
    ]
    for r in recipes:
        _write(store, "xiachufang", r)
    chroma = _chroma(tmp_path)
    embeddings = FakeEmbeddings()
    urls = [r.source_url for r in recipes]
    expected_chunks = sum(len(chunk_recipe(r)) for r in recipes)

    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=embeddings,
            chroma=chroma,
        )
    )
    assert code == 0
    assert _recipe_count(urls) == 2
    assert chroma.count() == expected_chunks


def test_rerun_cleans_stale_chunks(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    url = f"https://www.xiachufang.com/recipe/{SUFFIX}900007/"
    v1 = _recipe(url, "旧版")
    v1.steps = [
        {"instruction": "翻炒食材。" * 40, "minutes": None},
        {"instruction": "大火收汁。" * 40, "minutes": None},
        {"instruction": "装盘出锅。" * 40, "minutes": None},
    ]
    _write(store, "xiachufang", v1)
    chroma = _chroma(tmp_path)
    embeddings = FakeEmbeddings()
    asyncio.run(
        run_ingest(
            settings, site="xiachufang", out_dir=tmp_path, embeddings=embeddings, chroma=chroma
        )
    )
    count_v1 = chroma.count()
    assert count_v1 == len(chunk_recipe(v1))

    v2 = _recipe(url, "新版")
    v2.steps = [{"instruction": "只剩一步", "minutes": None}]
    _write(store, "xiachufang", v2)
    asyncio.run(
        run_ingest(
            settings, site="xiachufang", out_dir=tmp_path, embeddings=embeddings, chroma=chroma
        )
    )
    assert chroma.count() == len(chunk_recipe(v2))
    assert chroma.count() < count_v1


def test_chunk_metadata_unit_type(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    url = f"https://www.xiachufang.com/recipe/{SUFFIX}900008/"
    recipe = _recipe(url, "元数据")
    recipe.steps = [
        {"instruction": "切丝", "minutes": None},
        {"instruction": "翻炒", "minutes": None},
    ]
    _write(store, "xiachufang", recipe)
    chroma = _chroma(tmp_path)
    asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(),
            chroma=chroma,
        )
    )
    metas = asyncio.run(chroma.get_chunk_metadata({"source_url": url}))
    assert len(metas) == len(chunk_recipe(recipe))
    types = {m["unit_type"] for m in metas}
    assert types == {"header", "ingredients", "steps"}
    steps_meta = [m for m in metas if m["unit_type"] == "steps"]
    assert steps_meta[0]["step_start"] == 1
    assert steps_meta[-1]["step_end"] == 2


def test_seasoning_category_on_create_and_keep_existing(tmp_path, seeded_db):
    settings = Settings()
    store = JsonStore(tmp_path)
    recipe = _recipe(f"https://www.xiachufang.com/recipe/{SUFFIX}900003/", "调料映射")
    recipe.seasonings = [
        CrawledIngredient(name="蚝油", amount="1勺", is_essential=False),
        CrawledIngredient(name="葱", amount="适量", is_essential=False),
    ]
    _write(store, "xiachufang", recipe)
    chroma = _chroma(tmp_path)

    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(),
            chroma=chroma,
        )
    )
    assert code == 0
    with SessionLocal() as session:
        haoyou = session.scalar(select(Ingredient).where(Ingredient.name == "蚝油"))
        assert haoyou is not None
        assert haoyou.category == "调料"
        cong = session.scalar(select(Ingredient).where(Ingredient.name == "葱"))
        assert cong is not None
        assert cong.category == "调味"  # 种子已有行，保持不动


def test_duplicate_seasoning_name_dedup_on_save(tmp_path, seeded_db):
    settings = Settings()
    store = JsonStore(tmp_path)
    url = f"https://www.xiachufang.com/recipe/{SUFFIX}900006/"
    recipe = _recipe(url, "重复调料")
    recipe.seasonings = [
        CrawledIngredient(name="酱油", amount="1勺", is_essential=False),
        CrawledIngredient(name="酱油", amount="半勺", is_essential=False),
    ]
    _write(store, "xiachufang", recipe)
    chroma = _chroma(tmp_path)
    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(),
            chroma=chroma,
        )
    )
    assert code == 0
    with SessionLocal() as session:
        row = session.scalar(select(Recipe).where(Recipe.source_url == url))
        assert row is not None
        assert len(row.recipe_ingredients) == 2  # 土豆 + 酱油（生抽/老抽 归并）
        jiangyou = session.scalar(select(Ingredient).where(Ingredient.name == "酱油"))
        assert jiangyou is not None
        amounts = [
            ri.amount for ri in row.recipe_ingredients if ri.ingredient_id == jiangyou.id
        ]
        assert amounts == ["1勺、半勺"]


def test_force_rebuilds(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    url = f"https://www.xiachufang.com/recipe/{SUFFIX}900004/"
    _write(store, "xiachufang", _recipe(url, "旧标题"))
    chroma = _chroma(tmp_path)
    embeddings = FakeEmbeddings()
    asyncio.run(
        run_ingest(
            settings, site="xiachufang", out_dir=tmp_path, embeddings=embeddings, chroma=chroma
        )
    )
    assert _recipe_count([url]) == 1

    _write(store, "xiachufang", _recipe(url, "新标题"))
    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=embeddings,
            chroma=chroma,
            force=True,
        )
    )
    assert code == 0
    assert _recipe_count([url]) == 1
    with SessionLocal() as session:
        row = session.scalar(select(Recipe).where(Recipe.source_url == url))
        assert row.title == "新标题"


def test_invalid_file_moved(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    bad = store.site_dir("xiachufang") / ("a" * 64 + ".json")
    bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    chroma = _chroma(tmp_path)

    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(),
            chroma=chroma,
        )
    )
    assert code == 0
    assert not bad.exists()
    invalid_dir = tmp_path / "xiachufang" / "invalid"
    assert (invalid_dir / bad.name).exists()
    assert (invalid_dir / "reasons.jsonl").exists()


def test_dry_run_no_side_effects(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    url = f"https://www.xiachufang.com/recipe/{SUFFIX}900005/"
    _write(store, "xiachufang", _recipe(url, "干跑"))

    code = asyncio.run(
        run_ingest(settings, site="xiachufang", out_dir=tmp_path, dry_run=True)
    )
    assert code == 0
    assert _recipe_count([url]) == 0
    assert not (tmp_path / "xiachufang" / "invalid").exists()


def test_circuit_breaker_and_self_heal(tmp_path):
    settings = Settings()
    store = JsonStore(tmp_path)
    recipes = [
        _recipe(f"https://www.xiachufang.com/recipe/{SUFFIX}91000{i}/", f"熔断{i}")
        for i in range(1, 7)
    ]
    for r in recipes:
        _write(store, "xiachufang", r)
    chroma = _chroma(tmp_path)
    urls = [r.source_url for r in recipes]

    code = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(fail=True),
            chroma=chroma,
        )
    )
    assert code == 3  # 连续 5 次失败熔断
    assert _recipe_count(urls) == 5  # MySQL 已提交 5 条（Chroma 缺失）
    assert chroma.count() == 0

    code2 = asyncio.run(
        run_ingest(
            settings,
            site="xiachufang",
            out_dir=tmp_path,
            embeddings=FakeEmbeddings(),
            chroma=chroma,
        )
    )
    assert code2 == 0
    expected = sum(len(chunk_recipe(r)) for r in recipes)
    assert chroma.count() == expected  # 重跑自愈补齐 Chroma
