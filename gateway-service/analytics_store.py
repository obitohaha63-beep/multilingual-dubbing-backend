"""
Analytics Store & Database Manager cho Admin Dashboard & Client Tracking.
Lưu trữ trên Supabase PostgreSQL (theo thiết kế DFD mức 1, migration 001):
1. visitor_sessions: mỗi trình duyệt ẩn danh 1 dòng, tính online qua cửa sổ 90s.
2. analytics_events: hợp nhất các sự kiện theo event_type:
   'page_view', 'location_detected', 'location_activated',
   'narration_requested', 'narration_success', 'narration_failed', 'error'
3. visitor_access_logs (D9): nguồn duy nhất cho số page view vĩnh viễn,
   liên kết client_id sang visitor_sessions (trigger DB tự động cập nhật session).
   Tránh ghi trùng page_view vào analytics_events.

Chính sách bảo mật & dữ liệu:
- Không lưu IP người dùng.
- Không lưu lịch sử tọa độ GPS chính xác (chỉ khoảng cách distance_m quanh POI).
- Không lưu mật khẩu, token, API key (làm sạch tại Python + trigger DB).
- Tự động dọn dữ liệu cũ hơn 30 ngày (analytics_cleanup).
- Hỗ trợ fallback sang SQLite cục bộ khi chạy unit test hoặc không có cấu hình Supabase.
- Ghi analytics là non-blocking và an toàn tuyệt đối (không làm chậm hay lỗi luồng nghiệp vụ).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("uvicorn.error.gateway.analytics")

# Cấu hình Supabase từ biến môi trường
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Xác định đường dẫn file database analytics SQLite (dùng cho local/test fallback)
_DEFAULT_DB_PATH = os.getenv("ANALYTICS_DB_PATH")
if not _DEFAULT_DB_PATH:
    _CONTENT_DB = os.getenv("DB_PATH", "")
    if _CONTENT_DB and os.path.isabs(_CONTENT_DB):
        _DEFAULT_DB_PATH = str(Path(_CONTENT_DB).parent / "analytics.db")
    else:
        _DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "analytics.db")

ANALYTICS_DB_PATH = _DEFAULT_DB_PATH

# Thread pool thực thi ghi analytics nền non-blocking
_bg_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="analytics_bg")


def _use_supabase(db_path: Optional[str] = None) -> bool:
    """Chỉ dùng Supabase khi có URL + Key và không bị ép dùng db_path cụ thể (như trong unit test)."""
    if db_path is not None:
        return False
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _supabase_headers(prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _supabase_post(url: str, json_data: dict, prefer: Optional[str] = None) -> Optional[httpx.Response]:
    """Gửi HTTP POST an toàn tới Supabase với timeout ngắn 3s."""
    try:
        headers = _supabase_headers(prefer=prefer)
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(url, headers=headers, json=json_data)
            return resp
    except Exception as exc:
        logger.warning("Lỗi gửi request Supabase (%s): %s", url, exc)
        return None


def _supabase_rpc(func_name: str, params: Optional[dict] = None) -> Optional[Any]:
    """Gọi PostgreSQL stored function qua PostgREST RPC endpoint."""
    if not (SUPABASE_URL and SUPABASE_SECRET_KEY):
        return None
    url = f"{SUPABASE_URL}/rest/v1/rpc/{func_name}"
    resp = _supabase_post(url, params or {})
    if resp is not None and resp.is_success:
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _bg_task(func, *args, **kwargs) -> None:
    """Đẩy tác vụ ghi vào thread pool chạy nền, không block request và không throw lỗi ra ngoài."""
    def _runner():
        try:
            func(*args, **kwargs)
        except Exception as exc:
            logger.warning("Tác vụ analytics nền gặp lỗi: %s", exc)

    try:
        _bg_executor.submit(_runner)
    except Exception as exc:
        logger.warning("Không thể đưa tác vụ analytics vào pool: %s", exc)


def _sync_visit_to_supabase(client_id: str, path: str = "/app/", user_agent: str = "") -> None:
    """
    Đồng bộ số lượt truy cập người dùng lên Supabase visitor_access_logs.
    Trigger trg_visitor_access_logs_before_insert tự động cập nhật visitor_sessions.
    """
    if not client_id:
        return

    # Cách 1: PostgREST API
    if SUPABASE_URL and SUPABASE_SECRET_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/visitor_access_logs"
            payload = {
                "client_id": client_id[:64],
                "path": path[:120],
                "user_agent_short": user_agent[:120] if user_agent else "",
            }
            _supabase_post(url, payload, prefer="return=minimal")
            return
        except Exception:
            pass

    # Cách 2: PostgreSQL trực tiếp qua DATABASE_URL nếu có driver
    if DATABASE_URL:
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(DATABASE_URL, pool_pre_ping=True)
            with engine.connect() as pg_conn:
                pg_conn.execute(
                    text("INSERT INTO public.visitor_access_logs (client_id, path, user_agent_short) VALUES (:cid, :p, :ua)"),
                    {"cid": client_id[:64], "p": path[:120], "ua": user_agent[:120] if user_agent else ""}
                )
                pg_conn.commit()
        except Exception:
            pass


def _fetch_supabase_visitor_count() -> Optional[int]:
    """Lấy tổng số lượt truy cập được lưu trữ trên Supabase."""
    if SUPABASE_URL and SUPABASE_SECRET_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/visitor_access_logs?select=count"
            headers = {
                "apikey": SUPABASE_SECRET_KEY,
                "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
                "Range-Unit": "items",
                "Range": "0-0",
                "Prefer": "count=exact",
            }
            with httpx.Client(timeout=3.0) as client:
                resp = client.head(url, headers=headers)
                cr = resp.headers.get("content-range")
                if cr and "/" in cr:
                    total_str = cr.split("/")[-1]
                    if total_str.isdigit():
                        return int(total_str)
        except Exception:
            pass
    return None


# ===========================================================================
# SQLite Helpers (Dùng khi chạy offline/unit tests)
# ===========================================================================

def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or ANALYTICS_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_analytics_db(db_path: Optional[str] = None) -> None:
    """Khởi tạo bảng analytics SQLite nếu chưa có."""
    if _use_supabase(db_path):
        logger.info("Gateway Analytics đang sử dụng Supabase PostgreSQL.")
        return

    conn = _get_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visitor_sessions (
                client_id TEXT PRIMARY KEY,
                first_seen_ts REAL NOT NULL,
                last_seen_ts REAL NOT NULL,
                total_heartbeats INTEGER NOT NULL DEFAULT 1,
                user_agent_short TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON visitor_sessions(last_seen_ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                path TEXT NOT NULL,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS location_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT,
                location_id INTEGER,
                location_name TEXT,
                language TEXT,
                event_type TEXT NOT NULL,
                distance_m REAL,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loc_events_created ON location_events(created_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loc_events_loc_id ON location_events(location_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS narration_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER,
                language TEXT,
                event_type TEXT NOT NULL,
                duration_ms REAL,
                is_fallback INTEGER DEFAULT 0,
                status_reason TEXT,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_narration_events_created ON narration_events(created_ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                request_type TEXT NOT NULL,
                status_code INTEGER,
                error_type TEXT,
                message TEXT NOT NULL,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_error_logs_created ON error_logs(created_ts)")
    finally:
        conn.close()


# ===========================================================================
# Ghi nhận sự kiện (Record Functions)
# ===========================================================================

def record_heartbeat(client_id: str, user_agent_short: str = "", db_path: Optional[str] = None) -> None:
    """Ghi nhận hoặc cập nhật nhịp tim của client."""
    if not client_id or len(client_id) > 64:
        return

    if _use_supabase(db_path):
        def _sb_heartbeat():
            _supabase_rpc("analytics_touch_session", {
                "p_client_id": client_id[:64],
                "p_user_agent": user_agent_short[:100] if user_agent_short else None,
            })
        _bg_task(_sb_heartbeat)
        return

    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO visitor_sessions (client_id, first_seen_ts, last_seen_ts, total_heartbeats, user_agent_short)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                last_seen_ts = excluded.last_seen_ts,
                total_heartbeats = visitor_sessions.total_heartbeats + 1
        """, (client_id, now, now, user_agent_short[:100]))
    finally:
        conn.close()


def record_page_view(client_id: str, path: str = "/app/", user_agent: str = "", db_path: Optional[str] = None) -> None:
    """
    Ghi nhận lượt xem trang HTML duy nhất (D9: ghi vào visitor_access_logs).
    Trigger DB tự động liên kết và cập nhật visitor_sessions. Không ghi trùng analytics_events.
    """
    if not client_id or len(client_id) > 64:
        return

    if _use_supabase(db_path):
        _bg_task(_sync_visit_to_supabase, client_id, path, user_agent)
        return

    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO page_views (client_id, path, created_ts)
            VALUES (?, ?, ?)
        """, (client_id, path[:120], now))
        conn.execute("""
            INSERT INTO visitor_sessions (client_id, first_seen_ts, last_seen_ts, total_heartbeats, user_agent_short)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                last_seen_ts = excluded.last_seen_ts
        """, (client_id, now, now, user_agent[:100] if user_agent else ''))
    finally:
        conn.close()

    _bg_task(_sync_visit_to_supabase, client_id, path, user_agent)


def record_location_event(
    location_id: Optional[int],
    location_name: Optional[str],
    language: str,
    event_type: str,
    client_id: Optional[str] = None,
    distance_m: Optional[float] = None,
    db_path: Optional[str] = None,
) -> None:
    """Ghi nhận sự kiện phát hiện địa điểm hoặc kích hoạt thuyết minh."""
    # Ánh xạ sang event_type chuẩn schema
    mapped_type = "location_activated" if event_type in ("activated", "location_activated") else "location_detected"

    if _use_supabase(db_path):
        def _sb_loc():
            payload = {
                "event_type": mapped_type,
                "client_id": client_id[:64] if client_id else None,
                "location_id": int(location_id) if location_id is not None else None,
                "language": language[:10] if language else None,
                "distance_m": round(float(distance_m), 1) if distance_m is not None else None,
                "metadata": {"location_name": location_name[:200]} if location_name else {},
            }
            _supabase_post(f"{SUPABASE_URL}/rest/v1/analytics_events", payload, prefer="return=minimal")
        _bg_task(_sb_loc)
        return

    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO location_events (client_id, location_id, location_name, language, event_type, distance_m, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            (client_id[:64] if client_id else None),
            location_id,
            (location_name[:200] if location_name else None),
            (language[:10] if language else None),
            event_type[:32],
            distance_m,
            now,
        ))
    finally:
        conn.close()


def record_narration_event(
    location_id: Optional[int],
    language: str,
    event_type: str,
    duration_ms: Optional[float] = None,
    is_fallback: bool = False,
    status_reason: Optional[str] = None,
    db_path: Optional[str] = None,
    client_id: Optional[str] = None,
    cache_hit: Optional[bool] = None,
) -> None:
    """Ghi nhận sự kiện xử lý nội dung & tạo âm thanh."""
    if event_type in ("tts_success", "narration_success"):
        mapped_type = "narration_success"
    elif event_type in ("tts_fail", "narration_failed"):
        mapped_type = "narration_failed"
    elif event_type in ("tts_requested", "narration_requested"):
        mapped_type = "narration_requested"
    else:
        mapped_type = "narration_requested"

    if _use_supabase(db_path):
        def _sb_narr():
            payload = {
                "event_type": mapped_type,
                "client_id": client_id[:64] if client_id else None,
                "location_id": int(location_id) if location_id is not None else None,
                "language": language[:10] if language else None,
                "duration_ms": round(float(duration_ms), 1) if duration_ms is not None else None,
                "is_fallback": bool(is_fallback),
                "cache_hit": bool(cache_hit) if cache_hit is not None else None,
                "reason": status_reason[:150] if status_reason else None,
            }
            _supabase_post(f"{SUPABASE_URL}/rest/v1/analytics_events", payload, prefer="return=minimal")
        _bg_task(_sb_narr)
        return

    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO narration_events (location_id, language, event_type, duration_ms, is_fallback, status_reason, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            location_id,
            (language[:10] if language else None),
            event_type[:32],
            duration_ms,
            1 if is_fallback else 0,
            (status_reason[:150] if status_reason else None),
            now,
        ))
    finally:
        conn.close()


def record_error_log(
    service: str,
    request_type: str,
    message: str,
    status_code: Optional[int] = None,
    error_type: Optional[str] = None,
    db_path: Optional[str] = None,
    client_id: Optional[str] = None,
) -> None:
    """Ghi nhận log lỗi backend/gateway đã làm sạch nhạy cảm."""
    clean_msg = _sanitize_log_message(message)

    if _use_supabase(db_path):
        def _sb_err():
            payload = {
                "event_type": "error",
                "client_id": client_id[:64] if client_id else None,
                "service": (service or "unknown")[:32],
                "request_type": (request_type or "unknown")[:64],
                "status_code": int(status_code) if status_code is not None else None,
                "error_type": error_type[:64] if error_type else None,
                "message": clean_msg[:500] if clean_msg else "Unknown error",
            }
            _supabase_post(f"{SUPABASE_URL}/rest/v1/analytics_events", payload, prefer="return=minimal")
        _bg_task(_sb_err)
        return

    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO error_logs (service, request_type, status_code, error_type, message, created_ts)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            service[:32],
            request_type[:64],
            status_code,
            (error_type[:64] if error_type else None),
            clean_msg[:500],
            now,
        ))
    finally:
        conn.close()


def _sanitize_log_message(msg: str) -> str:
    """Xóa IP, mật khẩu, authorization token khỏi chuỗi log."""
    if not msg:
        return ""
    # Thay thế chuỗi mật khẩu/token/secret
    msg = re.sub(r"(?i)(password|passwd|secret|token|authorization|cookie|session|api[_-]?key)[=:\s]+[^\s,;&]+", r"\1=[REDACTED]", msg)
    msg = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", msg)
    msg = re.sub(r"://[^/:\s@]+:[^@\s]+@", "://[REDACTED]@", msg)
    # Ẩn địa chỉ IPv4
    msg = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP_REDACTED]", msg)
    return msg


def cleanup_old_analytics(days: int = 30, db_path: Optional[str] = None) -> int:
    """Xóa dữ liệu cũ hơn N ngày để giữ dung lượng tối thiểu."""
    if _use_supabase(db_path):
        res = _supabase_rpc("analytics_cleanup", {
            "p_retention_days": max(int(days), 1),
            "p_access_log_days": None,
        })
        if isinstance(res, dict):
            return int(res.get("analytics_events", 0)) + int(res.get("visitor_sessions", 0))
        return 0

    cutoff_ts = time.time() - (days * 86400)
    conn = _get_connection(db_path)
    total_deleted = 0
    try:
        cur1 = conn.execute("DELETE FROM visitor_sessions WHERE last_seen_ts < ?", (cutoff_ts,))
        cur2 = conn.execute("DELETE FROM page_views WHERE created_ts < ?", (cutoff_ts,))
        cur3 = conn.execute("DELETE FROM location_events WHERE created_ts < ?", (cutoff_ts,))
        cur4 = conn.execute("DELETE FROM narration_events WHERE created_ts < ?", (cutoff_ts,))
        cur5 = conn.execute("DELETE FROM error_logs WHERE created_ts < ?", (cutoff_ts,))
        total_deleted = cur1.rowcount + cur2.rowcount + cur3.rowcount + cur4.rowcount + cur5.rowcount
    finally:
        conn.close()
    return total_deleted


# ===========================================================================
# Đọc số liệu Dashboard (Read Functions)
# ===========================================================================

def get_analytics_summary(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Tổng hợp chỉ số tổng quan cho Dashboard."""
    if _use_supabase(db_path):
        data = _supabase_rpc("analytics_summary", {"p_online_window_seconds": 90})
        if data and isinstance(data, dict):
            lang_distribution = data.get("language_distribution_7d") or {}
            if isinstance(lang_distribution, dict):
                sorted_lang = dict(sorted(lang_distribution.items(), key=lambda item: item[1], reverse=True))
            else:
                sorted_lang = {}

            return {
                "online_visitors": data.get("online_visitors", 0),
                "unique_visitors_24h": data.get("unique_visitors_24h", 0),
                "unique_visitors_7d": data.get("unique_visitors_7d", 0),
                "page_views_24h": data.get("page_views_24h", 0),
                "supabase_visitors_total": data.get("page_views_total"),
                "supabase_storage_enabled": True,
                "tts_requests_24h": data.get("tts_requests_24h", 0),
                "tts_success_24h": data.get("tts_success_24h", 0),
                "tts_fail_24h": data.get("tts_fail_24h", 0),
                "tts_avg_duration_ms": data.get("tts_avg_duration_ms"),
                "cache_hit_24h": data.get("cache_hit_24h", 0),
                "errors_24h": data.get("errors_24h", 0),
                "language_distribution_7d": sorted_lang,
                "as_of_iso": data.get("as_of") or datetime.now(timezone.utc).isoformat(),
            }
        logger.warning("Không thể lấy analytics_summary từ Supabase RPC, trả về cấu trúc mặc định.")
        return {
            "online_visitors": 0,
            "unique_visitors_24h": 0,
            "unique_visitors_7d": 0,
            "page_views_24h": 0,
            "supabase_visitors_total": _fetch_supabase_visitor_count(),
            "supabase_storage_enabled": True,
            "tts_requests_24h": 0,
            "tts_success_24h": 0,
            "tts_fail_24h": 0,
            "tts_avg_duration_ms": None,
            "cache_hit_24h": 0,
            "errors_24h": 0,
            "language_distribution_7d": {},
            "as_of_iso": datetime.now(timezone.utc).isoformat(),
        }

    now = time.time()
    online_threshold = now - 90.0        # 90s gần nhất = Đang online
    h24_threshold = now - 86400.0        # 24 giờ qua
    d7_threshold = now - (7 * 86400.0)   # 7 ngày qua

    conn = _get_connection(db_path)
    try:
        r_online = conn.execute(
            "SELECT COUNT(*) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (online_threshold,)
        ).fetchone()
        online_count = r_online["c"] if r_online else 0

        r_24h = conn.execute(
            "SELECT COUNT(DISTINCT client_id) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        visitors_24h = r_24h["c"] if r_24h else 0

        r_7d = conn.execute(
            "SELECT COUNT(DISTINCT client_id) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (d7_threshold,)
        ).fetchone()
        visitors_7d = r_7d["c"] if r_7d else 0

        r_views_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM page_views WHERE created_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        page_views_24h = r_views_24h["c"] if r_views_24h else 0

        r_tts = conn.execute("""
            SELECT 
                COUNT(*) AS total_tts,
                SUM(CASE WHEN event_type = 'tts_success' THEN 1 ELSE 0 END) AS tts_success,
                SUM(CASE WHEN event_type = 'tts_fail' THEN 1 ELSE 0 END) AS tts_fail,
                AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms ELSE NULL END) AS avg_duration_ms
            FROM narration_events
            WHERE created_ts >= ?
        """, (h24_threshold,)).fetchone()

        r_err = conn.execute(
            "SELECT COUNT(*) AS c FROM error_logs WHERE created_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        errors_24h = r_err["c"] if r_err else 0

        lang_rows = conn.execute("""
            SELECT language, COUNT(*) AS count
            FROM location_events
            WHERE created_ts >= ? AND language IS NOT NULL AND language != ''
            GROUP BY language
            ORDER BY count DESC
        """, (d7_threshold,)).fetchall()
        lang_distribution = {row["language"]: row["count"] for row in lang_rows}

        supabase_count = _fetch_supabase_visitor_count()
        has_supabase = bool(SUPABASE_URL and SUPABASE_SECRET_KEY) or bool(DATABASE_URL)

        return {
            "online_visitors": online_count,
            "unique_visitors_24h": visitors_24h,
            "unique_visitors_7d": visitors_7d,
            "page_views_24h": page_views_24h,
            "supabase_visitors_total": supabase_count,
            "supabase_storage_enabled": has_supabase,
            "tts_requests_24h": r_tts["total_tts"] if r_tts and r_tts["total_tts"] else 0,
            "tts_success_24h": r_tts["tts_success"] if r_tts and r_tts["tts_success"] else 0,
            "tts_fail_24h": r_tts["tts_fail"] if r_tts and r_tts["tts_fail"] else 0,
            "tts_avg_duration_ms": round(r_tts["avg_duration_ms"], 1) if r_tts and r_tts["avg_duration_ms"] else None,
            "cache_hit_24h": 0,
            "errors_24h": errors_24h,
            "language_distribution_7d": lang_distribution,
            "as_of_iso": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def get_activity_chart_data(days: int = 7, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Dữ liệu biểu đồ theo ngày (mặc định 7 ngày, hỗ trợ 1, 7, 30)."""
    if _use_supabase(db_path):
        rows = _supabase_rpc("analytics_activity_by_day", {"p_days": int(days), "p_tz": "UTC"})
        if rows and isinstance(rows, list):
            return [
                {
                    "date": str(r.get("day", "")),
                    "visitors": r.get("visitors", 0),
                    "page_views": r.get("page_views", 0),
                    "detections": r.get("detections", 0),
                    "activations": r.get("activations", 0),
                }
                for r in rows
            ]
        return []

    now = time.time()
    start_ts = now - (days * 86400.0)
    conn = _get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT 
                strftime('%Y-%m-%d', datetime(created_ts, 'unixepoch')) AS day,
                COUNT(DISTINCT client_id) AS visitors,
                COUNT(*) AS total_views
            FROM page_views
            WHERE created_ts >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (start_ts,)).fetchall()

        loc_rows = conn.execute("""
            SELECT 
                strftime('%Y-%m-%d', datetime(created_ts, 'unixepoch')) AS day,
                COUNT(*) AS detections
            FROM location_events
            WHERE created_ts >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (start_ts,)).fetchall()
        loc_dict = {r["day"]: r["detections"] for r in loc_rows}

        results = []
        for r in rows:
            day_str = r["day"]
            results.append({
                "date": day_str,
                "visitors": r["visitors"],
                "page_views": r["total_views"],
                "detections": loc_dict.get(day_str, 0),
            })
        return results
    finally:
        conn.close()


def get_top_locations(limit: int = 10, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Những địa điểm được tìm kiếm và kích hoạt thuyết minh nhiều nhất."""
    if _use_supabase(db_path):
        rows = _supabase_rpc("analytics_top_locations", {"p_limit": int(limit), "p_days": None})
        if rows and isinstance(rows, list):
            return [
                {
                    "location_id": r.get("location_id"),
                    "location_name": r.get("location_name") or f"ID #{r.get('location_id')}",
                    "request_count": r.get("request_count", 0),
                    "last_requested_iso": r.get("last_requested_at"),
                }
                for r in rows
            ]
        return []

    conn = _get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT 
                location_id,
                location_name,
                COUNT(*) AS request_count,
                MAX(created_ts) AS last_requested_ts
            FROM location_events
            WHERE location_id IS NOT NULL
            GROUP BY location_id, location_name
            ORDER BY request_count DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [
            {
                "location_id": r["location_id"],
                "location_name": r["location_name"] or f"ID #{r['location_id']}",
                "request_count": r["request_count"],
                "last_requested_iso": datetime.fromtimestamp(r["last_requested_ts"], tz=timezone.utc).isoformat() if r["last_requested_ts"] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_recent_error_logs(limit: int = 25, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Nhật ký lỗi gần đây (đã làm sạch)."""
    if _use_supabase(db_path):
        rows = _supabase_rpc("analytics_recent_errors", {"p_limit": int(limit)})
        if rows and isinstance(rows, list):
            return [
                {
                    "id": r.get("id"),
                    "service": r.get("service", ""),
                    "request_type": r.get("request_type", ""),
                    "status_code": r.get("status_code"),
                    "error_type": r.get("error_type"),
                    "message": r.get("message", ""),
                    "time_iso": r.get("occurred_at"),
                }
                for r in rows
            ]
        return []

    conn = _get_connection(db_path)
    try:
        rows = conn.execute("""
            SELECT id, service, request_type, status_code, error_type, message, created_ts
            FROM error_logs
            ORDER BY created_ts DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [
            {
                "id": r["id"],
                "service": r["service"],
                "request_type": r["request_type"],
                "status_code": r["status_code"],
                "error_type": r["error_type"],
                "message": r["message"],
                "time_iso": datetime.fromtimestamp(r["created_ts"], tz=timezone.utc).isoformat(),
            }
            for r in rows
        ]
    finally:
        conn.close()
