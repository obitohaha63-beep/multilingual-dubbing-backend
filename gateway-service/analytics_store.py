"""
Analytics Store & Database Manager cho Admin Dashboard & Client Tracking.
Lưu trữ:
1. Sessions / Visitor Heartbeats (ước tính khách đang online, 24h, 7d).
2. Page Views (số lượt xem trang, loại trừ heartbeat/health/polling).
3. Search & Detect Events (địa điểm được tìm kiếm/phát hiện qua GPS).
4. Audio & Narration Events (AI text generated/cached, TTS requested/succeeded/failed).
5. Error Logs (nhật ký lỗi backend/gateway đã làm sạch dữ liệu nhạy cảm).

Chính sách bảo mật & dữ liệu:
- Không lưu IP người dùng.
- Không lưu lịch sử tọa độ GPS chính xác.
- Tự động dọn dữ liệu cũ hơn 30 ngày.
- Database SQLite lưu tại DB_PATH hoặc ANALYTICS_DB_PATH.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Xác định đường dẫn file database analytics
# Nếu có DB_PATH (như trong Docker /app/data/content.db), ta dùng cùng thư mục /app/data/analytics.db
# Hoặc nếu chạy local: ./analytics.db hoặc content-service/analytics.db
_DEFAULT_DB_PATH = os.getenv("ANALYTICS_DB_PATH")
if not _DEFAULT_DB_PATH:
    _CONTENT_DB = os.getenv("DB_PATH", "")
    if _CONTENT_DB and os.path.isabs(_CONTENT_DB):
        _DEFAULT_DB_PATH = str(Path(_CONTENT_DB).parent / "analytics.db")
    else:
        _DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "analytics.db")

ANALYTICS_DB_PATH = _DEFAULT_DB_PATH


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or ANALYTICS_DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_analytics_db(db_path: Optional[str] = None) -> None:
    """Khởi tạo bảng analytics nếu chưa có."""
    conn = _get_connection(db_path)
    try:
        # Bảng 1: visitor_sessions (mã định danh ẩn danh, first_seen, last_seen, views_count)
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

        # Bảng 2: page_views (ghi nhận lượt mở trang web, không tính heartbeat/health)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS page_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                path TEXT NOT NULL,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_ts)")

        # Bảng 3: location_events (sự kiện phát hiện hoặc kích hoạt địa điểm)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS location_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT,
                location_id INTEGER,
                location_name TEXT,
                language TEXT,
                event_type TEXT NOT NULL, -- 'detected' | 'activated' | 'searched'
                distance_m REAL,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loc_events_created ON location_events(created_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loc_events_loc_id ON location_events(location_id)")

        # Bảng 4: narration_events (theo dõi yêu cầu thuyết minh & TTS)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS narration_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER,
                language TEXT,
                event_type TEXT NOT NULL, -- 'ai_content' | 'cache_hit' | 'tts_requested' | 'tts_success' | 'tts_fail'
                duration_ms REAL,
                is_fallback INTEGER DEFAULT 0,
                status_reason TEXT,
                created_ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_narration_events_created ON narration_events(created_ts)")

        # Bảng 5: error_logs (nhật ký lỗi backend/gateway đã loại bỏ nhạy cảm)
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


def record_heartbeat(client_id: str, user_agent_short: str = "", db_path: Optional[str] = None) -> None:
    """Ghi nhận hoặc cập nhật nhịp tim của client. Không thêm dòng mới mà chỉ UPDATE last_seen."""
    if not client_id or len(client_id) > 64:
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


def record_page_view(client_id: str, path: str = "/app/", db_path: Optional[str] = None) -> None:
    """Ghi nhận lượt xem trang HTML duy nhất (không ghi cho API polling / heartbeat)."""
    if not client_id or len(client_id) > 64:
        return
    now = time.time()
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO page_views (client_id, path, created_ts)
            VALUES (?, ?, ?)
        """, (client_id, path[:120], now))
        # Đồng thời cập nhật session
        conn.execute("""
            INSERT INTO visitor_sessions (client_id, first_seen_ts, last_seen_ts, total_heartbeats, user_agent_short)
            VALUES (?, ?, ?, 1, '')
            ON CONFLICT(client_id) DO UPDATE SET
                last_seen_ts = excluded.last_seen_ts
        """, (client_id, now, now))
    finally:
        conn.close()


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
) -> None:
    """Ghi nhận sự kiện xử lý nội dung & tạo âm thanh."""
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
) -> None:
    """Ghi nhận log lỗi backend/gateway đã làm sạch nhạy cảm."""
    now = time.time()
    clean_msg = _sanitize_log_message(message)
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
    import re
    # Thay thế chuỗi mật khẩu/token
    msg = re.sub(r"(?i)(password|secret|token|authorization|cookie|session)[=:\s]+[^\s,;&]+", r"\1=[REDACTED]", msg)
    # Ẩn địa chỉ IPv4
    msg = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP_REDACTED]", msg)
    return msg


def cleanup_old_analytics(days: int = 30, db_path: Optional[str] = None) -> int:
    """Xóa dữ liệu cũ hơn N ngày để giữ dung lượng tối thiểu."""
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


def get_analytics_summary(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Tổng hợp chỉ số tổng quan."""
    now = time.time()
    online_threshold = now - 90.0        # 90s gần nhất = Đang online
    h24_threshold = now - 86400.0        # 24 giờ qua
    d7_threshold = now - (7 * 86400.0)   # 7 ngày qua

    conn = _get_connection(db_path)
    try:
        # 1. Đang online
        r_online = conn.execute(
            "SELECT COUNT(*) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (online_threshold,)
        ).fetchone()
        online_count = r_online["c"] if r_online else 0

        # 2. Khách duy nhất 24h
        r_24h = conn.execute(
            "SELECT COUNT(DISTINCT client_id) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        visitors_24h = r_24h["c"] if r_24h else 0

        # 3. Khách duy nhất 7d
        r_7d = conn.execute(
            "SELECT COUNT(DISTINCT client_id) AS c FROM visitor_sessions WHERE last_seen_ts >= ?",
            (d7_threshold,)
        ).fetchone()
        visitors_7d = r_7d["c"] if r_7d else 0

        # 4. Lượt xem trang 24h
        r_views_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM page_views WHERE created_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        page_views_24h = r_views_24h["c"] if r_views_24h else 0

        # 5. Yêu cầu tạo âm thanh (TTS) trong 24h & tổng
        r_tts = conn.execute("""
            SELECT 
                COUNT(*) AS total_tts,
                SUM(CASE WHEN event_type = 'tts_success' THEN 1 ELSE 0 END) AS tts_success,
                SUM(CASE WHEN event_type = 'tts_fail' THEN 1 ELSE 0 END) AS tts_fail,
                AVG(CASE WHEN duration_ms IS NOT NULL THEN duration_ms ELSE NULL END) AS avg_duration_ms
            FROM narration_events
            WHERE created_ts >= ?
        """, (h24_threshold,)).fetchone()

        # 6. Lỗi trong 24h
        r_err = conn.execute(
            "SELECT COUNT(*) AS c FROM error_logs WHERE created_ts >= ?",
            (h24_threshold,)
        ).fetchone()
        errors_24h = r_err["c"] if r_err else 0

        # 7. Phân bổ ngôn ngữ 7d
        lang_rows = conn.execute("""
            SELECT language, COUNT(*) AS count
            FROM location_events
            WHERE created_ts >= ? AND language IS NOT NULL AND language != ''
            GROUP BY language
            ORDER BY count DESC
        """, (d7_threshold,)).fetchall()
        lang_distribution = {row["language"]: row["count"] for row in lang_rows}

        return {
            "online_visitors": online_count,
            "unique_visitors_24h": visitors_24h,
            "unique_visitors_7d": visitors_7d,
            "page_views_24h": page_views_24h,
            "tts_requests_24h": r_tts["total_tts"] if r_tts and r_tts["total_tts"] else 0,
            "tts_success_24h": r_tts["tts_success"] if r_tts and r_tts["tts_success"] else 0,
            "tts_fail_24h": r_tts["tts_fail"] if r_tts and r_tts["tts_fail"] else 0,
            "tts_avg_duration_ms": round(r_tts["avg_duration_ms"], 1) if r_tts and r_tts["avg_duration_ms"] else None,
            "errors_24h": errors_24h,
            "language_distribution_7d": lang_distribution,
            "as_of_iso": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def get_activity_chart_data(days: int = 7, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Dữ liệu biểu đồ theo ngày (mặc định 7 ngày, hỗ trợ 1, 7, 30)."""
    now = time.time()
    start_ts = now - (days * 86400.0)
    conn = _get_connection(db_path)
    try:
        # Gom nhóm theo ngày YYYY-MM-DD
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

        # Location events theo ngày
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
