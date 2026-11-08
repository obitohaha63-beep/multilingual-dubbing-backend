#!/usr/bin/env python3
"""
scripts/migrate_sqlite_to_supabase.py
==============================================================================
Script chuyển dữ liệu lịch sử từ SQLite cũ (analytics.db) sang Supabase PostgreSQL:
1. visitor_sessions -> visitor_sessions (PK client_id, idempotent).
2. page_views -> analytics_events (event_type = 'page_view') [+ tùy chọn visitor_access_logs].
3. location_events -> analytics_events (event_type = 'location_activated' / 'location_detected').
4. narration_events -> analytics_events (event_type = 'narration_success' / 'narration_failed' / 'narration_requested').
5. error_logs -> analytics_events (event_type = 'error', làm sạch dữ liệu nhạy cảm).

Tính năng:
- Hỗ trợ cả 2 phương thức kết nối: DATABASE_URL (Direct PostgreSQL) hoặc PostgREST (SUPABASE_URL + SUPABASE_SECRET_KEY).
- Chế độ --dry-run: kiểm tra tính hợp lệ dữ liệu, đếm bản ghi và báo cáo chênh lệch mà không sửa đổi DB.
- Idempotent: chạy lại nhiều lần an toàn, không chèn trùng (lưu sqlite_source và sqlite_id trong metadata).
- Chèn theo lô (batch size) có thể điều chỉnh (--batch-size, mặc định 100).
- An toàn tuyệt đối: không sửa đổi hay xóa file SQLite gốc; không ghi đè hay xóa dữ liệu Supabase hiện có.
- Tự động bảo vệ khóa ngoại: kiểm tra và xử lý client_id mồ côi và location_id không tồn tại.
- Đọc biến môi trường từ .env hoặc môi trường thực thi, không hard-code thông tin bí mật.
==============================================================================
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from dotenv import load_dotenv

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_sqlite_to_supabase")


def sanitize_text(text: Optional[str]) -> str:
    """Loại bỏ mật khẩu, token, API key và địa chỉ IPv4 khỏi văn bản."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(
        r"(?i)(password|passwd|secret|token|authorization|cookie|session|api[_-]?key)[=:\s]+[^\s,;&]+",
        r"\1=[REDACTED]",
        s,
    )
    s = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", s)
    s = re.sub(r"://[^/:\s@]+:[^@\s]+@", "://[REDACTED]@", s)
    s = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP_REDACTED]", s)
    return s


def ts_to_iso(ts: Optional[float]) -> str:
    """Chuyển đổi unix timestamp sang chuỗi ISO 8601 UTC."""
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


class SQLiteReader:
    """Đọc dữ liệu an toàn ở chế độ chỉ đọc từ file SQLite."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file SQLite tại: {self.db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        # Mở ở chế độ URI read-only nếu hỗ trợ, hoặc timeout an toàn
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        except Exception:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get_table_names(self) -> List[str]:
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def count_rows(self, table_name: str) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
            return cur.fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def fetch_all(self, table_name: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.execute(f"SELECT * FROM {table_name} ORDER BY rowid ASC")
            return [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()


class SupabaseClient:
    """Giao diện kết nối Supabase qua PostgREST hoặc SQLAlchemy (DATABASE_URL)."""

    def __init__(self, url: Optional[str], key: Optional[str], database_url: Optional[str]):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.database_url = database_url or ""
        self.engine = None

        if self.database_url:
            try:
                from sqlalchemy import create_engine
                from models import normalize_database_url
                normalized_db = normalize_database_url(self.database_url)
                self.engine = create_engine(normalized_db, pool_pre_ping=True)
                logger.info("Đã khởi tạo engine kết nối PostgreSQL trực tiếp (DATABASE_URL).")
            except Exception as exc:
                logger.warning("Không thể khởi tạo SQLAlchemy engine (%s), sẽ dùng PostgREST: %s", self.database_url[:15], exc)

        if not self.engine and not (self.url and self.key):
            raise ValueError("Cần cấu hình ít nhất một trong: (SUPABASE_URL + SUPABASE_SECRET_KEY) hoặc DATABASE_URL hợp lệ.")

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        h = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def count_remote_rows(self, table_name: str) -> int:
        """Đếm số bản ghi hiện có trên bảng Supabase."""
        if self.engine:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                res = conn.execute(text(f"SELECT COUNT(*) FROM public.{table_name}"))
                return res.scalar() or 0

        # Qua PostgREST
        req_url = f"{self.url}/rest/v1/{table_name}?select=count"
        h = self._headers()
        h["Range-Unit"] = "items"
        h["Range"] = "0-0"
        h["Prefer"] = "count=exact"
        with httpx.Client(timeout=10.0) as client:
            resp = client.head(req_url, headers=h)
            cr = resp.headers.get("content-range")
            if cr and "/" in cr:
                tot = cr.split("/")[-1]
                if tot.isdigit():
                    return int(tot)
        return 0

    def fetch_existing_sessions(self) -> Set[str]:
        """Lấy danh sách client_id đã có trong visitor_sessions."""
        if self.engine:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                res = conn.execute(text("SELECT client_id FROM public.visitor_sessions"))
                return {str(r[0]) for r in res.fetchall() if r[0]}

        # Qua PostgREST
        existing = set()
        req_url = f"{self.url}/rest/v1/visitor_sessions?select=client_id"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(req_url, headers=self._headers())
            if resp.is_success:
                for item in resp.json():
                    cid = item.get("client_id")
                    if cid:
                        existing.add(str(cid))
        return existing

    def fetch_existing_location_ids(self) -> Set[int]:
        """Lấy danh sách location_id hợp lệ từ bảng locations để kiểm tra khóa ngoại."""
        if self.engine:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                res = conn.execute(text("SELECT id FROM public.locations"))
                return {int(r[0]) for r in res.fetchall() if r[0] is not None}

        # Qua PostgREST
        loc_ids = set()
        req_url = f"{self.url}/rest/v1/locations?select=id"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(req_url, headers=self._headers())
            if resp.is_success:
                for item in resp.json():
                    lid = item.get("id")
                    if lid is not None:
                        loc_ids.add(int(lid))
        return loc_ids

    def fetch_migrated_event_ids(self, source_table: str) -> Set[str]:
        """Lấy danh sách sqlite_id đã được migrate vào analytics_events cho một source table."""
        if self.engine:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                stmt = text(
                    "SELECT metadata->>'sqlite_id' FROM public.analytics_events "
                    "WHERE metadata->>'sqlite_source' = :src AND metadata->>'sqlite_id' IS NOT NULL"
                )
                res = conn.execute(stmt, {"src": source_table})
                return {str(r[0]) for r in res.fetchall() if r[0]}

        # Qua PostgREST: lọc theo metadata->>sqlite_source
        migrated = set()
        req_url = f"{self.url}/rest/v1/analytics_events?select=metadata&metadata->>sqlite_source=eq.{source_table}&limit=10000"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(req_url, headers=self._headers())
            if resp.is_success:
                for item in resp.json():
                    meta = item.get("metadata") or {}
                    sid = meta.get("sqlite_id")
                    if sid:
                        migrated.add(str(sid))
        return migrated

    def insert_batch(self, table_name: str, rows: List[Dict[str, Any]], on_conflict_do_nothing: bool = False) -> int:
        """Chèn một lô bản ghi vào Supabase."""
        if not rows:
            return 0

        if self.engine:
            import json
            from sqlalchemy import text
            # Format insert statement an toàn
            cols = list(rows[0].keys())
            cols_str = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join(f":{c}" for c in cols)
            conflict_clause = ""
            if on_conflict_do_nothing:
                if table_name == "visitor_sessions":
                    conflict_clause = ' ON CONFLICT ("client_id") DO NOTHING'
                elif table_name == "visitor_access_logs":
                    conflict_clause = " ON CONFLICT DO NOTHING"

            sql = f'INSERT INTO public."{table_name}" ({cols_str}) VALUES ({placeholders}){conflict_clause}'
            # Chuyển đổi JSON field nếu cần
            formatted_rows = []
            for r in rows:
                row_copy = dict(r)
                if "metadata" in row_copy and isinstance(row_copy["metadata"], dict):
                    row_copy["metadata"] = json.dumps(row_copy["metadata"])
                formatted_rows.append(row_copy)

            with self.engine.begin() as conn:
                res = conn.execute(text(sql), formatted_rows)
                return res.rowcount if res.rowcount >= 0 else len(rows)

        # Qua PostgREST
        req_url = f"{self.url}/rest/v1/{table_name}"
        prefer = "return=minimal"
        if on_conflict_do_nothing:
            prefer += ",resolution=ignore-duplicates"

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(req_url, headers=self._headers(prefer=prefer), json=rows)
            if resp.is_success:
                return len(rows)
            else:
                logger.error("Lỗi khi chèn lô vào %s: HTTP %s - %s", table_name, resp.status_code, resp.text)
                raise RuntimeError(f"PostgREST error: {resp.status_code} {resp.text}")


class SQLiteMigrator:
    """Bộ điều phối chuyển đổi và đồng bộ dữ liệu SQLite sang Supabase."""

    def __init__(
        self,
        sqlite_reader: SQLiteReader,
        supabase_client: Optional[SupabaseClient],
        batch_size: int = 100,
        dry_run: bool = False,
        include_access_logs: bool = True,
    ):
        self.reader = sqlite_reader
        self.sb = supabase_client
        self.batch_size = max(batch_size, 10)
        self.dry_run = dry_run
        self.include_access_logs = include_access_logs

        # Thống kê trước / sau
        self.stats: Dict[str, Dict[str, Any]] = {
            "visitor_sessions": {"sqlite_total": 0, "existing_remote": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
            "page_views": {"sqlite_total": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
            "location_events": {"sqlite_total": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
            "narration_events": {"sqlite_total": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
            "error_logs": {"sqlite_total": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
            "visitor_access_logs": {"sqlite_total": 0, "to_insert": 0, "inserted": 0, "skipped": 0},
        }

    def run(self) -> bool:
        logger.info("=" * 70)
        logger.info("BẮT ĐẦU TIẾN TRÌNH CHUYỂN DỮ LIỆU SQLITE -> SUPABASE")
        logger.info("Chế độ: %s", "DRY-RUN (CHỈ KIỂM TRA, KHÔNG GHI)" if self.dry_run else "LIVE MIGRATION")
        logger.info("Batch size: %d", self.batch_size)
        logger.info("=" * 70)

        # 1. Đếm số bản ghi SQLite nguồn
        for tbl in ["visitor_sessions", "page_views", "location_events", "narration_events", "error_logs"]:
            cnt = self.reader.count_rows(tbl)
            self.stats[tbl]["sqlite_total"] = cnt
            logger.info("SQLite [%s]: %d bản ghi", tbl, cnt)

        if self.include_access_logs:
            self.stats["visitor_access_logs"]["sqlite_total"] = self.stats["page_views"]["sqlite_total"]

        # 2. Đếm số bản ghi Supabase hiện có (nếu có kết nối)
        remote_counts_before = {}
        valid_location_ids = set()
        existing_session_ids = set()

        if self.sb:
            for tbl in ["visitor_sessions", "analytics_events", "visitor_access_logs"]:
                try:
                    c = self.sb.count_remote_rows(tbl)
                    remote_counts_before[tbl] = c
                    logger.info("Supabase [%s] hiện có: %d bản ghi", tbl, c)
                except Exception as exc:
                    logger.warning("Không thể lấy số bản ghi hiện có của %s: %s", tbl, exc)
                    remote_counts_before[tbl] = 0

            try:
                valid_location_ids = self.sb.fetch_existing_location_ids()
                logger.info("Supabase [locations]: tìm thấy %d location_id hợp lệ", len(valid_location_ids))
            except Exception as exc:
                logger.warning("Không thể đọc danh sách locations từ Supabase: %s", exc)

            try:
                existing_session_ids = self.sb.fetch_existing_sessions()
                logger.info("Supabase [visitor_sessions]: đã có %d sessions", len(existing_session_ids))
            except Exception as exc:
                logger.warning("Không thể đọc danh sách sessions từ Supabase: %s", exc)

        # 3. Chuẩn bị dữ liệu visitor_sessions
        sessions_to_insert = self._prepare_sessions(existing_session_ids)
        self.stats["visitor_sessions"]["to_insert"] = len(sessions_to_insert)

        # 4. Chuẩn bị dữ liệu analytics_events cho từng loại sự kiện
        pv_events, pv_access_logs = self._prepare_page_views()
        loc_events = self._prepare_location_events(valid_location_ids)
        narr_events = self._prepare_narration_events(valid_location_ids)
        err_events = self._prepare_error_logs()

        self.stats["page_views"]["to_insert"] = len(pv_events)
        self.stats["location_events"]["to_insert"] = len(loc_events)
        self.stats["narration_events"]["to_insert"] = len(narr_events)
        self.stats["error_logs"]["to_insert"] = len(err_events)
        self.stats["visitor_access_logs"]["to_insert"] = len(pv_access_logs)

        # 5. Thực thi ghi dữ liệu (nếu không phải dry-run)
        if not self.dry_run and self.sb:
            # 5.1 Ghi visitor_sessions trước để đảm bảo khóa ngoại
            logger.info("--- Đang chèn %d visitor_sessions vào Supabase ---", len(sessions_to_insert))
            self.stats["visitor_sessions"]["inserted"] = self._insert_in_batches(
                "visitor_sessions", sessions_to_insert, on_conflict_do_nothing=True
            )

            # 5.2 Ghi analytics_events
            all_events = pv_events + loc_events + narr_events + err_events
            logger.info("--- Đang chèn %d analytics_events vào Supabase theo lô ---", len(all_events))
            self._insert_in_batches("analytics_events", all_events, on_conflict_do_nothing=False)

            # Cập nhật số đã chèn từng loại
            self.stats["page_views"]["inserted"] = len(pv_events)
            self.stats["location_events"]["inserted"] = len(loc_events)
            self.stats["narration_events"]["inserted"] = len(narr_events)
            self.stats["error_logs"]["inserted"] = len(err_events)

            # 5.3 Tùy chọn ghi visitor_access_logs
            if self.include_access_logs and pv_access_logs:
                logger.info("--- Đang chèn %d visitor_access_logs vào Supabase ---", len(pv_access_logs))
                self.stats["visitor_access_logs"]["inserted"] = self._insert_in_batches(
                    "visitor_access_logs", pv_access_logs, on_conflict_do_nothing=True
                )
        else:
            if self.dry_run:
                logger.info("--- CHẾ ĐỘ DRY-RUN: Bỏ qua bước ghi Supabase ---")

        # 6. Đếm số bản ghi Supabase sau khi chuyển
        remote_counts_after = {}
        if not self.dry_run and self.sb:
            for tbl in ["visitor_sessions", "analytics_events", "visitor_access_logs"]:
                try:
                    c = self.sb.count_remote_rows(tbl)
                    remote_counts_after[tbl] = c
                except Exception:
                    remote_counts_after[tbl] = 0

        # 7. Báo cáo tổng kết
        self._print_summary(remote_counts_before, remote_counts_after)
        return True

    def _prepare_sessions(self, existing_session_ids: Set[str]) -> List[Dict[str, Any]]:
        """Thu thập sessions từ SQLite + tìm các client_id xuất hiện ở bảng khác để tạo session bổ sung."""
        raw_sessions = self.reader.fetch_all("visitor_sessions")
        sessions_map: Dict[str, Dict[str, Any]] = {}

        for r in raw_sessions:
            cid = str(r["client_id"]).strip()[:64]
            if not cid:
                continue
            first_ts = float(r.get("first_seen_ts") or 0.0)
            last_ts = float(r.get("last_seen_ts") or first_ts)
            if last_ts < first_ts:
                last_ts = first_ts

            sessions_map[cid] = {
                "client_id": cid,
                "started_at": ts_to_iso(first_ts),
                "last_seen_at": ts_to_iso(last_ts),
                "heartbeat_count": max(int(r.get("total_heartbeats") or 1), 0),
                "user_agent_short": str(r.get("user_agent_short") or "")[:100],
            }

        # Quét thêm client_id từ page_views & location_events & error_logs để tránh lỗi FK
        for tbl in ["page_views", "location_events", "error_logs"]:
            for row in self.reader.fetch_all(tbl):
                cid = row.get("client_id")
                if cid and str(cid).strip():
                    cid = str(cid).strip()[:64]
                    if cid not in sessions_map:
                        created_ts = float(row.get("created_ts") or 0.0)
                        sessions_map[cid] = {
                            "client_id": cid,
                            "started_at": ts_to_iso(created_ts),
                            "last_seen_at": ts_to_iso(created_ts),
                            "heartbeat_count": 1,
                            "user_agent_short": "legacy_sqlite_import",
                        }

        # Lọc bỏ các session đã có trên remote nếu có
        to_insert = []
        for cid, data in sessions_map.items():
            if cid in existing_session_ids:
                self.stats["visitor_sessions"]["skipped"] += 1
            else:
                to_insert.append(data)

        return to_insert

    def _prepare_page_views(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Ánh xạ page_views sang analytics_events (event_type='page_view') và visitor_access_logs."""
        raw = self.reader.fetch_all("page_views")
        already_migrated = set()
        if self.sb:
            already_migrated = self.sb.fetch_migrated_event_ids("page_views")

        events = []
        access_logs = []
        for r in raw:
            sid = str(r.get("id"))
            if sid in already_migrated:
                self.stats["page_views"]["skipped"] += 1
                continue

            cid = str(r.get("client_id") or "").strip()[:64] or "legacy_anonymous"
            raw_path = str(r.get("path") or "/app/").split("?")[0].split("#")[0][:120]
            created_ts = float(r.get("created_ts") or 0.0)
            iso_time = ts_to_iso(created_ts)

            events.append({
                "event_type": "page_view",
                "occurred_at": iso_time,
                "client_id": cid,
                "path": raw_path,
                "metadata": {"sqlite_source": "page_views", "sqlite_id": sid},
            })

            if self.include_access_logs:
                access_logs.append({
                    "client_id": cid,
                    "path": raw_path,
                    "user_agent_short": "legacy_import",
                    "created_at": iso_time,
                })

        return events, access_logs

    def _prepare_location_events(self, valid_location_ids: Set[int]) -> List[Dict[str, Any]]:
        """Ánh xạ location_events sang analytics_events (location_activated / location_detected)."""
        raw = self.reader.fetch_all("location_events")
        already_migrated = set()
        if self.sb:
            already_migrated = self.sb.fetch_migrated_event_ids("location_events")

        events = []
        for r in raw:
            sid = str(r.get("id"))
            if sid in already_migrated:
                self.stats["location_events"]["skipped"] += 1
                continue

            raw_type = str(r.get("event_type") or "activated")
            event_type = "location_activated" if raw_type in ("activated", "location_activated") else "location_detected"
            cid = str(r.get("client_id") or "").strip()[:64] or None

            loc_id = r.get("location_id")
            meta = {
                "sqlite_source": "location_events",
                "sqlite_id": sid,
            }
            if r.get("location_name"):
                meta["location_name"] = str(r["location_name"])[:200]

            # Kiểm tra FK location_id
            target_loc_id = None
            if loc_id is not None:
                try:
                    int_id = int(loc_id)
                    if not valid_location_ids or int_id in valid_location_ids:
                        target_loc_id = int_id
                    else:
                        meta["original_location_id"] = int_id
                except (ValueError, TypeError):
                    pass

            lang = str(r.get("language") or "")[:10] or None
            dist = r.get("distance_m")
            distance_m = round(float(dist), 1) if dist is not None else None

            events.append({
                "event_type": event_type,
                "occurred_at": ts_to_iso(r.get("created_ts")),
                "client_id": cid,
                "location_id": target_loc_id,
                "language": lang,
                "distance_m": distance_m,
                "metadata": meta,
            })

        return events

    def _prepare_narration_events(self, valid_location_ids: Set[int]) -> List[Dict[str, Any]]:
        """Ánh xạ narration_events sang narration_success / narration_failed / narration_requested."""
        raw = self.reader.fetch_all("narration_events")
        already_migrated = set()
        if self.sb:
            already_migrated = self.sb.fetch_migrated_event_ids("narration_events")

        events = []
        for r in raw:
            sid = str(r.get("id"))
            if sid in already_migrated:
                self.stats["narration_events"]["skipped"] += 1
                continue

            raw_type = str(r.get("event_type") or "")
            if raw_type in ("tts_success", "narration_success"):
                event_type = "narration_success"
            elif raw_type in ("tts_fail", "narration_failed"):
                event_type = "narration_failed"
            else:
                event_type = "narration_requested"

            loc_id = r.get("location_id")
            meta = {
                "sqlite_source": "narration_events",
                "sqlite_id": sid,
            }

            target_loc_id = None
            if loc_id is not None:
                try:
                    int_id = int(loc_id)
                    if not valid_location_ids or int_id in valid_location_ids:
                        target_loc_id = int_id
                    else:
                        meta["original_location_id"] = int_id
                except (ValueError, TypeError):
                    pass

            dur = r.get("duration_ms")
            duration_ms = round(float(dur), 1) if dur is not None and float(dur) >= 0 else None
            is_fallback = bool(r.get("is_fallback"))
            status_reason = str(r.get("status_reason") or "")[:150] or None

            events.append({
                "event_type": event_type,
                "occurred_at": ts_to_iso(r.get("created_ts")),
                "location_id": target_loc_id,
                "language": str(r.get("language") or "")[:10] or None,
                "duration_ms": duration_ms,
                "is_fallback": is_fallback,
                "reason": status_reason,
                "metadata": meta,
            })

        return events

    def _prepare_error_logs(self) -> List[Dict[str, Any]]:
        """Ánh xạ error_logs sang analytics_events (event_type='error', đã làm sạch)."""
        raw = self.reader.fetch_all("error_logs")
        already_migrated = set()
        if self.sb:
            already_migrated = self.sb.fetch_migrated_event_ids("error_logs")

        events = []
        for r in raw:
            sid = str(r.get("id"))
            if sid in already_migrated:
                self.stats["error_logs"]["skipped"] += 1
                continue

            raw_msg = sanitize_text(str(r.get("message") or "Unknown error"))[:500]
            status_code = r.get("status_code")
            try:
                sc = int(status_code) if status_code is not None else None
                if sc is not None and not (100 <= sc <= 599):
                    sc = None
            except Exception:
                sc = None

            events.append({
                "event_type": "error",
                "occurred_at": ts_to_iso(r.get("created_ts")),
                "service": str(r.get("service") or "unknown")[:32],
                "request_type": str(r.get("request_type") or "unknown")[:64],
                "status_code": sc,
                "error_type": str(r.get("error_type") or "")[:64] or None,
                "message": raw_msg,
                "metadata": {
                    "sqlite_source": "error_logs",
                    "sqlite_id": sid,
                },
            })

        return events

    def _insert_in_batches(self, table_name: str, rows: List[Dict[str, Any]], on_conflict_do_nothing: bool = False) -> int:
        """Chèn danh sách bản ghi theo từng lô có kích thước batch_size."""
        if not rows or not self.sb:
            return 0

        total_inserted = 0
        total_batches = (len(rows) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]
            batch_num = (i // self.batch_size) + 1
            try:
                cnt = self.sb.insert_batch(table_name, batch, on_conflict_do_nothing=on_conflict_do_nothing)
                total_inserted += cnt
                logger.info(
                    "Đã chèn lô %d/%d vào [%s] (%d bản ghi)",
                    batch_num,
                    total_batches,
                    table_name,
                    cnt,
                )
            except Exception as exc:
                logger.error("Lỗi khi chèn lô %d vào [%s]: %s", batch_num, table_name, exc)
                raise

        return total_inserted

    def _print_summary(self, remote_before: Dict[str, int], remote_after: Dict[str, int]) -> None:
        """In bảng báo cáo đối chiếu số lượng bản ghi và chênh lệch."""
        logger.info("")
        logger.info("=" * 80)
        logger.info("BÁO CÁO KẾT QUẢ CHUYỂN ĐỔI DỮ LIỆU SQLITE SANG SUPABASE")
        logger.info("=" * 80)
        header = f"{'Thực thể / Bảng':<25} | {'SQLite cũ':<10} | {'Cần chèn':<10} | {'Đã chèn':<10} | {'Bỏ qua':<10} | {'Trạng thái'}"
        logger.info(header)
        logger.info("-" * 80)

        for k, v in self.stats.items():
            status = "DRY-RUN OK" if self.dry_run else ("HOÀN TẤT" if v["inserted"] == v["to_insert"] else "CẦN KIỂM TRA")
            line = (
                f"{k:<25} | {v['sqlite_total']:<10} | {v['to_insert']:<10} | "
                f"{v['inserted']:<10} | {v['skipped']:<10} | {status}"
            )
            logger.info(line)

        logger.info("-" * 80)
        if not self.dry_run and self.sb:
            logger.info("ĐỐI CHIẾU SỐ LƯỢNG TRÊN SUPABASE (TRƯỚC VÀ SAU):")
            for tbl in ["visitor_sessions", "analytics_events", "visitor_access_logs"]:
                b = remote_before.get(tbl, 0)
                a = remote_after.get(tbl, 0)
                delta = a - b
                logger.info("  * Bảng %-22s: Trước = %d, Sau = %d (Tăng +%d)", tbl, b, a, delta)
        logger.info("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Chuyển dữ liệu lịch sử từ SQLite (analytics.db) sang Supabase PostgreSQL."
    )
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("ANALYTICS_DB_PATH", "gateway-service/analytics.db"),
        help="Đường dẫn file SQLite nguồn (mặc định: gateway-service/analytics.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chạy thử nghiệm: kiểm tra dữ liệu, đếm số bản ghi và báo cáo mà không ghi vào DB.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Số bản ghi mỗi lô khi chèn vào Supabase (mặc định: 100).",
    )
    parser.add_argument(
        "--no-access-logs",
        action="store_true",
        help="Không đồng bộ page views vào visitor_access_logs (mặc định: có đồng bộ).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Đường dẫn file .env cấu hình Supabase (mặc định: .env)",
    )

    args = parser.parse_args()

    # Load biến môi trường
    env_path = Path(args.env_file)
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logger.info("Đã nạp biến môi trường từ: %s", env_path.resolve())

    # Khởi tạo SQLite Reader
    try:
        reader = SQLiteReader(args.sqlite_path)
        logger.info("Đã kết nối thành công file SQLite: %s", reader.db_path)
    except Exception as exc:
        logger.error("Không thể đọc file SQLite: %s", exc)
        sys.exit(1)

    # Khởi tạo Supabase Client
    sb_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    sb_key = os.getenv("SUPABASE_SECRET_KEY", "")
    db_url = os.getenv("DATABASE_URL", "")

    sb_client = None
    has_valid_remote = bool(
        (sb_url and sb_key and not sb_url.startswith("https://YOUR_PROJECT") and not sb_key.startswith("YOUR_"))
        or (db_url and "YOUR" not in db_url and "PASSWORD" not in db_url)
    )

    if has_valid_remote:
        try:
            sb_client = SupabaseClient(url=sb_url, key=sb_key, database_url=db_url)
        except Exception as exc:
            logger.warning("Không thể khởi tạo kết nối Supabase: %s", exc)
    else:
        if args.dry_run:
            logger.info("Supabase chưa được cấu hình hoặc dùng giá trị mẫu; dry-run sẽ chạy trên dữ liệu SQLite cục bộ.")
        else:
            logger.error(
                "LỖI: Chưa cấu hình thông tin kết nối Supabase (SUPABASE_URL + SUPABASE_SECRET_KEY hoặc DATABASE_URL) trong .env!\n"
                "Hãy thiết lập thông tin Supabase hoặc dùng cờ --dry-run để chạy thử nghiệm."
            )
            sys.exit(1)

    migrator = SQLiteMigrator(
        sqlite_reader=reader,
        supabase_client=sb_client,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        include_access_logs=not args.no_access_logs,
    )

    success = migrator.run()
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
