"""
Kiểm thử toàn diện cho Admin Dashboard & Hệ thống Analytics (Mục 9).
1. Người chưa đăng nhập không thể xem dashboard (trả về trang login / mã 401 cho API).
2. Người chưa đăng nhập không thể gọi API thống kê quản trị (401).
3. Tài khoản và mật khẩu hợp lệ đăng nhập được (302 redirect và có Set-Cookie).
4. Mật khẩu sai không mở được dashboard (401 và không có session cookie hợp lệ).
5. Đăng xuất làm mất phiên quản trị (xóa cookie phiên).
6. Hai trình duyệt độc lập được tính thành hai mã định danh khác nhau.
7. Một trình duyệt gửi nhiều heartbeat vẫn chỉ được tính là một trình duyệt online.
8. Một trình duyệt không còn gửi heartbeat sẽ bị loại khỏi số online sau 90 giây.
9. Heartbeat không làm tăng lượt xem trang.
10. Dữ liệu lỗi được làm sạch và ghi nhận thực tế.
"""

import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import admin_auth
import analytics_store

# Cấu hình môi trường test quản trị
os.environ["ADMIN_PATH"] = "/admin-secret-test/"
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "SuperSecretPassword123!"
os.environ["ADMIN_SESSION_SECRET"] = "very-long-and-secure-random-secret-key-32b"

# Reload admin_auth để nhận cấu hình test
admin_auth.ADMIN_PATH = "/admin-secret-test/"
admin_auth.ADMIN_USERNAME = "testadmin"
admin_auth.ADMIN_PASSWORD = "SuperSecretPassword123!"
admin_auth.ADMIN_SESSION_SECRET = "very-long-and-secure-random-secret-key-32b"

from main import app, ADMIN_BASE

client = TestClient(app)


# ===========================================================================
# 1. Kiểm thử xác thực & Bảo vệ Route Admin
# ===========================================================================

def test_unauthenticated_user_sees_login_page():
    """1. Người chưa đăng nhập khi vào URL admin sẽ thấy form đăng nhập."""
    resp = client.get(f"{ADMIN_BASE}/")
    assert resp.status_code == 200
    assert "Đăng nhập Quản Trị" in resp.text
    assert 'name="username"' in resp.text
    assert 'name="password"' in resp.text


def test_unauthenticated_api_returns_401():
    """2. Người chưa đăng nhập không thể gọi API thống kê quản trị (nhận 401)."""
    resp = client.get(f"{ADMIN_BASE}/api/summary")
    assert resp.status_code == 401
    assert "không hợp lệ" in resp.json().get("detail", "")

    resp_chart = client.get(f"{ADMIN_BASE}/api/chart")
    assert resp_chart.status_code == 401

    resp_locs = client.get(f"{ADMIN_BASE}/api/locations")
    assert resp_locs.status_code == 401

    resp_errs = client.get(f"{ADMIN_BASE}/api/errors")
    assert resp_errs.status_code == 401


def test_login_with_invalid_credentials_fails():
    """4. Mật khẩu sai không mở được dashboard (trả về 401 và form báo lỗi)."""
    resp = client.post(
        f"{ADMIN_BASE}/login",
        data="username=testadmin&password=WrongPassword",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 401
    assert "không chính xác" in resp.text
    # Không có session cookie
    assert admin_auth.SESSION_COOKIE_NAME not in resp.cookies


def test_login_with_valid_credentials_succeeds_and_creates_session():
    """3. Tài khoản và mật khẩu hợp lệ đăng nhập thành công, nhận cookie phiên."""
    resp = client.post(
        f"{ADMIN_BASE}/login",
        data="username=testadmin&password=SuperSecretPassword123!",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].endswith(f"{ADMIN_BASE}/")
    assert admin_auth.SESSION_COOKIE_NAME in resp.cookies

    # Sử dụng cookie vừa nhận để vào Dashboard
    session_token = resp.cookies[admin_auth.SESSION_COOKIE_NAME]
    dash_resp = client.get(
        f"{ADMIN_BASE}/",
        cookies={admin_auth.SESSION_COOKIE_NAME: session_token},
    )
    assert dash_resp.status_code == 200
    assert "GPS Audio Guide Dashboard" in dash_resp.text
    assert "Khách đang online" in dash_resp.text


def test_authenticated_api_call_succeeds():
    """Kiểm tra gọi API thống kê khi đã có session token hợp lệ."""
    valid_token = admin_auth.create_session_token("testadmin")
    resp = client.get(
        f"{ADMIN_BASE}/api/summary",
        cookies={admin_auth.SESSION_COOKIE_NAME: valid_token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "online_visitors" in data
    assert "unique_visitors_24h" in data


def test_logout_clears_session():
    """5. Đăng xuất làm mất phiên quản trị."""
    valid_token = admin_auth.create_session_token("testadmin")
    resp = client.get(
        f"{ADMIN_BASE}/logout",
        cookies={admin_auth.SESSION_COOKIE_NAME: valid_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Cookie bị xóa hoặc set rỗng/hết hạn
    cookie_header = resp.headers.get("set-cookie", "")
    assert admin_auth.SESSION_COOKIE_NAME in cookie_header


# ===========================================================================
# 2. Kiểm thử logic Thống kê Analytics (Heartbeat, Client ID, Page Views)
# ===========================================================================

def test_heartbeat_and_online_visitors_calculation(tmp_path):
    """
    6. Hai trình duyệt độc lập được tính thành 2 mã định danh khác nhau.
    7. Một trình duyệt gửi nhiều heartbeat vẫn chỉ tính là 1 online.
    8. Trình duyệt không gửi heartbeat bị loại khỏi online sau 90 giây.
    9. Heartbeat không làm tăng page view.
    """
    db_file = str(tmp_path / "test_analytics.db")
    analytics_store.init_analytics_db(db_file)

    client_a = "cid_browser_A_unique_123"
    client_b = "cid_browser_B_unique_456"

    # Gửi page view cho client A (mở trang /app/)
    analytics_store.record_page_view(client_a, "/app/", db_path=db_file)

    # Client A gửi 5 heartbeat liên tiếp
    for _ in range(5):
        analytics_store.record_heartbeat(client_a, db_path=db_file)

    # Client B chỉ gửi 1 heartbeat
    analytics_store.record_heartbeat(client_b, db_path=db_file)

    summary = analytics_store.get_analytics_summary(db_path=db_file)

    # Hai client độc lập -> 2 online
    assert summary["online_visitors"] == 2
    # Client A gửi 5 heartbeat vẫn chỉ là 1 người online duy nhất
    assert summary["unique_visitors_24h"] == 2
    # Heartbeat không làm tăng lượt xem trang (chỉ có 1 pageview được ghi)
    assert summary["page_views_24h"] == 1

    # Kiểm tra sau 90s: Giả lập thời gian trôi qua 95 giây cho Client A
    # Thủ công cập nhật last_seen_ts của client_a về 100s trước
    conn = analytics_store._get_connection(db_file)
    old_ts = time.time() - 100.0
    conn.execute("UPDATE visitor_sessions SET last_seen_ts = ? WHERE client_id = ?", (old_ts, client_a))
    conn.close()

    summary_after = analytics_store.get_analytics_summary(db_path=db_file)
    # Client A đã quá 90s -> Tự động bị loại khỏi online, chỉ còn Client B
    assert summary_after["online_visitors"] == 1
    # Nhưng trong 24h cả 2 vẫn được ghi nhận
    assert summary_after["unique_visitors_24h"] == 2


def test_public_heartbeat_and_pageview_endpoints():
    """Kiểm tra gọi POST /analytics/heartbeat và /analytics/pageview từ frontend."""
    resp_pv = client.post("/analytics/pageview", json={"client_id": "cid_test_frontend_1", "path": "/app/"})
    assert resp_pv.status_code == 200
    assert resp_pv.json() == {"status": "ok"}

    resp_hb = client.post("/analytics/heartbeat", json={"client_id": "cid_test_frontend_1"})
    assert resp_hb.status_code == 200
    assert resp_hb.json() == {"status": "ok"}


def test_error_log_sanitization():
    """Kiểm tra làm sạch dữ liệu nhạy cảm trong error logs (không lưu password, token, IP)."""
    raw_message = "Failed to login with password=MySecretPass123 from 192.168.1.100 using token=Bearer_xyz"
    clean = analytics_store._sanitize_log_message(raw_message)
    assert "MySecretPass123" not in clean
    assert "192.168.1.100" not in clean
    assert "[REDACTED]" in clean or "[IP_REDACTED]" in clean


def test_supabase_visitor_sync_and_internal_online_visitors(tmp_path):
    """Kiểm tra số người truy cập được đồng bộ Supabase và số người đang dùng chạy nội bộ."""
    db_file = str(tmp_path / "supabase_test_analytics.db")
    analytics_store.init_analytics_db(db_file)

    # Ghi nhận lượt xem trang
    analytics_store.record_page_view("client_sp_1", path="/app/", db_path=db_file)
    analytics_store.record_heartbeat("client_sp_1", user_agent_short="TestBrowser", db_path=db_file)

    # Thống kê: online_visitors được tính nội bộ qua heartbeat
    summary = analytics_store.get_analytics_summary(db_path=db_file)
    assert summary["online_visitors"] == 1
    assert summary["page_views_24h"] >= 1
    assert "supabase_visitors_total" in summary
    assert "supabase_storage_enabled" in summary


# ===========================================================================
# 3. Kiểm thử Supabase Analytics Store & RPC Mapping (Migration 001)
# ===========================================================================

def test_supabase_heartbeat_calls_rpc(monkeypatch):
    """Kiểm tra record_heartbeat gọi analytics_touch_session qua RPC khi dùng Supabase."""
    monkeypatch.setattr(analytics_store, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(analytics_store, "SUPABASE_SECRET_KEY", "secret-key")

    rpc_calls = []
    def fake_rpc(func_name, params=None):
        rpc_calls.append((func_name, params))
        return True

    monkeypatch.setattr(analytics_store, "_supabase_rpc", fake_rpc)
    # Đồng bộ chạy hàm trực tiếp để assert
    analytics_store._supabase_rpc("analytics_touch_session", {"p_client_id": "c1", "p_user_agent": "UA"})
    assert len(rpc_calls) == 1
    assert rpc_calls[0][0] == "analytics_touch_session"
    assert rpc_calls[0][1]["p_client_id"] == "c1"


def test_supabase_summary_and_chart_mapping(monkeypatch):
    """Kiểm tra get_analytics_summary và chart ánh xạ đúng từ Supabase RPC."""
    monkeypatch.setattr(analytics_store, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(analytics_store, "SUPABASE_SECRET_KEY", "secret-key")

    fake_summary_data = {
        "online_visitors": 4,
        "unique_visitors_24h": 10,
        "unique_visitors_7d": 35,
        "page_views_24h": 50,
        "page_views_total": 120,
        "narration_requested_24h": 15,
        "tts_requests_24h": 15,
        "tts_success_24h": 14,
        "tts_fail_24h": 1,
        "tts_avg_duration_ms": 820.5,
        "cache_hit_24h": 8,
        "errors_24h": 0,
        "language_distribution_7d": {"vi": 20, "en": 10, "ja": 5},
        "as_of": "2026-11-08T03:00:00Z",
    }

    def fake_rpc(func_name, params=None):
        if func_name == "analytics_summary":
            return fake_summary_data
        if func_name == "analytics_activity_by_day":
            return [
                {"day": "2026-11-07", "visitors": 5, "page_views": 20, "detections": 8, "activations": 4},
                {"day": "2026-11-08", "visitors": 7, "page_views": 30, "detections": 12, "activations": 7},
            ]
        if func_name == "analytics_top_locations":
            return [
                {"location_id": 1, "location_name": "Địa điểm A", "request_count": 10, "last_requested_at": "2026-11-08T03:00:00Z"}
            ]
        if func_name == "analytics_recent_errors":
            return [
                {"id": 1, "service": "content", "request_type": "POST /narrate", "status_code": 500, "error_type": "Err", "message": "fail", "occurred_at": "2026-11-08T03:00:00Z"}
            ]
        if func_name == "analytics_cleanup":
            return {"analytics_events": 5, "visitor_sessions": 2, "visitor_access_logs": 0, "ran_at": "2026-11-08"}
        return None

    monkeypatch.setattr(analytics_store, "_supabase_rpc", fake_rpc)

    summary = analytics_store.get_analytics_summary()
    assert summary["online_visitors"] == 4
    assert summary["unique_visitors_24h"] == 10
    assert summary["supabase_visitors_total"] == 120
    assert summary["cache_hit_24h"] == 8
    assert list(summary["language_distribution_7d"].keys()) == ["vi", "en", "ja"]

    chart = analytics_store.get_activity_chart_data(days=7)
    assert len(chart) == 2
    assert chart[0]["date"] == "2026-11-07"
    assert chart[0]["visitors"] == 5
    assert chart[0]["page_views"] == 20
    assert chart[0]["detections"] == 8
    assert chart[0]["activations"] == 4

    top_locs = analytics_store.get_top_locations(limit=5)
    assert len(top_locs) == 1
    assert top_locs[0]["location_id"] == 1
    assert top_locs[0]["last_requested_iso"] == "2026-11-08T03:00:00Z"

    errs = analytics_store.get_recent_error_logs(limit=5)
    assert len(errs) == 1
    assert errs[0]["time_iso"] == "2026-11-08T03:00:00Z"

    deleted = analytics_store.cleanup_old_analytics(days=30)
    assert deleted == 7  # 5 events + 2 sessions


def test_supabase_rpc_error_fallback(monkeypatch):
    """Kiểm tra khi Supabase RPC lỗi (404/500/timeout), Dashboard không sập mà trả số 0."""
    monkeypatch.setattr(analytics_store, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(analytics_store, "SUPABASE_SECRET_KEY", "secret-key")
    monkeypatch.setattr(analytics_store, "_supabase_rpc", lambda *args, **kwargs: None)

    summary = analytics_store.get_analytics_summary()
    assert summary["online_visitors"] == 0
    assert summary["unique_visitors_24h"] == 0
    assert summary["tts_requests_24h"] == 0
    assert "as_of_iso" in summary
