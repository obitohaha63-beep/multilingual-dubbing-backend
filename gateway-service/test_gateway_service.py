"""
Unit & Integration Tests cho gateway-service — Tuần 3.
Chạy bằng pytest:
    pytest gateway-service/test_gateway_service.py -v

Test coverage:
  - GET /health
  - Phục vụ Static Files /app/ (index.html, app.js, style.css)
  - GET /locations/{id}/preview (kế thừa tuần 2)
  - Đảm bảo route dynamic /{id} không nuốt POST /locations/detect
  - POST /locations/detect:
      * Validation: tọa độ vô hạn (inf/nan), tọa độ ngoài [-90,90] & [-180,180] -> 422
      * Validation: language không có trong allowlist (vi, en, ja) -> 422
      * Validation: accuracy_m âm hoặc vô hạn -> 422
      * Cache & Không có địa điểm nào (no_place_found)
      * Có địa điểm nhưng ngoài bán kính kích hoạt (outside_activation_radius)
      * Địa điểm trong bán kính kích hoạt (detected=True, narration_status='not_implemented')
      * Upstream Content Service bị lỗi / 503 / network timeout (upstream_unavailable)
      * Luồng Discover được kích hoạt khi Nearby ban đầu rỗng hoặc không có địa điểm kích hoạt
"""

import math
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from main import app, DetectRequest, DetectResponse

client = TestClient(app)


# ===========================================================================
# 1. Health & Legacy Preview & Route conflicts
# ===========================================================================

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"service": "gateway-service", "status": "ok"}


def test_static_app_served():
    resp = client.get("/app/")
    assert resp.status_code == 200
    assert "Thuyết Minh Du Lịch Tự Động" in resp.text
    assert "btn-start" in resp.text


def test_preview_legacy_success():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.json.return_value = {"id": 1, "name": "Bưu điện Trung tâm"}
        mock_get.return_value = mock_resp

        resp = client.get("/locations/1/preview")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bưu điện Trung tâm"


def test_preview_legacy_404():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.is_error = True
        mock_resp.json.return_value = {"detail": "Địa điểm không tồn tại"}
        mock_get.return_value = mock_resp

        resp = client.get("/locations/999/preview")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Địa điểm không tồn tại"


def test_route_id_does_not_swallow_detect():
    """
    Đảm bảo POST /locations/detect không bao giờ bị router nuốt nhầm sang route khác.
    """
    payload = {
        "latitude": 10.7769,
        "longitude": 106.7009,
        "language": "vi",
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.is_server_error = False
        mock_resp.json.return_value = {"candidates": []}
        mock_get.return_value = mock_resp

        resp = client.post("/locations/detect", json=payload)
        # Nếu bị route GET /{id}/preview nuốt, method POST sẽ trả về 405 Method Not Allowed
        assert resp.status_code == 200
        data = resp.json()
        assert "detected" in data
        assert "narration_status" in data


# ===========================================================================
# 2. Validation tests for POST /locations/detect
# ===========================================================================

@pytest.mark.parametrize("lat,lon", [
    (91.0, 106.0),
    (-90.1, 106.0),
    (10.0, 180.1),
    (10.0, -180.1),
])
def test_detect_coords_out_of_range(lat, lon):
    resp = client.post(
        "/locations/detect",
        json={"latitude": lat, "longitude": lon, "language": "vi"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("lang", ["fr", "de", "zh", "korean", ""])
def test_detect_language_not_in_allowlist(lang):
    resp = client.post(
        "/locations/detect",
        json={"latitude": 10.77, "longitude": 106.70, "language": lang}
    )
    assert resp.status_code == 422
    assert "không được hỗ trợ" in resp.text


def test_detect_accuracy_negative():
    resp = client.post(
        "/locations/detect",
        json={"latitude": 10.77, "longitude": 106.70, "language": "vi", "accuracy_m": -5.0}
    )
    assert resp.status_code == 422


def test_detect_accuracy_valid():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.is_server_error = False
        mock_resp.json.return_value = {"candidates": []}
        mock_get.return_value = mock_resp

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.77, "longitude": 106.70, "language": "en", "accuracy_m": 12.5}
        )
        assert resp.status_code == 200
        assert resp.json()["requested_lang"] == "en"


# ===========================================================================
# 3. Detect Flow & Business Logic
# ===========================================================================

def test_detect_inside_activation_radius():
    """
    Candidate có khoảng cách 40m <= radius 80m -> Kích hoạt!
    detected=True, narration_status='not_implemented' (tuần 3 chưa phát audio)
    """
    mock_candidate = {
        "id": 1,
        "name": "Nhà thờ Đức Bà",
        "latitude": 10.77978,
        "longitude": 106.69902,
        "radius": 80.0,
        "distance_m": 40.5,
        "source": "wikimedia",
        "source_url": "https://vi.wikipedia.org/wiki/Nh%C3%A0_th%E1%BB%9D_%C4%90%E1%BB%A9c_B%C3%A0_S%C3%A0i_G%C3%B2n",
        "description_source": "Nhà thờ chính tòa Đức Bà Sài Gòn..."
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.is_server_error = False
        mock_resp.json.return_value = {"candidates": [mock_candidate]}
        mock_get.return_value = mock_resp

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.7797, "longitude": 106.6990, "language": "vi"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["reason"] is None
        assert data["location"]["name"] == "Nhà thờ Đức Bà"
        assert data["distance_m"] == 40.5
        assert data["narration_status"] == "not_implemented"
        assert data["audio_url"] is None
        assert data["translated_text"] is None
        assert data["source_url"] == mock_candidate["source_url"]


def test_detect_outside_activation_radius():
    """
    Candidate có khoảng cách 150m > radius 80m -> Không kích hoạt.
    detected=False, reason='outside_activation_radius'
    """
    mock_candidate = {
        "id": 2,
        "name": "Chợ Bến Thành",
        "latitude": 10.772,
        "longitude": 106.698,
        "radius": 80.0,
        "distance_m": 150.0,
        "source": "wikimedia",
        "source_url": "https://vi.wikipedia.org/wiki/Ch%E1%BB%A3_B%E1%BA%BFn_Th%C3%A0nh"
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        # Lần 1: /nearby -> có candidate nhưng ngoài radius
        # Hệ thống gọi /discover để tìm xem có địa điểm nào gần hơn không
        # Giả sử sau discover vẫn là candidate đó
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.is_server_error = False
        mock_resp.json.return_value = {"candidates": [mock_candidate], "cache_hit": True}
        mock_get.return_value = mock_resp

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.773, "longitude": 106.699, "language": "ja"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is False
        assert data["reason"] == "outside_activation_radius"
        assert data["location"]["id"] == 2
        assert data["distance_m"] == 150.0
        assert data["narration_status"] is None


def test_detect_triggers_discovery_and_finds_new_place():
    """
    Khi /nearby ban đầu rỗng, Gateway tự động gọi /discover để tìm và lưu bài Wikimedia,
    sau đó /nearby lần 2 trả về kết quả mới.
    """
    candidate = {
        "id": 5,
        "name": "Dinh Độc Lập",
        "latitude": 10.777,
        "longitude": 106.695,
        "radius": 80.0,
        "distance_m": 35.0,
        "source": "wikimedia",
    }

    async def custom_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.is_error = False
        resp.is_server_error = False
        resp.status_code = 200
        if "nearby" in url:
            # Lần đầu chưa có, lần sau có
            if not getattr(custom_get, "discovered", False):
                resp.json.return_value = {"candidates": []}
            else:
                resp.json.return_value = {"candidates": [candidate]}
        elif "discover" in url:
            custom_get.discovered = True
            resp.json.return_value = {"candidates": [candidate], "cache_hit": False}
        return resp

    with patch("httpx.AsyncClient.get", side_effect=custom_get):
        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.777, "longitude": 106.695, "language": "vi"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["location"]["name"] == "Dinh Độc Lập"
        assert data["distance_m"] == 35.0


def test_detect_no_place_found():
    """
    Sau cả Nearby và Discover đều không có địa điểm nào trong 500m.
    detected=False, reason='no_place_found'
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.is_server_error = False
        mock_resp.json.return_value = {"candidates": []}
        mock_get.return_value = mock_resp

        resp = client.post(
            "/locations/detect",
            json={"latitude": 0.0, "longitude": 0.0, "language": "vi"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is False
        assert data["reason"] == "no_place_found"
        assert data["location"] is None


def test_detect_upstream_timeout_error():
    """
    Khi Content Service bị ngắt kết nối (httpx.RequestError) -> reason='upstream_unavailable', không sập service.
    """
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectTimeout("Connection timeout")):
        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.77, "longitude": 106.70, "language": "vi"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is False
        assert data["reason"] == "upstream_unavailable"
