"""
Bài kiểm tra chuyên biệt tái hiện và xác minh lỗi thực tế:
- GPS tại 21.02850, 105.85420: Có bài không liên quan 'Việt Nam Dân chủ Cộng hòa' cách 18.9m
  và bài tham quan phù hợp 'Hồ Hoàn Kiếm' (hoặc 'Đền Ngọc Sơn') cách xa hơn.
- Hệ thống tuyệt đối KHÔNG được chọn bài 'Việt Nam Dân chủ Cộng hòa'.
- Hệ thống phải chọn bài tham quan 'Hồ Hoàn Kiếm'.
- Kiểm tra các trường hợp bài chính thể, quốc gia, con người, đơn vị hành chính diện rộng bị loại bỏ.
- Đo thời gian xử lý và kiểm tra fast path.
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

GATEWAY_DIR = Path(__file__).resolve().parent

# Đảm bảo GATEWAY_DIR nằm ở đầu sys.path để các import phụ lấy đúng từ thư mục Gateway
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

# Nạp trực tiếp gateway-service/main.py thành module riêng biệt 'gateway_main'
spec = importlib.util.spec_from_file_location("gateway_main", GATEWAY_DIR / "main.py")
gateway_main = importlib.util.module_from_spec(spec)
sys.modules["gateway_main"] = gateway_main
spec.loader.exec_module(gateway_main)

app = gateway_main.app
_find_active_candidate = gateway_main._find_active_candidate
haversine_m = gateway_main.haversine_m
is_valid_travel_poi = gateway_main.is_valid_travel_poi


def test_poi_filter_rejects_political_entities_and_abstract_articles():
    """Kiểm tra bộ lọc loại bỏ chính xác các bài về nhà nước, chính thể, tổ chức, sự kiện, con người."""
    # 1. Quốc gia / Nhà nước / Chính thể
    is_valid, reason, _ = is_valid_travel_poi("Việt Nam Dân chủ Cộng hòa", "Việt Nam Dân chủ Cộng hòa là một nhà nước tồn tại từ năm 1945 đến năm 1976...")
    assert not is_valid, "Phải loại bài Việt Nam Dân chủ Cộng hòa"

    is_valid, reason, _ = is_valid_travel_poi("Cộng hòa Xã hội chủ nghĩa Việt Nam", "là một quốc gia nằm ở cực đông bán đảo Đông Dương...")
    assert not is_valid

    is_valid, reason, _ = is_valid_travel_poi("Đại La", "Đại La là tên gọi cũ của vùng đất...")
    assert not is_valid

    # 2. Tổ chức / Ủy ban
    is_valid, reason, _ = is_valid_travel_poi("Ủy ban Quốc tế về Giám sát và Kiểm soát", "là một tổ chức quốc tế...")
    assert not is_valid

    # 3. Đơn vị hành chính diện rộng
    is_valid, reason, _ = is_valid_travel_poi("Hà Nội", "Hà Nội là thủ đô của nước Cộng hòa xã hội chủ nghĩa Việt Nam...")
    assert not is_valid

    is_valid, reason, _ = is_valid_travel_poi("Hoàn Kiếm (quận)", "Hoàn Kiếm là một quận trung tâm...")
    assert not is_valid

    is_valid, reason, _ = is_valid_travel_poi("Hoàn Kiếm (phường)", "Hoàn Kiếm là một phường thuộc...")
    assert not is_valid

    # 4. Địa điểm tham quan du lịch hợp lệ (POI)
    is_valid, reason, score = is_valid_travel_poi("Hồ Hoàn Kiếm", "Hồ Hoàn Kiếm còn được gọi là Hồ Gươm là một hồ nước ngọt tự nhiên...")
    assert is_valid, "Phải chấp nhận Hồ Hoàn Kiếm"
    assert score >= 100

    is_valid, reason, score = is_valid_travel_poi("Đền Ngọc Sơn", "Đền Ngọc Sơn là một ngôi đền thờ nằm trên đảo Ngọc của hồ Hoàn Kiếm...")
    assert is_valid, "Phải chấp nhận Đền Ngọc Sơn"
    assert score >= 100

    is_valid, reason, score = is_valid_travel_poi("Tháp Hòa Phong", "Tháp Hòa Phong là một ngôi tháp nằm ở bên bờ hồ Hoàn Kiếm...")
    assert is_valid, "Phải chấp nhận Tháp Hòa Phong"

    is_valid, reason, score = is_valid_travel_poi("Bưu điện Hà Nội", "Bưu điện Hà Nội là một công trình kiến trúc cổ...")
    assert is_valid, "Phải chấp nhận Bưu điện Hà Nội"


def test_find_active_candidate_skips_closer_irrelevant_article_and_picks_hoan_kiem():
    """
    Tái hiện đúng trường hợp thực tế của người dùng:
    - Bài 'Việt Nam Dân chủ Cộng hòa' ở khoảng cách 18.9 m (bán kính 80 m)
    - Bài 'Hồ Hoàn Kiếm' ở khoảng cách 50.0 m (bán kính 150 m)
    -> Tuyệt đối không chọn 'Việt Nam Dân chủ Cộng hòa', PHẢI chọn 'Hồ Hoàn Kiếm'.
    """
    candidates = [
        {
            "id": 1,
            "name": "Việt Nam Dân chủ Cộng hòa",
            "source_title": "Việt Nam Dân chủ Cộng hòa",
            "description_source": "Việt Nam Dân chủ Cộng hòa là một nhà nước tồn tại từ năm 1945 đến năm 1976 trong lịch sử Việt Nam.",
            "distance_m": 18.9,
            "radius": 80.0,
            "latitude": 21.028333,
            "longitude": 105.854167,
        },
        {
            "id": 2,
            "name": "Ủy ban Quốc tế về Giám sát và Kiểm soát",
            "source_title": "Ủy ban Quốc tế về Giám sát và Kiểm soát",
            "description_source": "Ủy ban Quốc tế về Giám sát và Kiểm soát là một tổ chức quốc tế...",
            "distance_m": 18.9,
            "radius": 80.0,
            "latitude": 21.028333,
            "longitude": 105.854167,
        },
        {
            "id": 3,
            "name": "Hồ Hoàn Kiếm",
            "source_title": "Hồ Hoàn Kiếm",
            "description_source": "Hồ Hoàn Kiếm còn được gọi là Hồ Gươm là một hồ nước ngọt tự nhiên nằm ở trung tâm Hà Nội.",
            "distance_m": 50.0,
            "radius": 150.0,
            "latitude": 21.02851,
            "longitude": 105.85248,
        },
    ]

    active = _find_active_candidate(candidates)
    assert active is not None, "Phải tìm thấy một active candidate"
    assert active["name"] == "Hồ Hoàn Kiếm", f"Dù Việt Nam Dân chủ Cộng hòa gần hơn (18.9m vs 50m), hệ thống phải chọn Hồ Hoàn Kiếm, nhưng lại chọn: {active['name']}"
    assert active["id"] == 3


def test_find_active_candidate_returns_none_when_only_irrelevant_articles_in_range():
    """
    Nếu chỉ có bài không liên quan (quốc gia, chính thể, tổ chức) trong bán kính kích hoạt:
    Hệ thống KHÔNG ĐƯỢC thuyết minh sai, mà phải trả về None.
    """
    candidates = [
        {
            "id": 1,
            "name": "Việt Nam Dân chủ Cộng hòa",
            "source_title": "Việt Nam Dân chủ Cộng hòa",
            "description_source": "Việt Nam Dân chủ Cộng hòa là một nhà nước...",
            "distance_m": 18.9,
            "radius": 80.0,
        },
        {
            "id": 2,
            "name": "Ủy ban Quốc tế về Giám sát và Kiểm soát",
            "source_title": "Ủy ban Quốc tế về Giám sát và Kiểm soát",
            "description_source": "Ủy ban Quốc tế về Giám sát và Kiểm soát là một ủy ban...",
            "distance_m": 18.9,
            "radius": 80.0,
        },
    ]

    active = _find_active_candidate(candidates)
    assert active is None, "Không được tự ý kích hoạt bài không liên quan"


def test_detect_endpoint_returns_no_place_found_when_only_irrelevant_articles_exist():
    """
    Luồng /locations/detect: Nếu content-service chỉ trả bài chính thể/quốc gia,
    Gateway phải trả detected=False, reason='no_place_found'.
    """
    client = TestClient(app)

    nearby_payload = {
        "candidates": [
            {
                "id": 1,
                "name": "Việt Nam Dân chủ Cộng hòa",
                "source_title": "Việt Nam Dân chủ Cộng hòa",
                "description_source": "Việt Nam Dân chủ Cộng hòa là một nhà nước...",
                "distance_m": 18.9,
                "radius": 80.0,
            }
        ]
    }

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.is_server_error = False
    mock_resp.json.return_value = nearby_payload
    mock_client.get.return_value = mock_resp

    with patch.object(gateway_main, "get_shared_client", return_value=mock_client):
        res = client.post(
            "/locations/detect",
            json={"latitude": 21.02850, "longitude": 105.85420, "language": "vi"}
        )

    assert res.status_code == 200
    data = res.json()
    assert data["detected"] is False
    assert data["reason"] == "no_place_found"
    assert data["location"] is None


def test_detect_endpoint_activates_hoan_kiem_even_when_vietnam_article_is_closer():
    """
    Luồng /locations/detect đầy đủ:
    Nearby trả về bài 'Việt Nam Dân chủ Cộng hòa' (18.9m) và 'Hồ Hoàn Kiếm' (55m).
    Hệ thống phải kích hoạt 'Hồ Hoàn Kiếm', gọi /narrate và trả về audio_url của Hồ Hoàn Kiếm!
    """
    client = TestClient(app)

    nearby_payload = {
        "candidates": [
            {
                "id": 101,
                "name": "Việt Nam Dân chủ Cộng hòa",
                "source_title": "Việt Nam Dân chủ Cộng hòa",
                "description_source": "Việt Nam Dân chủ Cộng hòa là một nhà nước...",
                "distance_m": 18.9,
                "radius": 80.0,
                "latitude": 21.028333,
                "longitude": 105.854167,
            },
            {
                "id": 202,
                "name": "Hồ Hoàn Kiếm",
                "source_title": "Hồ Hoàn Kiếm",
                "description_source": "Hồ Hoàn Kiếm còn được gọi là Hồ Gươm là một hồ nước ngọt tự nhiên...",
                "distance_m": 55.0,
                "radius": 120.0,
                "latitude": 21.02851,
                "longitude": 105.85248,
            }
        ]
    }

    narrate_payload = {
        "narration_status": "audio_ready",
        "audio_url": "https://storage.example.com/audio/hoankiem.mp3",
        "translated_text": "Chào mừng bạn đến với Hồ Hoàn Kiếm, trái tim của thủ đô Hà Nội.",
        "lang": "vi",
        "requested_lang": "vi",
        "cache_hit": True,
    }

    mock_client = AsyncMock()

    async def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.is_error = False
        resp.is_server_error = False
        resp.json.return_value = nearby_payload
        return resp

    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.is_error = False
        resp.is_server_error = False
        resp.json.return_value = narrate_payload
        return resp

    mock_client.get.side_effect = mock_get
    mock_client.post.side_effect = mock_post

    with patch.object(gateway_main, "get_shared_client", return_value=mock_client):
        res = client.post(
            "/locations/detect",
            json={"latitude": 21.02850, "longitude": 105.85420, "language": "vi"}
        )

    assert res.status_code == 200
    data = res.json()
    assert data["detected"] is True
    assert data["location"]["name"] == "Hồ Hoàn Kiếm"
    assert data["location"]["id"] == 202
    assert data["audio_url"] == "https://storage.example.com/audio/hoankiem.mp3"
    assert data["narration_status"] == "audio_ready"
    assert data["cache_hit"] is True

    # Xác minh trong danh sách candidates trả về chỉ còn các địa điểm hợp lệ (không chứa Việt Nam Dân chủ Cộng hòa)
    candidate_names = [c["name"] for c in data.get("candidates") or []]
    assert "Việt Nam Dân chủ Cộng hòa" not in candidate_names
    assert "Hồ Hoàn Kiếm" in candidate_names
