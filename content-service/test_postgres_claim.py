"""Tùy chọn: kiểm tra chống trùng bằng PostgreSQL THẬT trong schema tạm riêng.

TEST_POSTGRES_URL phải là database test có quyền tạo schema, tuyệt đối không dùng
database production. Không có cấu hình thì skip, không giả vờ đã kiểm chứng PG.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models import Base, Location
from narration_pipeline import choose_voice, make_key
from narration_store import NarrationStore
from test_narration_pipeline import LOCATION


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Chưa cấu hình PostgreSQL test")
def test_postgres_unique_claim_across_connections():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=5, max_overflow=15)
    schema = "narration_test_" + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        sessions = sessionmaker(isolated)
        with sessions.begin() as db:
            db.add(Location(id=1, name="Test", description_vi="Test", target_languages="vi"))
        key, snapshot = make_key(LOCATION, "vi", choose_voice("vi"))
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _: NarrationStore(sessions).claim(key, snapshot), range(20)))
        assert sum(claimed for _, claimed in results) == 1
    finally:
        # Chỉ xóa schema ngẫu nhiên mà chính test này vừa tạo.
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
