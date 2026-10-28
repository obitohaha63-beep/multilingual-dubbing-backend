"""
Admin HTML & Dashboard View Renderer.
Cung cấp giao diện quản trị viên hoàn chỉnh:
- Đăng nhập (Login Form) với cơ chế bảo vệ, thông báo lỗi.
- Trang Tổng quan & Dashboard:
  * Thẻ KPI thống kê: Đang online (90s), Khách 24h, Khách 7 ngày, Lượt xem 24h, Tổng địa điểm, Bài thuyết minh đã cache, Yêu cầu âm thanh, Lỗi 24h.
  * Bộ lọc thời gian (24h, 7 ngày, 30 ngày) và biểu đồ SVG hoạt động tương tác nhẹ, không kéo thư viện nặng bên ngoài.
  * Danh sách Top địa điểm được tìm kiếm / kích hoạt nhiều nhất.
  * Quản lý địa điểm: Tìm kiếm, lọc theo nguồn (manual/wikimedia), xem GPS, bán kính, trạng thái cache âm thanh.
  * Theo dõi AI & TTS: Yêu cầu thuyết minh, tỷ lệ thành công/thất bại, thời gian phản hồi trung bình, phân bổ ngôn ngữ.
  * Trạng thái Microservices: Gateway, Content Service (8001), Translate Audio Service (8002) với health-check nội bộ và latency.
  * Nhật ký lỗi gần đây (đã làm sạch dữ liệu nhạy cảm).
  * Nút Đăng xuất an toàn.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional


def render_login_page(admin_path: str, error_message: str = "", username: str = "") -> str:
    """Giao diện đăng nhập dành riêng cho quản trị viên."""
    error_html = ""
    if error_message:
        error_html = f"""
        <div class="alert-error" role="alert">
            <span class="alert-icon">⚠️</span>
            <span>{html.escape(error_message)}</span>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Đăng nhập Quản Trị - GPS Audio Guide</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #38bdf8;
            --primary-hover: #0284c7;
            --danger: #ef4444;
            --radius: 12px;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: var(--font-family);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .login-card {{
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 32px 28px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
        }}
        .logo-badge {{
            display: inline-block;
            background: rgba(56, 189, 248, 0.15);
            color: var(--primary);
            border: 1px solid var(--primary);
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            padding: 4px 10px;
            border-radius: 999px;
            margin-bottom: 12px;
        }}
        h1 {{
            font-size: 1.4rem;
            font-weight: 700;
            margin-bottom: 6px;
        }}
        .subtitle {{
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-bottom: 24px;
        }}
        .alert-error {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid var(--danger);
            color: #fca5a5;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .form-group {{
            margin-bottom: 18px;
            text-align: left;
        }}
        label {{
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 11px 14px;
            background: #0f172a;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-main);
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: var(--primary);
        }}
        .btn-submit {{
            width: 100%;
            padding: 12px;
            background: var(--primary);
            color: #0f172a;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.2s;
            margin-top: 8px;
        }}
        .btn-submit:hover {{
            background: var(--primary-hover);
        }}
        .footer-note {{
            margin-top: 24px;
            font-size: 0.75rem;
            color: var(--text-muted);
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-badge">GPS Audio Guide</div>
        <h1>Quản Trị Hệ Thống</h1>
        <p class="subtitle">Đăng nhập tài khoản quản trị viên để vào Dashboard</p>

        {error_html}

        <form method="POST" action="{html.escape(admin_path)}login">
            <div class="form-group">
                <label for="username">Tên đăng nhập</label>
                <input type="text" id="username" name="username" value="{html.escape(username)}" required autocomplete="username" autofocus>
            </div>
            <div class="form-group">
                <label for="password">Mật khẩu</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit" class="btn-submit">Đăng nhập Quản Trị</button>
        </form>

        <p class="footer-note">Hệ thống bảo vệ đăng nhập tự động ngắt kết nối khi phát hiện bất thường.</p>
    </div>
</body>
</html>"""


def render_dashboard_page(
    admin_path: str,
    summary: Dict[str, Any],
    top_locations: List[Dict[str, Any]],
    recent_errors: List[Dict[str, Any]],
    locations_summary: Dict[str, Any],
    downstream_health: Dict[str, Any],
    username: str = "Quản trị viên",
) -> str:
    """Giao diện Dashboard Quản Trị đầy đủ các thông tin."""
    # Định dạng các chỉ số
    online_v = summary.get("online_visitors", 0)
    u_24h = summary.get("unique_visitors_24h", 0)
    u_7d = summary.get("unique_visitors_7d", 0)
    views_24h = summary.get("page_views_24h", 0)
    total_locs = locations_summary.get("total_locations", "--")
    cached_narrations = locations_summary.get("total_cached_narrations", "--")
    tts_24h = summary.get("tts_requests_24h", 0)
    tts_succ_24h = summary.get("tts_success_24h", 0)
    tts_fail_24h = summary.get("tts_fail_24h", 0)
    tts_avg_time = summary.get("tts_avg_duration_ms")
    tts_avg_display = f"{tts_avg_time} ms" if tts_avg_time is not None else "--"
    err_24h = summary.get("errors_24h", 0)

    # Bảng phân bổ ngôn ngữ
    lang_dist = summary.get("language_distribution_7d", {})
    lang_badges = "".join(
        f'<span class="badge badge-info">{html.escape(str(k)).upper()}: {v} lượt</span> '
        for k, v in lang_dist.items()
    ) if lang_dist else '<span class="text-muted">Chưa có dữ liệu ngôn ngữ</span>'

    # Top địa điểm
    top_loc_rows = ""
    if top_locations:
        for idx, loc in enumerate(top_locations, 1):
            top_loc_rows += f"""
            <tr>
                <td>#{idx}</td>
                <td class="font-semibold">{html.escape(str(loc.get("location_name", "")))}</td>
                <td><span class="badge badge-primary">{loc.get("request_count", 0)} yêu cầu</span></td>
                <td class="text-muted text-sm">{html.escape(str(loc.get("last_requested_iso", "--") or "--"))}</td>
            </tr>
            """
    else:
        top_loc_rows = '<tr><td colspan="4" class="text-center text-muted">Chưa có sự kiện tìm kiếm hoặc kích hoạt địa điểm nào được ghi nhận.</td></tr>'

    # Nhật ký lỗi
    error_rows = ""
    if recent_errors:
        for err in recent_errors:
            err_rows_cls = "badge-danger" if err.get("status_code", 500) >= 500 else "badge-warning"
            error_rows += f"""
            <tr>
                <td class="text-sm text-muted">{html.escape(str(err.get("time_iso", "")))}</td>
                <td><span class="badge {err_rows_cls}">{html.escape(str(err.get("service", "")))}</span></td>
                <td>{html.escape(str(err.get("request_type", "")))}</td>
                <td><span class="mono">{err.get("status_code") or "--"}</span></td>
                <td class="text-sm">{html.escape(str(err.get("message", "")))}</td>
            </tr>
            """
    else:
        error_rows = '<tr><td colspan="5" class="text-center text-success">Không ghi nhận lỗi nào trong hệ thống!</td></tr>'

    # Trạng thái downstream services
    cs_stat = downstream_health.get("content-service", {})
    tts_stat = downstream_health.get("translate-audio-service", {})
    cs_ok = cs_stat.get("status") == "ok"
    tts_ok = tts_stat.get("status") == "ok"

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard - GPS Audio Guide</title>
    <style>
        :root {{
            --bg-color: #0b1120;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #38bdf8;
            --primary-hover: #0284c7;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --info: #818cf8;
            --radius: 12px;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: var(--font-family);
            line-height: 1.5;
            padding: 20px;
        }}
        .dashboard-container {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}
        /* Top Navigation */
        .dash-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 16px 24px;
        }}
        .dash-brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .logo-badge {{
            background: rgba(56, 189, 248, 0.15);
            color: var(--primary);
            border: 1px solid var(--primary);
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            padding: 4px 10px;
            border-radius: 999px;
        }}
        .dash-title {{
            font-size: 1.25rem;
            font-weight: 700;
        }}
        .user-actions {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .user-greeting {{
            font-size: 0.9rem;
            color: var(--text-muted);
        }}
        .btn-logout {{
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
            border: 1px solid var(--danger);
            padding: 7px 14px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 0.85rem;
            font-weight: 600;
            transition: all 0.2s;
        }}
        .btn-logout:hover {{
            background: var(--danger);
            color: #ffffff;
        }}
        /* Notice Banner */
        .notice-banner {{
            background: rgba(56, 189, 248, 0.08);
            border-left: 4px solid var(--primary);
            padding: 12px 16px;
            border-radius: 6px;
            font-size: 0.85rem;
            color: var(--text-muted);
        }}
        .notice-banner strong {{ color: var(--text-main); }}

        /* KPI Cards Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
        }}
        .kpi-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 18px 20px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            position: relative;
            overflow: hidden;
        }}
        .kpi-card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: var(--card-border);
        }}
        .kpi-card.highlight::before {{ background: var(--primary); }}
        .kpi-card.success::before {{ background: var(--success); }}
        .kpi-card.warning::before {{ background: var(--warning); }}
        .kpi-card.danger::before {{ background: var(--danger); }}

        .kpi-label {{
            font-size: 0.85rem;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.025em;
        }}
        .kpi-value {{
            font-size: 2rem;
            font-weight: 800;
            color: var(--text-main);
        }}
        .kpi-subtext {{
            font-size: 0.78rem;
            color: var(--text-muted);
        }}

        /* Section Cards */
        .dash-section {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 20px 24px;
        }}
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--card-border);
        }}
        .section-title {{
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .filter-buttons {{
            display: flex;
            gap: 8px;
        }}
        .btn-filter {{
            background: #0f172a;
            border: 1px solid var(--card-border);
            color: var(--text-muted);
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-filter.active, .btn-filter:hover {{
            background: var(--primary);
            color: #0f172a;
            border-color: var(--primary);
            font-weight: 600;
        }}

        /* Tables */
        .table-responsive {{
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }}
        th, td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(51, 65, 85, 0.6);
        }}
        th {{
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        /* Badges */
        .badge {{
            display: inline-block;
            font-size: 0.75rem;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 999px;
        }}
        .badge-primary {{ background: rgba(56, 189, 248, 0.2); color: var(--primary); border: 1px solid var(--primary); }}
        .badge-success {{ background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); border: 1px solid var(--warning); }}
        .badge-danger {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }}
        .badge-info {{ background: rgba(129, 140, 248, 0.2); color: var(--info); border: 1px solid var(--info); }}

        /* Status Indicators */
        .service-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }}
        .service-box {{
            background: #0f172a;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 14px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .service-info h4 {{
            font-size: 0.95rem;
            margin-bottom: 2px;
        }}
        .service-info p {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .mono {{ font-family: monospace; }}
        .text-sm {{ font-size: 0.8rem; }}
        .text-muted {{ color: var(--text-muted); }}
        .text-center {{ text-align: center; }}
        .text-success {{ color: var(--success); }}
        .font-semibold {{ font-weight: 600; }}

        /* Chart placeholder / SVG */
        .chart-box {{
            background: #0f172a;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 16px;
            min-height: 200px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
    </style>
</head>
<body>
    <div class="dashboard-container">
        <!-- Top Navigation -->
        <header class="dash-header">
            <div class="dash-brand">
                <span class="logo-badge">Quản Trị</span>
                <h1 class="dash-title">GPS Audio Guide Dashboard</h1>
            </div>
            <div class="user-actions">
                <span class="user-greeting">Xin chào, <strong>{html.escape(username)}</strong></span>
                <a href="{html.escape(admin_path)}logout" class="btn-logout">Đăng xuất</a>
            </div>
        </header>

        <!-- Information Notice -->
        <div class="notice-banner">
            ℹ️ <strong>Lưu ý thống kê:</strong> Số liệu "Khách đang online" và "Khách duy nhất" được tính dựa trên mã định danh trình duyệt ẩn danh (Client ID) và heartbeat định kỳ mỗi 30 giây (không thu thập IP hay lịch sử GPS chi tiết). Trên Render Free, dữ liệu SQLite được giữ trong quá trình container chạy và có thể được làm mới khi redeploy.
        </div>

        <!-- KPI Summary Cards -->
        <div class="kpi-grid">
            <div class="kpi-card highlight">
                <span class="kpi-label">Khách đang online</span>
                <span class="kpi-value" id="kpi-online">{online_v}</span>
                <span class="kpi-subtext">Trình duyệt có heartbeat trong 90s qua</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-label">Khách duy nhất (24 giờ)</span>
                <span class="kpi-value">{u_24h}</span>
                <span class="kpi-subtext">Mã định danh hoạt động 24h</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-label">Khách duy nhất (7 ngày)</span>
                <span class="kpi-value">{u_7d}</span>
                <span class="kpi-subtext">Mã định danh hoạt động 7 ngày</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-label">Lượt xem trang (24h)</span>
                <span class="kpi-value">{views_24h}</span>
                <span class="kpi-subtext">Lượt tải trang /app/ thực tế</span>
            </div>
            <div class="kpi-card success">
                <span class="kpi-label">Tổng địa điểm</span>
                <span class="kpi-value">{total_locs}</span>
                <span class="kpi-subtext">Đã lưu trong CSDL Content Service</span>
            </div>
            <div class="kpi-card success">
                <span class="kpi-label">Bài thuyết minh lưu trữ</span>
                <span class="kpi-value">{cached_narrations}</span>
                <span class="kpi-subtext">Nội dung đa ngôn ngữ & MP3 đã cache</span>
            </div>
            <div class="kpi-card warning">
                <span class="kpi-label">Yêu cầu tạo âm thanh (24h)</span>
                <span class="kpi-value">{tts_24h}</span>
                <span class="kpi-subtext">Thành công: {tts_succ_24h} | Thất bại: {tts_fail_24h}</span>
            </div>
            <div class="kpi-card danger">
                <span class="kpi-label">Số lỗi hệ thống (24h)</span>
                <span class="kpi-value">{err_24h}</span>
                <span class="kpi-subtext">Ghi nhận từ logs backend/gateway</span>
            </div>
        </div>

        <!-- Microservices Health Status -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">🚦 Tình trạng hoạt động Microservices</h2>
                <span class="text-sm text-muted">Cập nhật nội bộ qua REST health-check</span>
            </div>
            <div class="service-grid">
                <div class="service-box">
                    <div class="service-info">
                        <h4>API Gateway Service</h4>
                        <p>Cổng chính, tiếp nhận GPS & phục vụ web</p>
                    </div>
                    <span class="badge badge-success">OK (Healthy)</span>
                </div>
                <div class="service-box">
                    <div class="service-info">
                        <h4>Content Service</h4>
                        <p>Port 8001 (Internal) - Quản lý địa điểm & SQLite</p>
                    </div>
                    <span class="badge {'badge-success' if cs_ok else 'badge-danger'}">
                        {'OK' if cs_ok else 'Lỗi kết nối'}
                    </span>
                </div>
                <div class="service-box">
                    <div class="service-info">
                        <h4>Translate Audio Service</h4>
                        <p>Port 8002 (Internal) - Bản địa hóa & Edge TTS</p>
                    </div>
                    <span class="badge {'badge-success' if tts_ok else 'badge-danger'}">
                        {'OK' if tts_ok else 'Lỗi kết nối'}
                    </span>
                </div>
            </div>
        </section>

        <!-- Biểu đồ & Xu hướng hoạt động -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">📊 Xu hướng hoạt động</h2>
                <div class="filter-buttons">
                    <button class="btn-filter" onclick="loadChart(1, this)">24 giờ</button>
                    <button class="btn-filter active" onclick="loadChart(7, this)">7 ngày</button>
                    <button class="btn-filter" onclick="loadChart(30, this)">30 ngày</button>
                </div>
            </div>
            <div class="chart-box" id="chart-container">
                <p class="text-center text-muted">Đang tải dữ liệu biểu đồ...</p>
            </div>
        </section>

        <!-- Thống kê AI, TTS & Ngôn ngữ -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">🎙️ Theo dõi Thuyết minh & AI TTS</h2>
            </div>
            <div class="service-grid" style="margin-bottom: 16px;">
                <div class="service-box">
                    <div class="service-info">
                        <h4>Thời gian phản hồi TTS trung bình</h4>
                        <p>Thời gian sinh giọng nói Edge TTS thực tế</p>
                    </div>
                    <span class="badge badge-info mono">{tts_avg_display}</span>
                </div>
                <div class="service-box">
                    <div class="service-info">
                        <h4>Ngôn ngữ được yêu cầu (7 ngày)</h4>
                        <p>Thống kê lượt chọn ngôn ngữ khi phát hiện GPS</p>
                    </div>
                    <div>{lang_badges}</div>
                </div>
            </div>
        </section>

        <!-- Top địa điểm được tìm kiếm & kích hoạt -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">📍 Top địa điểm được quan tâm nhất</h2>
                <span class="text-sm text-muted">Dựa trên sự kiện phát hiện & kích hoạt thuyết minh thực tế</span>
            </div>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Thứ hạng</th>
                            <th>Tên địa điểm</th>
                            <th>Lượt kích hoạt / tìm kiếm</th>
                            <th>Lần gần nhất</th>
                        </tr>
                    </thead>
                    <tbody>
                        {top_loc_rows}
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Quản lý địa điểm: Tìm kiếm & Phân trang từ CSDL Content Service -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">🗺️ Quản lý danh mục địa điểm</h2>
                <input type="text" id="loc-search" placeholder="Tìm kiếm địa điểm..." onkeyup="filterLocations()" style="padding: 6px 12px; background: #0f172a; border: 1px solid var(--card-border); border-radius: 6px; color: #fff; font-size: 0.85rem;">
            </div>
            <div class="table-responsive">
                <table id="locations-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Tên địa điểm</th>
                            <th>Tọa độ (GPS)</th>
                            <th>Bán kính</th>
                            <th>Nguồn dữ liệu</th>
                            <th>Trạng thái Audio</th>
                        </tr>
                    </thead>
                    <tbody id="locations-tbody">
                        <tr><td colspan="6" class="text-center text-muted">Đang tải danh sách địa điểm...</td></tr>
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Nhật ký lỗi gần đây -->
        <section class="dash-section">
            <div class="section-header">
                <h2 class="section-title">🚨 Nhật ký lỗi gần đây</h2>
                <span class="text-sm text-muted">Dữ liệu IP và nhạy cảm đã được loại bỏ tự động</span>
            </div>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Thời gian</th>
                            <th>Dịch vụ</th>
                            <th>Loại yêu cầu</th>
                            <th>Mã lỗi</th>
                            <th>Nội dung lỗi</th>
                        </tr>
                    </thead>
                    <tbody>
                        {error_rows}
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <script>
        const ADMIN_API = "{html.escape(admin_path)}api";

        // Tải dữ liệu biểu đồ
        async function loadChart(days, btn) {{
            if (btn) {{
                document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            }}
            const container = document.getElementById('chart-container');
            container.innerHTML = '<p class="text-center text-muted">Đang tải dữ liệu biểu đồ...</p>';

            try {{
                const res = await fetch(`${{ADMIN_API}}/chart?days=${{days}}`);
                if (!res.ok) throw new Error("Lỗi tải biểu đồ");
                const data = await res.json();
                renderSvgChart(data, container);
            }} catch (e) {{
                container.innerHTML = `<p class="text-center text-muted">Không thể tải biểu đồ: ${{e.message}}</p>`;
            }}
        }}

        function renderSvgChart(data, container) {{
            if (!data || data.length === 0) {{
                container.innerHTML = '<p class="text-center text-muted">Chưa có đủ dữ liệu lượt truy cập trong khoảng thời gian này.</p>';
                return;
            }}

            const maxVisitors = Math.max(...data.map(d => Math.max(d.visitors || 0, d.page_views || 0)), 5);
            const w = 800, h = 180, pad = 40;
            const step = (w - pad * 2) / Math.max(data.length - 1, 1);

            let pointsVisitors = [];
            let pointsViews = [];
            let labels = "";

            data.forEach((d, idx) => {{
                const x = pad + idx * step;
                const yV = h - pad - ((d.visitors || 0) / maxVisitors) * (h - pad * 2);
                const yP = h - pad - ((d.page_views || 0) / maxVisitors) * (h - pad * 2);
                pointsVisitors.push(`${{x}},${{yV}}`);
                pointsViews.push(`${{x}},${{yP}}`);
                labels += `<text x="${{x}}" y="${{h - 10}}" fill="#94a3b8" font-size="11" text-anchor="middle">${{d.date.slice(5)}}</text>`;
            }});

            const svg = `
                <svg viewBox="0 0 ${{w}} ${{h}}" style="width: 100%; height: 200px; overflow: visible;">
                    <!-- Grid Lines -->
                    <line x1="${{pad}}" y1="${{h - pad}}" x2="${{w - pad}}" y2="${{h - pad}}" stroke="#334155" stroke-width="1"/>
                    <line x1="${{pad}}" y1="${{pad}}" x2="${{w - pad}}" y2="${{pad}}" stroke="#334155" stroke-dasharray="4" stroke-width="1"/>
                    
                    <!-- Polyline Page Views -->
                    <polyline fill="none" stroke="#818cf8" stroke-width="2" points="${{pointsViews.join(' ')}}" />
                    <!-- Polyline Unique Visitors -->
                    <polyline fill="none" stroke="#38bdf8" stroke-width="3" points="${{pointsVisitors.join(' ')}}" />

                    <!-- Labels -->
                    ${{labels}}
                </svg>
                <div style="display: flex; gap: 20px; justify-content: center; margin-top: 10px; font-size: 0.8rem;">
                    <span style="color: #38bdf8;">━ Khách truy cập (Visitors)</span>
                    <span style="color: #818cf8;">━ Lượt xem trang (Page views)</span>
                </div>
            `;
            container.innerHTML = svg;
        }}

        // Tải danh mục địa điểm từ Content Service qua Admin API
        let allLocations = [];
        async function loadLocations() {{
            const tbody = document.getElementById('locations-tbody');
            try {{
                const res = await fetch(`${{ADMIN_API}}/locations`);
                if (!res.ok) throw new Error("Không thể tải danh sách địa điểm");
                allLocations = await res.json();
                renderLocations(allLocations);
            }} catch (e) {{
                tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Lỗi: ${{e.message}}</td></tr>`;
            }}
        }}

        function renderLocations(locs) {{
            const tbody = document.getElementById('locations-tbody');
            if (!locs || locs.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Chưa có địa điểm nào trong CSDL.</td></tr>';
                return;
            }}
            tbody.innerHTML = locs.map(l => `
                <tr>
                    <td>#${{l.id}}</td>
                    <td class="font-semibold">${{escapeHtml(l.name)}}</td>
                    <td class="mono text-sm">${{l.latitude ? l.latitude.toFixed(5) + ', ' + l.longitude.toFixed(5) : '--'}}</td>
                    <td>${{l.radius || 80}}m</td>
                    <td><span class="badge badge-info">${{l.source || 'manual'}}</span></td>
                    <td>
                        <span class="badge ${{l.is_ready ? 'badge-success' : 'badge-warning'}}">
                            ${{l.is_ready ? 'Audio Ready' : 'Chưa có Audio'}}
                        </span>
                    </td>
                </tr>
            `).join('');
        }}

        function filterLocations() {{
            const q = document.getElementById('loc-search').value.toLowerCase().trim();
            if (!q) {{
                renderLocations(allLocations);
                return;
            }}
            const filtered = allLocations.filter(l => 
                (l.name && l.name.toLowerCase().includes(q)) || 
                (l.source && l.source.toLowerCase().includes(q))
            );
            renderLocations(filtered);
        }}

        function escapeHtml(str) {{
            if (!str) return '';
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }}

        // Khởi động
        loadChart(7);
        loadLocations();

        // Định kỳ cập nhật chỉ số online mỗi 15 giây (Polling nhẹ)
        setInterval(async () => {{
            try {{
                const res = await fetch(`${{ADMIN_API}}/summary`);
                if (res.ok) {{
                    const d = await res.json();
                    if (d.online_visitors !== undefined) {{
                        document.getElementById('kpi-online').innerText = d.online_visitors;
                    }}
                }}
            }} catch (err) {{}}
        }}, 15000);
    </script>
</body>
</html>"""
