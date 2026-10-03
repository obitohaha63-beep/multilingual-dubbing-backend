"""
Migration thủ công cho SQLite, có đánh version, CHẠY LẶP LẠI AN TOÀN
(idempotent) - chạy 2 lần không lỗi, không mất dữ liệu cũ.

Vì sao không dùng Base.metadata.create_all() cho việc này?
-> create_all() CHỈ tạo bảng nếu bảng đó CHƯA TỒN TẠI. Nếu bảng "locations"
   đã có sẵn từ tuần 2 (chỉ 4 cột), create_all() sẽ thấy bảng đã tồn tại
   và BỎ QUA HOÀN TOÀN, không tự thêm cột mới -> dữ liệu tuần 2 sẽ thiếu
   cột khi tuần 3 cần dùng. Vì vậy phải tự viết ALTER TABLE bằng tay.

Cách hoạt động:
  1. Sao lưu file DB hiện tại (nếu có) trước khi đổi gì, phòng khi lỗi.
  2. Tạo bảng "schema_migrations" để nhớ các version đã chạy.
  3. Với mỗi version CHƯA chạy: kiểm tra cột đã tồn tại chưa (PRAGMA
     table_info) rồi mới ALTER TABLE ADD COLUMN (tránh lỗi "duplicate
     column" nếu lỡ chạy lại).
  4. Backfill dữ liệu cũ, CHỈ cho những dòng còn thiếu (WHERE ... IS NULL)
     -> chạy lại lần 2 sẽ không ghi đè gì thêm, vẫn an toàn.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone

from models import DB_PATH

# Mỗi cột mới của tuần 3: (tên cột, kiểu SQL)
NEW_COLUMNS = [
    ("description_source", "TEXT"),
    ("latitude", "REAL"),
    ("longitude", "REAL"),
    ("radius", "REAL"),
    ("source_lang", "TEXT"),
    ("source", "TEXT"),
    ("source_title", "TEXT"),
    ("source_page_id", "INTEGER"),
    ("source_url", "TEXT"),
    ("last_synced_at", "TEXT"),
    ("is_ready", "INTEGER"),
]

MIGRATION_VERSION = 2  # version 1 coi như "schema tuần 2" (không ghi nhận vì
                        # lúc đó chưa có bảng schema_migrations)


def _backup_db():
    if os.path.exists(DB_PATH):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = f"{DB_PATH}.bak-{ts}"
        shutil.copy2(DB_PATH, backup_path)
        return backup_path
    return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    cur = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _mark_applied(conn: sqlite3.Connection, version: int):
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, datetime.now(timezone.utc).isoformat()),
    )


def _apply_v2(conn: sqlite3.Connection):
    """Thêm cột GPS + metadata nguồn, backfill dữ liệu cũ, tạo unique index."""
    if not _table_exists(conn, "locations"):
        # Chưa có bảng locations (DB hoàn toàn mới) -> không có gì để
        # migrate, Base.metadata.create_all() ở main.py sẽ tự tạo bảng
        # đầy đủ cột ngay từ đầu.
        return

    cols = _existing_columns(conn, "locations")
    for col_name, col_type in NEW_COLUMNS:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE locations ADD COLUMN {col_name} {col_type}")

    # Backfill cho các record cũ (tuần 2): chỉ áp dụng cho dòng CHƯA có
    # description_source -> chạy lại lần 2 sẽ không đụng tới nữa.
    # Không gán tọa độ 0,0 cho record cũ -> latitude/longitude giữ NULL.
    conn.execute(
        """
        UPDATE locations
        SET description_source = description_vi,
            source_lang = COALESCE(source_lang, 'vi'),
            source = COALESCE(source, 'manual'),
            radius = COALESCE(radius, 80),
            is_ready = COALESCE(is_ready, 0)
        WHERE description_source IS NULL
        """
    )

    # Unique index để chặn trùng Wikimedia theo (source, source_lang,
    # source_page_id). CREATE UNIQUE INDEX IF NOT EXISTS -> chạy lại
    # không lỗi.
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_locations_source
        ON locations (source, source_lang, source_page_id)
        """
    )


MIGRATIONS = {
    2: _apply_v2,
}


def run_migrations(verbose: bool = True) -> list[int]:
    """Chạy toàn bộ migration còn thiếu. Trả về danh sách version vừa áp dụng."""
    backup_path = _backup_db()
    if verbose and backup_path:
        print(f"[migrate] Đã sao lưu DB -> {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    applied_now = []
    try:
        already = _applied_versions(conn)
        for version in sorted(MIGRATIONS):
            if version in already:
                if verbose:
                    print(f"[migrate] version {version} đã chạy trước đó, bỏ qua.")
                continue
            if verbose:
                print(f"[migrate] Đang chạy version {version} ...")
            MIGRATIONS[version](conn)
            _mark_applied(conn, version)
            applied_now.append(version)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if verbose:
        if applied_now:
            print(f"[migrate] Hoàn tất. Đã áp dụng: {applied_now}")
        else:
            print("[migrate] Không có gì mới để chạy, schema đã cập nhật.")
    return applied_now


if __name__ == "__main__":
    run_migrations()
