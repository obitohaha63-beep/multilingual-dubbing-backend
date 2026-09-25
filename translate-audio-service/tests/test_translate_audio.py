"""
Unit tests cho translate-audio-service.

Dùng FastAPI TestClient (chạy app trong bộ nhớ, không cần server thật)
kết hợp với pytest.  Chạy bằng lệnh:

    cd translate-audio-service
    pytest tests/

Lưu ý: TestClient yêu cầu package `httpx` (đã có trong requirements.txt).
"""

import sys
import os

# Đảm bảo Python tìm thấy main.py ở thư mục cha (translate-audio-service/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------- Tests cho GET /health ----------

def test_health_returns_ok():
    """GET /health phải trả về status ok và đúng tên service."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "translate-audio-service"


# ---------- Tests cho POST /translate ----------

def test_translate_returns_200():
    """POST /translate với payload hợp lệ phải trả về HTTP 200."""
    response = client.post(
        "/translate",
        json={"text": "Chào buổi sáng", "target_lang": "en"},
    )
    assert response.status_code == 200


def test_translate_response_has_translated_text_field():
    """Response phải có field 'translated_text'."""
    response = client.post(
        "/translate",
        json={"text": "Xin chào", "target_lang": "ja"},
    )
    assert "translated_text" in response.json()


def test_translate_mock_contains_original_text():
    """
    Bản dịch giả lập hiện tại nhúng văn bản gốc vào kết quả.
    Test này sẽ được xóa / cập nhật khi tích hợp engine dịch thật.
    """
    original = "Hồ Hoàn Kiếm là hồ đẹp nhất Hà Nội"
    response = client.post(
        "/translate",
        json={"text": original, "target_lang": "en"},
    )
    data = response.json()
    # Giả lập trả về "[EN] <text gốc>"
    assert original in data["translated_text"]


def test_translate_mock_contains_uppercased_lang_code():
    """Bản dịch giả lập chứa mã ngôn ngữ đích viết hoa."""
    response = client.post(
        "/translate",
        json={"text": "Xin chào", "target_lang": "ko"},
    )
    data = response.json()
    assert "KO" in data["translated_text"]


def test_translate_missing_text_returns_422():
    """Thiếu field 'text' phải trả về HTTP 422 Unprocessable Entity."""
    response = client.post(
        "/translate",
        json={"target_lang": "en"},  # thiếu 'text'
    )
    assert response.status_code == 422


def test_translate_missing_target_lang_returns_422():
    """Thiếu field 'target_lang' phải trả về HTTP 422 Unprocessable Entity."""
    response = client.post(
        "/translate",
        json={"text": "Xin chào"},  # thiếu 'target_lang'
    )
    assert response.status_code == 422


def test_translate_empty_body_returns_422():
    """Body rỗng phải trả về HTTP 422 Unprocessable Entity."""
    response = client.post("/translate", json={})
    assert response.status_code == 422


def test_translate_multiple_languages():
    """
    Mô phỏng cách content-service gọi translate-audio-service:
    lấy target_languages "en,ja,ko" rồi gọi endpoint lần lượt cho từng ngôn ngữ.
    """
    description_vi = "Chợ Bến Thành là biểu tượng của thành phố Hồ Chí Minh"
    target_languages = "en,ja,ko".split(",")

    for lang in target_languages:
        response = client.post(
            "/translate",
            json={"text": description_vi, "target_lang": lang},
        )
        assert response.status_code == 200
        assert "translated_text" in response.json()
