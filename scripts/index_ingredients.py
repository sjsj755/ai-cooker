"""食材词典向量化：name + aliases 幂等 upsert 到 ingredients_docs。

用法：
    uv run python scripts/index_ingredients.py
缺 EMBEDDING_API_KEY 时退出码 3（与 ingest 一致）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.openai_embeddings import OpenAICompatibleEmbeddings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Ingredient  # noqa: E402
from app.vector_store import ChromaStore  # noqa: E402


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    if not settings.embedding_api_key:
        print("EMBEDDING_API_KEY 未配置，无法执行真实嵌入", file=sys.stderr)
        return 3
    embeddings = OpenAICompatibleEmbeddings(settings)
    store = ChromaStore(settings, collection=settings.chroma_ingredients_collection)

    with SessionLocal() as session:
        rows = session.execute(
            select(Ingredient.id, Ingredient.name, Ingredient.aliases)
        ).all()
    if not rows:
        print("ingredients 表为空，无需索引")
        return 0

    ids = [str(r.id) for r in rows]
    texts = [
        f"{r.name} {' '.join(a for a in (r.aliases or []) if isinstance(a, str))}".strip()
        for r in rows
    ]
    metadatas = [{"ingredient_id": r.id, "name": r.name} for r in rows]
    vectors = asyncio.run(embeddings.embed_texts(texts))

    # 全量刷新：删除集合中已不存在的旧条目（幂等）
    existing = asyncio.run(store.get_chunk_metadata(None))
    known = set(ids)
    stale = [
        str(m.get("ingredient_id"))
        for m in existing
        if m.get("ingredient_id") is not None
        and str(m.get("ingredient_id")) not in known
    ]
    if stale:
        asyncio.run(store.delete_ids(stale))
    asyncio.run(store.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=vectors))
    print(f"已写入 {len(rows)} 条食材向量（清理 {len(stale)} 条失效条目）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
