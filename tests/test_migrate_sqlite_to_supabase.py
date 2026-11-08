"""
tests/test_migrate_sqlite_to_supabase.py
==============================================================================
Kiểm thử toàn diện script chuyển dữ liệu SQLite sang Supabase:
- Khởi tạo SQLite tạm thời với đầy đủ 5 bảng cũ.
- Kiểm thử ánh xạ 7 loại event_type vào analytics_events.
- Kiểm thử cơ chế làm sạch dữ liệu nhạy cảm (sanitize_text).
- Kiểm thử chế độ --dry-run (không ghi vào DB).
- Kiểm thử cơ chế chạy lại an toàn (Idempotent: bỏ qua bản ghi đã có).
- Kiểm thử bảo vệ khóa ngoại (location_id không tồn tại, client_id mồ côi).
==============================================================================
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Thêm scripts/ và content-service/ vào sys.path để import
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
CONTENT_DIR = Path(__file__).resolve().parent.parent / "content-service"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CONTENT_DIR))

from migrate_sqlite_to_supabase import (
    SQLiteMigrator,
    SQLiteReader,
    SupabaseClient,
    sanitize_text,
    ts_to_iso,
)


@pytest.fixture
def temp_sqlite_db():
    """Tạo database SQLite tạm thời có đủ dữ liệu mẫu của 5 bảng cũ."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # 1. visitor_sessions
        conn.execute("""
            CREATE TABLE visitor_sessions (
                client_id TEXT PRIMARY KEY,
                first_seen_ts REAL,
                last_seen_ts REAL,
                total_heartbeats INTEGER,
                user_agent_short TEXT
            )
        """)
        conn.execute(
            "INSERT INTO visitor_sessions VALUES (?, ?, ?, ?, ?)",
            ("cid_user_1", 1700000000.0, 1700000100.0, 5, "Mozilla/5.0 Chrome"),
        )

        # 2. page_views
        conn.execute("""
            CREATE TABLE page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT,
                path TEXT,
                created_ts REAL
            )
        """)
        conn.execute(
            "INSERT INTO page_views (client_id, path, created_ts) VALUES (?, ?, ?)",
            ("cid_user_1", "/app/?ref=qr", 1700000010.0),
        )
        conn.execute(
            "INSERT INTO page_views (client_id, path, created_ts) VALUES (?, ?, ?)",
            ("cid_orphan_2", "/app/", 1700000020.0),
        )

        # 3. location_events
        conn.execute("""
            CREATE TABLE location_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT,
                location_id INTEGER,
                location_name TEXT,
                language TEXT,
                event_type TEXT,
                distance_m REAL,
                created_ts REAL
            )
        """)
        conn.execute(
            "INSERT INTO location_events (client_id, location_id, location_name, language, event_type, distance_m, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("cid_user_1", 101, "Nhà hát Lớn", "vi", "activated", 45.2, 1700000030.0),
        )
        conn.execute(
            "INSERT INTO location_events (client_id, location_id, location_name, language, event_type, distance_m, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("cid_user_1", 999, "Điểm Chưa Đăng Ký", "en", "detected", 120.0, 1700000040.0),
        )

        # 4. narration_events
        conn.execute("""
            CREATE TABLE narration_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER,
                language TEXT,
                event_type TEXT,
                duration_ms REAL,
                is_fallback INTEGER,
                status_reason TEXT,
                created_ts REAL
            )
        """)
        conn.execute(
            "INSERT INTO narration_events (location_id, language, event_type, duration_ms, is_fallback, status_reason, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (101, "vi", "tts_success", 520.5, 0, "audio_ready", 1700000050.0),
        )
        conn.execute(
            "INSERT INTO narration_events (location_id, language, event_type, duration_ms, is_fallback, status_reason, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (101, "ja", "tts_fail", 310.0, 1, "gemini_timeout", 1700000060.0),
        )
        conn.execute(
            "INSERT INTO narration_events (location_id, language, event_type, duration_ms, is_fallback, status_reason, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (101, "fr", "tts_requested", None, 0, None, 1700000070.0),
        )

        # 5. error_logs
        conn.execute("""
            CREATE TABLE error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT,
                request_type TEXT,
                status_code INTEGER,
                error_type TEXT,
                message TEXT,
                created_ts REAL
            )
        """)
        conn.execute(
            "INSERT INTO error_logs (service, request_type, status_code, error_type, message, created_ts) VALUES (?, ?, ?, ?, ?, ?)",
            ("gateway-service", "ADMIN_LOGIN_FAIL", 401, "AuthFailure", "Thử đăng nhập sai mật khẩu password=secret123 từ IP 192.168.1.100", 1700000080.0),
        )
        conn.commit()
    finally:
        conn.close()

    yield db_path

    import gc
    gc.collect()
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass


def test_sanitize_text():
    """Kiểm tra hàm làm sạch loại bỏ mật khẩu, token và IPv4."""
    raw = "User password=mypassword token=xyz123 Bearer abc.def.ghi from 10.0.0.1"
    clean = sanitize_text(raw)
    assert "mypassword" not in clean
    assert "password=[REDACTED]" in clean
    assert "Bearer [REDACTED]" in clean
    assert "10.0.0.1" not in clean
    assert "[IP_REDACTED]" in clean


def test_sqlite_reader_counts(temp_sqlite_db):
    """Kiểm tra SQLiteReader đếm đúng số dòng các bảng."""
    reader = SQLiteReader(temp_sqlite_db)
    assert reader.count_rows("visitor_sessions") == 1
    assert reader.count_rows("page_views") == 2
    assert reader.count_rows("location_events") == 2
    assert reader.count_rows("narration_events") == 3
    assert reader.count_rows("error_logs") == 1


def test_dry_run_does_not_call_insert(temp_sqlite_db):
    """Kiểm tra chế độ --dry-run chuẩn bị dữ liệu chính xác nhưng không gọi insert_batch."""
    reader = SQLiteReader(temp_sqlite_db)
    mock_sb = MagicMock(spec=SupabaseClient)
    mock_sb.fetch_existing_sessions.return_value = set()
    mock_sb.fetch_existing_location_ids.return_value = {101}
    mock_sb.fetch_migrated_event_ids.return_value = set()

    migrator = SQLiteMigrator(
        sqlite_reader=reader,
        supabase_client=mock_sb,
        batch_size=10,
        dry_run=True,
    )

    result = migrator.run()
    assert result is True
    # Kiểm tra không có lệnh chèn nào được gọi
    mock_sb.insert_batch.assert_not_called()
    assert migrator.stats["visitor_sessions"]["to_insert"] == 2  # cid_user_1 + cid_orphan_2
    assert migrator.stats["page_views"]["to_insert"] == 2
    assert migrator.stats["location_events"]["to_insert"] == 2
    assert migrator.stats["narration_events"]["to_insert"] == 3
    assert migrator.stats["error_logs"]["to_insert"] == 1


def test_event_mapping_and_idempotency(temp_sqlite_db):
    """Kiểm tra ánh xạ đúng 7 loại event_type và bỏ qua bản ghi đã có."""
    reader = SQLiteReader(temp_sqlite_db)
    mock_sb = MagicMock(spec=SupabaseClient)
    # Giả lập đã có session cid_user_1
    mock_sb.fetch_existing_sessions.return_value = {"cid_user_1"}
    mock_sb.fetch_existing_location_ids.return_value = {101}
    # Giả lập event id 1 của narration_events đã được migrate
    mock_sb.fetch_migrated_event_ids.side_effect = lambda src: {"1"} if src == "narration_events" else set()

    inserted_records = {}

    def fake_insert(table_name, rows, on_conflict_do_nothing=False):
        inserted_records.setdefault(table_name, []).extend(rows)
        return len(rows)

    mock_sb.insert_batch.side_effect = fake_insert

    migrator = SQLiteMigrator(
        sqlite_reader=reader,
        supabase_client=mock_sb,
        batch_size=10,
        dry_run=False,
    )

    migrator.run()

    # 1. visitor_sessions: chỉ chèn session mới cid_orphan_2
    sessions = inserted_records.get("visitor_sessions", [])
    assert len(sessions) == 1
    assert sessions[0]["client_id"] == "cid_orphan_2"

    # 2. analytics_events: kiểm tra từng loại event được chèn
    events = inserted_records.get("analytics_events", [])
    event_types = {e["event_type"] for e in events}

    assert "page_view" in event_types
    assert "location_activated" in event_types
    assert "location_detected" in event_types
    assert "narration_failed" in event_types  # narration id 2
    assert "narration_requested" in event_types  # narration id 3
    assert "narration_success" not in event_types  # id 1 đã bị skip do đã migrate
    assert "error" in event_types

    # 3. Kiểm tra bảo vệ location_id không tồn tại (location 999 không có trong valid_ids)
    loc_events = [e for e in events if e["event_type"] == "location_detected"]
    assert len(loc_events) == 1
    assert loc_events[0]["location_id"] is None
    assert loc_events[0]["metadata"]["original_location_id"] == 999

    # 4. Kiểm tra làm sạch tin nhắn lỗi
    err_events = [e for e in events if e["event_type"] == "error"]
    assert len(err_events) == 1
    assert "secret123" not in err_events[0]["message"]
    assert "password=[REDACTED]" in err_events[0]["message"]
    assert "192.168.1.100" not in err_events[0]["message"]
