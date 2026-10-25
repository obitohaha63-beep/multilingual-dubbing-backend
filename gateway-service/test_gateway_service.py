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


@pytest.mark.parametrize("pending", [True, False])
def test_detect_calls_content_and_preserves_signed_storage_url(pending):
    from main import CONTENT_SERVICE_URL
    place = {"id": 1, "name": "Chợ", "radius": 80, "distance_m": 10}
    signed = "https://project.supabase.co/storage/v1/object/sign/bucket/file.mp3?token=test"
    content = httpx.Response(202 if pending else 200, json={
        "narration_status": "processing" if pending else "audio_ready",
        "audio_url": None if pending else signed, "requested_lang": "vi", "cache_hit": not pending})
    nearby = httpx.Response(200, json={"candidates": [place]})
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=nearby), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=content) as post:
        result = client.post("/locations/detect", json={"latitude": 10, "longitude": 106, "language": "vi"})
    data = result.json()
    assert post.call_args.args[0] == f"{CONTENT_SERVICE_URL}/narrate"
    assert data["narration_status"] == ("processing" if pending else "audio_ready")
    assert data["audio_url"] == (None if pending else signed)
    assert data["cache_hit"] is (not pending)


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

def test_detect_inside_activation_radius_audio_ready():
    """
    Candidate có khoảng cách 40.5m <= radius 80m -> Kích hoạt!
    Gateway gọi B /narrate và B trả về 200 với audio_ready:
    - audio_url được chuyển sang URL public cùng origin: /audio/<filename>.mp3
    - narration_status='audio_ready', detected=True
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
        "description_source": "Nhà thờ chính tòa Đức Bà Sài Gòn...",
        "source_lang": "vi",
        "source_page_id": "12345",
    }

    mock_b_resp = {
        "requested_lang": "vi",
        "lang": "vi",
        "translated_text": "Bản thuyết minh tiếng Việt hoàn chỉnh...",
        "audio_url": "http://translate-audio-service:8002/audio/ducba_vi_abc123.mp3",
        "audio_filename": "ducba_vi_abc123.mp3",
        "voice": "vi-VN-HoaiMyNeural",
        "text_hash": "abc123hash",
        "source_url": mock_candidate["source_url"],
        "is_fallback": False,
        "reason": None,
        "narration_status": "audio_ready",
        "cache_hit": True,
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        resp_get = MagicMock()
        resp_get.status_code = 200
        resp_get.is_error = False
        resp_get.is_server_error = False
        resp_get.json.return_value = {"candidates": [mock_candidate]}
        mock_get.return_value = resp_get

        resp_post = MagicMock()
        resp_post.status_code = 200
        resp_post.is_error = False
        resp_post.json.return_value = mock_b_resp
        mock_post.return_value = resp_post

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
        assert data["narration_status"] == "audio_ready"
        assert data["audio_url"] == "/audio/ducba_vi_abc123.mp3"
        assert data["translated_text"] == "Bản thuyết minh tiếng Việt hoàn chỉnh..."
        assert data["lang"] == "vi"
        assert data["is_fallback"] is False
        assert mock_post.called


def test_detect_inside_activation_radius_text_ready():
    """
    Candidate kích hoạt, B /narrate trả text_ready (TTS của B lỗi nhưng có văn bản):
    - narration_status='text_ready'
    - audio_url=None
    - translated_text và lang sẵn sàng cho client Web SpeechSynthesis
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
        "description_source": "Nhà thờ chính tòa Đức Bà Sài Gòn...",
    }

    mock_b_resp = {
        "requested_lang": "vi",
        "lang": "vi",
        "translated_text": "Văn bản thuyết minh dùng cho Web Speech",
        "audio_url": None,
        "audio_filename": None,
        "voice": None,
        "text_hash": "xyzhash",
        "source_url": mock_candidate["source_url"],
        "is_fallback": False,
        "reason": "tts_error: Edge TTS failed, fallback to text",
        "narration_status": "text_ready",
        "cache_hit": False,
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        resp_get = MagicMock()
        resp_get.status_code = 200
        resp_get.is_error = False
        resp_get.is_server_error = False
        resp_get.json.return_value = {"candidates": [mock_candidate]}
        mock_get.return_value = resp_get

        resp_post = MagicMock()
        resp_post.status_code = 200
        resp_post.is_error = False
        resp_post.json.return_value = mock_b_resp
        mock_post.return_value = resp_post

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.7797, "longitude": 106.6990, "language": "vi"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["narration_status"] == "text_ready"
        assert data["audio_url"] is None
        assert data["translated_text"] == "Văn bản thuyết minh dùng cho Web Speech"
        assert data["lang"] == "vi"


def test_detect_inside_radius_b_server_error():
    """
    Khi B bị lỗi 500 hoặc 503 và không có text:
    - narration_status='error'
    - Tuyệt đối không giả là narration thành công
    """
    mock_candidate = {
        "id": 1,
        "name": "Nhà thờ Đức Bà",
        "latitude": 10.77978,
        "longitude": 106.69902,
        "radius": 80.0,
        "distance_m": 40.5,
        "source": "wikimedia",
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        resp_get = MagicMock()
        resp_get.status_code = 200
        resp_get.is_error = False
        resp_get.is_server_error = False
        resp_get.json.return_value = {"candidates": [mock_candidate]}
        mock_get.return_value = resp_get

        resp_post = MagicMock()
        resp_post.status_code = 503
        resp_post.is_error = True
        resp_post.json.return_value = {"detail": "Translation service down"}
        mock_post.return_value = resp_post

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.7797, "longitude": 106.6990, "language": "vi"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["narration_status"] == "error"
        assert "narrate_error" in data["reason"]
        assert data["audio_url"] is None
        assert data["translated_text"] is None


def test_detect_inside_radius_b_timeout():
    """
    Khi gọi B bị Timeout:
    - Gateway trả narration_status='error', reason='narrate_unavailable'
    """
    mock_candidate = {
        "id": 1,
        "name": "Nhà thờ Đức Bà",
        "latitude": 10.77978,
        "longitude": 106.69902,
        "radius": 80.0,
        "distance_m": 40.5,
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", side_effect=httpx.ReadTimeout("Timeout from B")):
        resp_get = MagicMock()
        resp_get.status_code = 200
        resp_get.is_error = False
        resp_get.is_server_error = False
        resp_get.json.return_value = {"candidates": [mock_candidate]}
        mock_get.return_value = resp_get

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.7797, "longitude": 106.6990, "language": "vi"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["narration_status"] == "error"
        assert data["reason"] == "narrate_unavailable"
        assert data["audio_url"] is None


def test_detect_outside_activation_radius_never_calls_b():
    """
    Candidate có khoảng cách 150m > radius 80m:
    - detected=False, reason='outside_activation_radius'
    - TUYỆT ĐỐI KHÔNG GỌI B (/narrate)
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

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
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
        # Khẳng định B không được gọi
        mock_post.assert_not_called()


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

    with patch("httpx.AsyncClient.get", side_effect=custom_get), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        resp_post = MagicMock()
        resp_post.status_code = 200
        resp_post.is_error = False
        resp_post.json.return_value = {
            "requested_lang": "vi",
            "lang": "vi",
            "translated_text": "Dinh Độc Lập...",
            "audio_url": "http://translate-audio-service:8002/audio/dinhdoclap.mp3",
            "audio_filename": "dinhdoclap.mp3",
            "narration_status": "audio_ready",
        }
        mock_post.return_value = resp_post

        resp = client.post(
            "/locations/detect",
            json={"latitude": 10.777, "longitude": 106.695, "language": "vi"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] is True
        assert data["location"]["name"] == "Dinh Độc Lập"
        assert data["distance_m"] == 35.0
        assert data["narration_status"] == "audio_ready"
        assert data["audio_url"] == "/audio/dinhdoclap.mp3"


def test_detect_no_place_found_never_calls_b():
    """
    Sau cả Nearby và Discover đều không có địa điểm nào trong 500m:
    - detected=False, reason='no_place_found'
    - TUYỆT ĐỐI KHÔNG GỌI B (/narrate)
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
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
        mock_post.assert_not_called()


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


# ===========================================================================
# 3. Audio Proxy & Debug TTS Tests (Tuần 4)
# ===========================================================================

class MockAsyncStreamResponse:
    def __init__(self, status_code=200, headers=None, content=b"ID3\x03\x00\x00\x00fake-mp3-bytes"):
        self.status_code = status_code
        self.headers = headers or {
            "Content-Type": "audio/mpeg",
            "Content-Length": str(len(content)),
            "Accept-Ranges": "bytes",
        }
        self.content = content

    async def aiter_bytes(self, chunk_size=16384):
        yield self.content

    async def aclose(self):
        pass


def test_audio_proxy_success():
    """
    Proxy stream MP3 thành công từ translate-audio-service: HTTP 200, Content-Type audio/mpeg.
    """
    mock_resp = MockAsyncStreamResponse(status_code=200, content=b"fake-mp3-audio-data")
    with patch("httpx.AsyncClient.send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_resp

        resp = client.get("/audio/a1b2c3d4e5f678901234567890abcdef.mp3")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "audio/mpeg"
        assert resp.content == b"fake-mp3-audio-data"
        assert resp.headers.get("Accept-Ranges") == "bytes"


def test_audio_proxy_with_range_header():
    """
    Proxy stream MP3 với Range header (mobile seeking / chunk streaming): forward header và trả 206 Partial Content.
    """
    range_headers = {
        "Content-Type": "audio/mpeg",
        "Content-Range": "bytes 0-499/1000",
        "Content-Length": "500",
        "Accept-Ranges": "bytes",
    }
    mock_resp = MockAsyncStreamResponse(
        status_code=206,
        headers=range_headers,
        content=b"partial-mp3-bytes",
    )
    with patch("httpx.AsyncClient.send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_resp

        resp = client.get(
            "/audio/a1b2c3d4e5f678901234567890abcdef.mp3",
            headers={"Range": "bytes=0-499"},
        )
        assert resp.status_code == 206
        assert resp.headers["Content-Range"] == "bytes 0-499/1000"
        assert resp.content == b"partial-mp3-bytes"


def test_audio_proxy_404_not_found():
    """
    Khi file MP3 không tồn tại trên máy chủ audio (B) -> Gateway trả 404 rõ ràng.
    """
    mock_resp = MockAsyncStreamResponse(status_code=404)
    with patch("httpx.AsyncClient.send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_resp

        resp = client.get("/audio/nonexistent_file.mp3")
        assert resp.status_code == 404
        assert "không tồn tại" in resp.json()["detail"]


def test_audio_proxy_timeout():
    """
    Khi gọi sang B bị timeout -> Gateway trả 503 Service Unavailable, không sập server.
    """
    with patch("httpx.AsyncClient.send", side_effect=httpx.ReadTimeout("Read timeout from audio service")):
        resp = client.get("/audio/timeout_file.mp3")
        assert resp.status_code == 503
        assert "không khả dụng" in resp.json()["detail"]


def test_audio_proxy_network_error():
    """
    Khi kết nối mạng tới B bị lỗi (ConnectError) -> Gateway trả 503.
    """
    with patch("httpx.AsyncClient.send", side_effect=httpx.ConnectError("Connection refused")):
        resp = client.get("/audio/connection_error.mp3")
        assert resp.status_code == 503
        assert "không khả dụng" in resp.json()["detail"]


@pytest.mark.parametrize("invalid_filename", [
    "../../etc/passwd.mp3",
    "..\\windows\\system32.mp3",
    "/root/secret.mp3",
    "test:stream.mp3",
    "file_without_extension",
    "script.sh",
    "image.png",
    "audio.wav",
    "",
])
def test_audio_proxy_path_traversal_and_invalid_filenames(invalid_filename):
    """
    Validate chặt chẽ: ngăn chặn tuyệt đối path traversal và các file không phải .mp3 -> HTTP 422.
    """
    resp = client.get(f"/audio/{invalid_filename}")
    # Nếu filename rỗng hoặc có slash có thể ra 404 (route not matched) hoặc 422
    assert resp.status_code in (404, 422)


def test_debug_tts_success():
    """
    Endpoint POST /debug/text-to-speech gọi B thành công và rewrite audio_url về same-origin Gateway (/audio/...).
    """
    mock_tts_data = {
        "lang": "vi",
        "voice": "vi-VN-HoaiMyNeural",
        "text_hash": "a1b2c3",
        "audio_filename": "f7e8d9.mp3",
        "audio_url": "/audio/f7e8d9.mp3",
        "cache_hit": False,
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.json.return_value = mock_tts_data
        mock_post.return_value = mock_resp

        payload = {"text": "Xin chào thế giới", "lang": "vi"}
        resp = client.post("/debug/text-to-speech", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["audio_url"] == "/audio/f7e8d9.mp3"
        assert data["audio_filename"] == "f7e8d9.mp3"


def test_debug_tts_invalid_language():
    """
    POST /debug/text-to-speech với ngôn ngữ không hỗ trợ -> 422.
    """
    payload = {"text": "Bonjour", "lang": "fr"}
    resp = client.post("/debug/text-to-speech", json=payload)
    assert resp.status_code == 422


def test_debug_tts_upstream_timeout():
    """
    POST /debug/text-to-speech gặp timeout từ B -> 503.
    """
    with patch("httpx.AsyncClient.post", side_effect=httpx.ReadTimeout("Timeout")):
        payload = {"text": "Kiểm tra timeout", "lang": "vi"}
        resp = client.post("/debug/text-to-speech", json=payload)
        assert resp.status_code == 503
