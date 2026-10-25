"""Chép địa điểm SQLite cũ sang Supabase, giữ ID và không sửa file nguồn.

Đặt DATABASE_URL bằng connection string Supabase trước khi chạy:
    python import_sqlite.py duong-dan/content.db
Không tự nạp .env và không in connection string/secret.
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from models import IS_SQLITE, Location, SessionLocal


def main():
    parser = argparse.ArgumentParser(description="Chuyển địa điểm SQLite sang Supabase PostgreSQL")
    parser.add_argument("sqlite_file", type=Path)
    args = parser.parse_args()
    if IS_SQLITE:
        parser.error("Hãy đặt DATABASE_URL trỏ tới PostgreSQL Supabase trước khi chạy")
    source_path = args.sqlite_file.resolve(strict=True)
    connection = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM locations").fetchall()
    finally:
        connection.close()
    added = 0
    columns = {c.name for c in Location.__table__.columns}
    try:
        with SessionLocal.begin() as db:
            for item in rows:
                data = {k: item[k] for k in item.keys() if k in columns}
                if data.get("last_synced_at"):
                    data["last_synced_at"] = datetime.fromisoformat(data["last_synced_at"])
                if data.get("is_ready") is not None:
                    data["is_ready"] = bool(data["is_ready"])
                # Cache MP3 cũ chưa nằm trên Storage, không xem là ready.
                data["is_ready"] = False
                existing = db.get(Location, data["id"])
                if existing:
                    if any(getattr(existing, k) != v for k, v in data.items() if k != "is_ready"):
                        raise ValueError(f"ID {data['id']} đã có dữ liệu khác; dừng để tránh ghi đè")
                    continue
                db.add(Location(**data))
                added += 1
            db.flush()
            db.execute(text("""SELECT setval(pg_get_serial_sequence('public.locations', 'id'),
                COALESCE((SELECT MAX(id) FROM public.locations), 1),
                EXISTS(SELECT 1 FROM public.locations))"""))
    except SQLAlchemyError:
        raise SystemExit("Không nhập được dữ liệu; đã rollback. Kiểm tra migration, kết nối và trùng bài nguồn.")
    print(f"Đã thêm {added} địa điểm; giữ nguyên file SQLite nguồn.")


if __name__ == "__main__":
    main()
