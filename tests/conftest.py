"""pytest 共享夹具：测试库迁移 + 种子数据。"""

import os

TEST_DATABASE_URL = "mysql+pymysql://ai_cooker:ai_cooker@127.0.0.1:3306/ai_cooker_test"

# 必须在导入 app 之前设置，保证 engine 指向测试库
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("CHROMA_DIR", "./data/chroma-test")
# P6.1 性能优化在测试中默认关闭，保证既有用例行为不变：
# 推荐缓存 TTL=0（含降级短缓存）、启动预热关闭（避免测试触发真实 MySQL/Chroma 预热）
os.environ.setdefault("RECOMMEND_CACHE_TTL_SECONDS", "0")
os.environ.setdefault("RECOMMEND_CACHE_DEGRADED_TTL_SECONDS", "0")
os.environ.setdefault("WARMUP_ON_STARTUP", "false")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.embeddings import EmbeddingProvider  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.retrieval.bm25 import tokenize  # noqa: E402
from scripts.seed_dictionary import seed  # noqa: E402

get_settings.cache_clear()
assert get_settings().database_url == TEST_DATABASE_URL


class FakeEmbeddings(EmbeddingProvider):
    """确定性伪嵌入（md5 词袋哈希），离线测试共享；fail=True 模拟嵌入故障。"""

    def __init__(self, dim: int = 128, fail: bool = False) -> None:
        import hashlib
        import math

        self.dim = dim
        self.fail = fail
        self.calls = 0
        self._hashlib = hashlib
        self._math = math

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embed service down")
        return [self._embed(t) for t in texts]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            digest = int(self._hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            vec[digest % self.dim] += 1.0
        norm = self._math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """对整个测试会话执行一次迁移（幂等：重复 upgrade 为空操作）。"""
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session")
def seeded_db(migrated_db):
    """测试库播种一次；依赖种子数据的用例显式声明该夹具。"""
    with SessionLocal() as session:
        seed(session)
    yield


@pytest.fixture()
def client(seeded_db):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session(seeded_db):
    with SessionLocal() as session:
        yield session
        session.rollback()
