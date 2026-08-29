"""pytest 共享夹具：测试库迁移 + 种子数据。"""

import os

TEST_DATABASE_URL = "mysql+pymysql://ai_cooker:ai_cooker@127.0.0.1:3306/ai_cooker_test"

# 必须在导入 app 之前设置，保证 engine 指向测试库
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("CHROMA_DIR", "./data/chroma-test")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed_dictionary import seed  # noqa: E402

get_settings.cache_clear()
assert get_settings().database_url == TEST_DATABASE_URL


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
