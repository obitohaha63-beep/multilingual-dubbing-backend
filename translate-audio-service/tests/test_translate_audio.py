"""
Unit tests cho translate-audio-service — Tuan 3 / 4 / 5.

Chay bang lenh (tu thu muc translate-audio-service/):
    pytest tests/test_translate_audio.py -v

Khong goi Wikimedia that / Edge TTS that / content-service that trong unit test:
dung unittest.mock.patch va respx (hoac unittest.mock) de mock toan bo upstream.

Test coverage:
  - GET /health
  - POST /translate (legacy tuan 2, giu nguyen)
  - POST /localized-content:
      * nguon vi -> en (co langlink)
      * nguon vi -> ja (co langlink)
      * cung ngon ngu (vi -> vi)
      * thieu langlink -> found=false, reason=target_language_unavailable
      * extract rong -> found=false, reason=empty_extract
      * redirect / tieu de Unicode
      * ngon ngu khong duoc ho tro -> 422
      * 429 (rate_limited) -> 503
      * timeout -> 503
  - POST /text-to-speech: (Tuan 4)
      * tao MP3 thanh cong, cache hit, file mat, concurrent, timeout 503, lang 422
  - POST /narrate: (Tuan 5)
      * cache miss vi/en/ja thanh cong (audio_ready)
      * cache miss ja khong co langlink -> fallback vi, is_fallback=true
      * cache hit audio_ready (file OK) -> khong goi Wikimedia/Edge
      * cache hit audio_ready (file mat) -> tai tao TTS
      * cache hit text_ready -> retry TTS thanh cong -> audio_ready
      * TTS loi sau text_ready -> tra 200 text_ready, audio_url=null
      * content-service timeout -> 503 cache_service_unavailable
      * PUT upsert loi -> 503 cache_upsert_failed
      * content-service tra 404 -> 404 location_not_found
      * concurrent cung key -> lock chong race condition
      * audio_url la duong dan /audio/, metadata day du
      * ngon ngu khong ho tro -> 422
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app, MAX_CHARS, CONTENT_SERVICE_URL

client = TestClient(app)


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

VALID_PAYLOAD = {
    "source_title": "Ha Noi",
    "source_lang": "vi",
    "target_lang": "en",
    "source_page_id": "12345",
}


def _mock_langlink(title: str):
    """Tra ve mock ham get_langlink -> title."""
    return MagicMock(return_value=title)


def _mock_extract(title: str, extract: str, url: str = "https://en.wikipedia.org/wiki/Hanoi"):
    """Tra ve mock ham get_extract_by_title hoac get_extract_by_pageid."""
    return MagicMock(return_value={"title": title, "extract": extract, "url": url})


# ===========================================================================
# GET /health
# ===========================================================================


def test_health_returns_ok():
    """GET /health phai tra ve status ok va dung ten service."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "translate-audio-service"


# ===========================================================================
# POST /translate (Legacy tuan 2 — giu nguyen, khong sua)
# ===========================================================================


def test_translate_returns_200():
    resp = client.post("/translate", json={"text": "Chao buoi sang", "target_lang": "en"})
    assert resp.status_code == 200


def test_translate_response_has_translated_text_field():
    resp = client.post("/translate", json={"text": "Xin chao", "target_lang": "ja"})
    assert "translated_text" in resp.json()


def test_translate_mock_contains_original_text():
    original = "Ho Hoan Kiem la ho dep nhat Ha Noi"
    resp = client.post("/translate", json={"text": original, "target_lang": "en"})
    assert original in resp.json()["translated_text"]


def test_translate_mock_contains_uppercased_lang_code():
    resp = client.post("/translate", json={"text": "Xin chao", "target_lang": "ko"})
    assert "KO" in resp.json()["translated_text"]


def test_translate_missing_text_returns_422():
    resp = client.post("/translate", json={"target_lang": "en"})
    assert resp.status_code == 422


def test_translate_missing_target_lang_returns_422():
    resp = client.post("/translate", json={"text": "Xin chao"})
    assert resp.status_code == 422


def test_translate_empty_body_returns_422():
    resp = client.post("/translate", json={})
    assert resp.status_code == 422


def test_translate_multiple_languages():
    """Mo phong cach content-service goi translate lien tiep."""
    text = "Cho Ben Thanh la bieu tuong cua thanh pho Ho Chi Minh"
    for lang in ["en", "ja"]:
        resp = client.post("/translate", json={"text": text, "target_lang": lang})
        assert resp.status_code == 200
        assert "translated_text" in resp.json()


# ===========================================================================
# POST /localized-content — validate dau vao
# ===========================================================================


def test_localized_content_unsupported_source_lang_returns_422():
    """source_lang khong trong allowlist -> 422."""
    payload = {**VALID_PAYLOAD, "source_lang": "zh"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_unsupported_target_lang_returns_422():
    """target_lang khong trong allowlist -> 422."""
    payload = {**VALID_PAYLOAD, "target_lang": "ko"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_invalid_page_id_returns_422():
    """source_page_id khong phai so -> 422."""
    payload = {**VALID_PAYLOAD, "source_page_id": "abc"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_blank_title_returns_422():
    """source_title rong -> 422."""
    payload = {**VALID_PAYLOAD, "source_title": "   "}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 422


def test_localized_content_missing_fields_returns_422():
    """Thieu truong bat buoc -> 422."""
    resp = client.post("/localized-content", json={"source_title": "Ha Noi"})
    assert resp.status_code == 422


# ===========================================================================
# POST /localized-content — vi -> en (co langlink, co extract)
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_vi_to_en_success(mock_langlink, mock_extract):
    """vi -> en: co langlink va extract -> found=true, lang='en'."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "Hanoi is the capital of Vietnam. It has many lakes.",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "en"
    assert data["requested_lang"] == "en"
    assert data["target_lang"] == "en"
    assert data["target_title"] == "Hanoi"
    assert "Hanoi" in data["text"]
    assert data["source_url"] == "https://en.wikipedia.org/wiki/Hanoi"
    assert data["is_fallback"] is False
    assert data["reason"] is None

    # Kiem tra mock duoc goi dung tham so
    mock_langlink.assert_called_once()
    mock_extract.assert_called_once()


# ===========================================================================
# POST /localized-content — vi -> ja
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_vi_to_ja_success(mock_langlink, mock_extract):
    """vi -> ja: co langlink va extract -> found=true, lang='ja'."""
    mock_langlink.return_value = "ハノイ"
    mock_extract.return_value = {
        "title": "ハノイ",
        "extract": "ハノイはベトナムの首都である。",
        "url": "https://ja.wikipedia.org/wiki/%E3%83%8F%E3%83%8E%E3%82%A4",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "ja"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "ja"
    assert data["target_title"] == "ハノイ"
    assert "ハノイ" in data["text"]


# ===========================================================================
# POST /localized-content — cung ngon ngu (vi -> vi)
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_vi(mock_pageid):
    """vi -> vi: lay extract tu wiki nguon, lang='vi'."""
    mock_pageid.return_value = {
        "title": "Ha Noi",
        "extract": "Ha Noi la thu do nuoc Viet Nam.",
        "url": "https://vi.wikipedia.org/wiki/H%C3%A0_N%E1%BB%99i",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "vi"
    assert data["target_title"] == "Ha Noi"
    mock_pageid.assert_called_once()


# ===========================================================================
# POST /localized-content — thieu langlink
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_missing_langlink(mock_langlink):
    """Khong co langlink sang target_lang -> found=false, reason=target_language_unavailable."""
    mock_langlink.return_value = None

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is False
    assert data["reason"] == "target_language_unavailable"
    assert data["lang"] is None
    assert data["target_title"] is None
    assert data["text"] is None
    assert data["source_url"] is None
    assert data["is_fallback"] is False


# ===========================================================================
# POST /localized-content — extract rong
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_empty_extract(mock_langlink, mock_extract):
    """Co langlink nhung extract rong -> found=false, reason=empty_extract."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is False
    assert data["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — whitespace-only extract
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_whitespace_only_extract(mock_langlink, mock_extract):
    """Extract chi co whitespace -> found=false, reason=empty_extract."""
    mock_langlink.return_value = "Hanoi"
    mock_extract.return_value = {
        "title": "Hanoi",
        "extract": "   \n\n   ",
        "url": "https://en.wikipedia.org/wiki/Hanoi",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — redirect / Unicode title
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_unicode_title_redirect(mock_langlink, mock_extract):
    """Title Unicode sau redirect: API tra ve title chuan hoa, service dung nguyen."""
    mock_langlink.return_value = "Ho Chi Minh City"
    # API tra ve title chuan hoa sau redirect (vi du: "Ho Chi Minh City" -> "Ho Chi Minh City")
    mock_extract.return_value = {
        "title": "Ho Chi Minh City",
        "extract": "Ho Chi Minh City, commonly known as Saigon, is the largest city in Vietnam.",
        "url": "https://en.wikipedia.org/wiki/Ho_Chi_Minh_City",
    }

    payload = {**VALID_PAYLOAD, "source_title": "Thanh pho Ho Chi Minh", "source_page_id": "99999"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["target_title"] == "Ho Chi Minh City"
    assert "Saigon" in data["text"]


# ===========================================================================
# POST /localized-content — cat text tai ranh gioi cau
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_text_truncated_at_sentence(mock_langlink, mock_extract):
    """Text dai hon MAX_CHARS phai bi cat tai dau cham cau."""
    # Tao text dai hon 1200 ky tu
    long_text = "This is a sentence. " * 100  # ~2000 ky tu
    mock_langlink.return_value = "SomePage"
    mock_extract.return_value = {
        "title": "SomePage",
        "extract": long_text,
        "url": "https://en.wikipedia.org/wiki/SomePage",
    }

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert len(data["text"]) <= MAX_CHARS


# ===========================================================================
# POST /localized-content — 429 (rate_limited) -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_429_returns_503(mock_langlink):
    """WikimediaError(rate_limited) -> HTTP 503 voi detail.code."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "rate_limited", "Wikimedia tra ve 429"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "rate_limited"
    assert "message" in data["detail"]


# ===========================================================================
# POST /localized-content — timeout -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_timeout_returns_503(mock_langlink):
    """WikimediaError(timeout) -> HTTP 503."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "timeout", "Wikimedia API khong phan hoi kip thoi"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "timeout"


# ===========================================================================
# POST /localized-content — upstream_error -> 503
# ===========================================================================


@patch("wikimedia_client.get_langlink")
def test_localized_upstream_error_returns_503(mock_langlink):
    """WikimediaError(upstream_error) -> HTTP 503, khong gia vo khong co ngu."""
    from wikimedia_client import WikimediaError

    mock_langlink.side_effect = WikimediaError(
        "upstream_error", "Wikimedia tra ve loi HTTP 500"
    )

    resp = client.post("/localized-content", json=VALID_PAYLOAD)
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "upstream_error"


# ===========================================================================
# POST /localized-content — cung ngon ngu, extract rong
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_empty_extract(mock_pageid):
    """Cung ngon ngu nhung extract rong -> found=false, reason=empty_extract."""
    mock_pageid.return_value = {
        "title": "Ha Noi",
        "extract": "",
        "url": "https://vi.wikipedia.org/wiki/Ha_Noi",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["reason"] == "empty_extract"


# ===========================================================================
# POST /localized-content — cung ngon ngu, page khong tim thay
# ===========================================================================


@patch("wikimedia_client.get_extract_by_pageid")
def test_localized_same_lang_page_not_found(mock_pageid):
    """Cung ngon ngu nhung API tra {} (page khong ton tai) -> found=false."""
    mock_pageid.return_value = {}

    payload = {**VALID_PAYLOAD, "target_lang": "vi", "source_lang": "vi"}
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["reason"] == "target_language_unavailable"


# ===========================================================================
# POST /localized-content — kiem tra lang dung, khong gan nhan sai
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_lang_field_matches_actual_language(mock_langlink, mock_extract):
    """lang phai la ngon ngu that cua text — khong gan 'en' cho text tieng Viet."""
    mock_langlink.return_value = "ハノイ"
    mock_extract.return_value = {
        "title": "ハノイ",
        "extract": "ハノイはベトナムの首都である。",
        "url": "https://ja.wikipedia.org/wiki/Hanoi",
    }

    payload = {**VALID_PAYLOAD, "target_lang": "ja"}
    resp = client.post("/localized-content", json=payload)
    data = resp.json()

    # lang phai la 'ja', khong phai 'vi' hay 'en'
    assert data["lang"] == "ja"
    assert data["lang"] != "vi"
    assert data["lang"] != "en"


# ===========================================================================
# POST /localized-content — en -> vi
# ===========================================================================


@patch("wikimedia_client.get_extract_by_title")
@patch("wikimedia_client.get_langlink")
def test_localized_en_to_vi_success(mock_langlink, mock_extract):
    """en -> vi: co langlink, extract tieng Viet, lang='vi'."""
    mock_langlink.return_value = "Ha Noi"
    mock_extract.return_value = {
        "title": "Ha Noi",
        "extract": "Ha Noi la thu do nuoc Cong hoa xa hoi chu nghia Viet Nam.",
        "url": "https://vi.wikipedia.org/wiki/Ha_Noi",
    }

    payload = {
        "source_title": "Hanoi",
        "source_lang": "en",
        "target_lang": "vi",
        "source_page_id": "54321",
    }
    resp = client.post("/localized-content", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["found"] is True
    assert data["lang"] == "vi"
    # Khong duoc gan nhan "en" cho text tieng Viet
    assert data["lang"] != "en"


# ===========================================================================
# TUẦN 4 — UNIT TESTS CHO EDGE TTS & AUDIO CACHE (MOCK TOÀN BỘ)
# Không gọi Microsoft Edge hoặc Wikimedia thật trong pytest/CI.
# ===========================================================================

import asyncio
import httpx
import tts


class MockEdgeCommunicate:
    """Mock edge_tts.Communicate để tạo file MP3 giả lập trong unit tests."""

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def save(self, filepath: str):
        with open(filepath, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00\x10MOCK_MP3_AUDIO_PAYLOAD")


def test_tts_create_mp3_success(tmp_path, monkeypatch):
    """Tạo MP3 thành công: trả 200, cache_hit=False, file tồn tại trên đĩa."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)
    monkeypatch.setattr("edge_tts.Communicate", MockEdgeCommunicate)

    req_body = {
        "text": "Nhà thờ Đức Bà là công trình kiến trúc nổi tiếng tại TP.HCM.",
        "lang": "vi",
    }
    resp = client.post("/text-to-speech", json=req_body)
    assert resp.status_code == 200
    data = resp.json()

    assert data["lang"] == "vi"
    assert data["voice"] == "vi-VN-HoaiMyNeural"
    assert len(data["text_hash"]) == 64
    assert data["audio_filename"].endswith(".mp3")
    assert data["audio_url"] == f"/audio/{data['audio_filename']}"
    assert data["cache_hit"] is False

    # Kiểm tra file vật lý tồn tại và có kích thước > 0
    file_on_disk = tmp_path / data["audio_filename"]
    assert file_on_disk.is_file()
    assert file_on_disk.stat().st_size > 0


def test_tts_cache_hit_second_request(tmp_path, monkeypatch):
    """File hit: gọi lần hai với cùng nội dung trả cache_hit=True, không gọi lại TTS."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    call_count = 0

    class CountingMockCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            nonlocal call_count
            call_count += 1
            with open(filepath, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x00\x10MOCK_MP3_AUDIO_PAYLOAD")

    monkeypatch.setattr("edge_tts.Communicate", CountingMockCommunicate)

    req_body = {"text": "Chợ Bến Thành là chợ lịch sử.", "lang": "vi"}

    # Lần 1: cache miss
    resp1 = client.post("/text-to-speech", json=req_body)
    assert resp1.status_code == 200
    assert resp1.json()["cache_hit"] is False
    assert call_count == 1

    # Lần 2: cache hit
    resp2 = client.post("/text-to-speech", json=req_body)
    assert resp2.status_code == 200
    assert resp2.json()["cache_hit"] is True
    assert resp2.json()["audio_filename"] == resp1.json()["audio_filename"]
    # Mock save không được gọi lần 2
    assert call_count == 1


def test_tts_file_lost_regenerates_no_fake_cache_hit(tmp_path, monkeypatch):
    """File mất: xóa file vật lý -> gọi lại phải tạo lại, KHÔNG trả cache_hit giả."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    call_count = 0

    class CountingMockCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            nonlocal call_count
            call_count += 1
            with open(filepath, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x00\x10MOCK_MP3_AUDIO_PAYLOAD")

    monkeypatch.setattr("edge_tts.Communicate", CountingMockCommunicate)

    req_body = {"text": "Hồ Hoàn Kiếm nằm ở trung tâm Hà Nội.", "lang": "vi"}

    # Lần 1: tạo file thành công
    resp1 = client.post("/text-to-speech", json=req_body)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["cache_hit"] is False
    assert call_count == 1

    file_path = tmp_path / data1["audio_filename"]
    assert file_path.is_file()

    # Giả lập file bị xóa / mất trên đĩa
    file_path.unlink()
    assert not file_path.is_file()

    # Lần 2: hệ thống PHẢI phát hiện file mất và tạo lại, cache_hit=False
    resp2 = client.post("/text-to-speech", json=req_body)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["cache_hit"] is False  # Không được trả cache_hit giả!
    assert call_count == 2
    assert file_path.is_file()
    assert file_path.stat().st_size > 0


@pytest.mark.anyio
async def test_tts_concurrent_requests_same_key(tmp_path, monkeypatch):
    """Hai request đồng thời cùng 1 key: khóa theo key ngăn gọi TTS trùng lặp."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    call_count = 0

    class SlowMockCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            nonlocal call_count
            call_count += 1
            # Delay nhẹ để mô phỏng thời gian mạng
            await asyncio.sleep(0.05)
            with open(filepath, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x00\x10MOCK_MP3_AUDIO_PAYLOAD")

    monkeypatch.setattr("edge_tts.Communicate", SlowMockCommunicate)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        req_body = {"text": "Chùa Một Cột được xây dựng từ thời Lý.", "lang": "vi"}
        r1, r2 = await asyncio.gather(
            ac.post("/text-to-speech", json=req_body),
            ac.post("/text-to-speech", json=req_body),
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    d1 = r1.json()
    d2 = r2.json()

    # Cùng ra tên file
    assert d1["audio_filename"] == d2["audio_filename"]
    # Mock save chỉ được gọi đúng 1 lần duy nhất nhờ khóa đồng thời
    assert call_count == 1
    # Một request tạo mới, một request hit cache
    results = [d1["cache_hit"], d2["cache_hit"]]
    assert False in results
    assert True in results


def test_tts_timeout_returns_503(tmp_path, monkeypatch):
    """Timeout khi gọi TTS: trả 503 detail.code='tts_unavailable', không crash server."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr(tts, "TTS_TIMEOUT_SECONDS", 0.05)
    tts.ensure_audio_dir(tmp_path)

    class TimeoutMockCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            await asyncio.sleep(0.3)

    monkeypatch.setattr("edge_tts.Communicate", TimeoutMockCommunicate)

    resp = client.post(
        "/text-to-speech",
        json={"text": "Văn bản kiểm tra quá hạn phản hồi", "lang": "vi"},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert "detail" in data
    assert data["detail"]["code"] == "tts_unavailable"

    # Đảm bảo không để lại file tạm rác
    assert len(list(tmp_path.glob("*.tmp"))) == 0


def test_tts_unsupported_language_returns_422():
    """Ngôn ngữ ngoài allowlist trả về HTTP 422."""
    for unsupported in ["fr", "zh", "ko", "de", "es"]:
        resp = client.post(
            "/text-to-speech",
            json={"text": "Hello world", "lang": unsupported},
        )
        assert resp.status_code == 422


def test_tts_text_validation_limits():
    """Kiểm tra validation độ dài và nội dung text: rỗng -> 422, >1200 ký tự -> 422."""
    # Text rỗng
    resp_empty = client.post("/text-to-speech", json={"text": "   ", "lang": "vi"})
    assert resp_empty.status_code == 422

    # Text quá 1200 ký tự
    long_text = "A" * 1201
    resp_long = client.post("/text-to-speech", json={"text": long_text, "lang": "vi"})
    assert resp_long.status_code == 422

    # Thiếu trường
    resp_missing = client.post("/text-to-speech", json={"lang": "vi"})
    assert resp_missing.status_code == 422


def test_audio_status_valid_file_and_missing(tmp_path, monkeypatch):
    """GET /audio/{filename}/status: file có trả exists=true, file thiếu trả exists=false."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    valid_filename = "abc123def456.mp3"
    # Khi file chưa tồn tại -> exists=false (HTTP 200)
    resp_missing = client.get(f"/audio/{valid_filename}/status")
    assert resp_missing.status_code == 200
    assert resp_missing.json() == {"exists": False}

    # Tạo file thực tế
    file_path = tmp_path / valid_filename
    with open(file_path, "wb") as f:
        f.write(b"AUDIO_DATA")

    # Khi file đã tồn tại -> exists=true
    resp_exists = client.get(f"/audio/{valid_filename}/status")
    assert resp_exists.status_code == 200
    assert resp_exists.json() == {"exists": True}


def test_audio_status_malicious_filename_rejected(tmp_path, monkeypatch):
    """GET /audio/{filename}/status: ngăn chặn path traversal và tên file độc hại."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    malicious_inputs = [
        "../../etc/passwd.mp3",
        "..%2F..%2Fetc%2Fpasswd.mp3",
        "evil_script.sh",
        "some_doc.txt",
        "../secret.mp3",
        "sub/folder.mp3",
        "a" * 125 + ".mp3",
    ]

    for bad_name in malicious_inputs:
        resp = client.get(f"/audio/{bad_name}/status")
        # Phải bị từ chối 422 hoặc 404
        assert resp.status_code in (422, 404)


def test_audio_static_serving(tmp_path, monkeypatch):
    """Kiểm tra phục vụ StaticFiles /audio: tải đúng nội dung file MP3."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    test_content = b"ID3\x03REAL_VERIFIED_AUDIO_CONTENT"

    class CustomMockCommunicate:
        def __init__(self, text: str, voice: str):
            pass

        async def save(self, filepath: str):
            with open(filepath, "wb") as f:
                f.write(test_content)

    monkeypatch.setattr("edge_tts.Communicate", CustomMockCommunicate)

    # Tạo audio qua API
    create_resp = client.post(
        "/text-to-speech",
        json={"text": "Kiểm tra phục vụ tệp tĩnh audio", "lang": "vi"},
    )
    assert create_resp.status_code == 200
    audio_filename = create_resp.json()["audio_filename"]
    audio_url = create_resp.json()["audio_url"]

    # Đọc status route trước
    status_resp = client.get(f"/audio/{audio_filename}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["exists"] is True

    # Tải file tĩnh qua /audio/{filename}
    static_resp = client.get(audio_url)
    assert static_resp.status_code == 200
    assert "audio/mpeg" in static_resp.headers.get("content-type", "")
    assert static_resp.content == test_content

    # File không tồn tại trả 404
    missing_resp = client.get("/audio/nonexistent_hash_123.mp3")
    assert missing_resp.status_code == 404


def test_tts_generic_error_returns_503(tmp_path, monkeypatch):
    """Khi Edge TTS ném ngoại lệ bất kỳ (lỗi mạng/websocket): trả 503 code='tts_unavailable'."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    class FailingMockCommunicate:
        def __init__(self, text: str, voice: str):
            pass

        async def save(self, filepath: str):
            raise ConnectionError("Mất kết nối tới máy chủ Edge WebSocket")

    monkeypatch.setattr("edge_tts.Communicate", FailingMockCommunicate)

    resp = client.post(
        "/text-to-speech",
        json={"text": "Văn bản thử nghiệm lỗi kết nối", "lang": "vi"},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "tts_unavailable"


def test_tts_voice_override_via_env(tmp_path, monkeypatch):
    """Kiểm tra khả năng override voice qua biến môi trường."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)
    monkeypatch.setattr("edge_tts.Communicate", MockEdgeCommunicate)

    # Override giọng đọc tiếng Việt sang Nam Minh
    monkeypatch.setenv("TTS_VOICE_VI", "vi-VN-NamMinhNeural")

    resp = client.post(
        "/text-to-speech",
        json={"text": "Thử nghiệm override voice qua env", "lang": "vi"},
    )
    assert resp.status_code == 200
    assert resp.json()["voice"] == "vi-VN-NamMinhNeural"


# /narrate cũ chỉ chuyển tiếp sang Content Service; kiểm thử flow nằm ở Content.
NARRATE_PAYLOAD = {"location_id": 1, "source_title": "Chợ", "source_lang": "vi",
                   "target_lang": "en", "source_text": "Nội dung", "source_page_id": "123"}

@pytest.mark.parametrize("code", [200, 202, 404, 503])
def test_narrate_forwards_content_status_without_orchestration(code):
    response = httpx.Response(code, json={"narration_status": "processing"})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response) as post, \
         patch("tts.synthesize_speech", new_callable=AsyncMock) as speech:
        result = client.post("/narrate", json=NARRATE_PAYLOAD)
    assert result.status_code == code
    assert post.call_args.args[0] == f"{CONTENT_SERVICE_URL}/narrate"
    assert post.call_args.kwargs["json"] == {"location_id": 1, "target_lang": "en"}
    speech.assert_not_called()


def test_narrate_forward_network_failure():
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock,
               side_effect=httpx.ConnectError("offline")):
        assert client.post("/narrate", json=NARRATE_PAYLOAD).status_code == 503


def test_tts_explicit_voice_and_reject_wrong_language(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    monkeypatch.setenv("TTS_ALLOWED_VOICES_VI", "vi-VN-NamMinhNeural")
    with patch("tts.edge_tts.Communicate", MockEdgeCommunicate):
        result = client.post("/text-to-speech", json={"text": "Xin chào", "lang": "vi", "voice": "vi-VN-NamMinhNeural"})
        assert result.status_code == 200
        assert result.json()["voice"] == "vi-VN-NamMinhNeural"
        bad = client.post("/text-to-speech", json={"text": "Xin chào", "lang": "vi", "voice": "en-US-JennyNeural"})
        assert bad.status_code == 422


def test_tts_retries_on_no_audio_received_and_succeeds(tmp_path, monkeypatch):
    """Giả lập lần 1 gặp NoAudioReceived, lần 2 thành công -> retry tạo phiên mới và trả kết quả."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    import edge_tts.exceptions
    attempts = 0

    class RetryMockCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise edge_tts.exceptions.NoAudioReceived("No audio received from Edge TTS")
            with open(filepath, "wb") as f:
                f.write(b"ID3\x03\x00\x00\x00\x00\x00\x10MOCK_MP3_PAYLOAD")

    monkeypatch.setattr("edge_tts.Communicate", RetryMockCommunicate)

    resp = client.post(
        "/text-to-speech",
        json={"text": "Thử nghiệm retry khi gặp NoAudioReceived", "lang": "vi"},
    )
    assert resp.status_code == 200
    assert attempts == 2
    data = resp.json()
    assert data["cache_hit"] is False
    audio_path = tmp_path / data["audio_filename"]
    assert audio_path.is_file()
    assert audio_path.stat().st_size > 0


def test_tts_retries_exhausted_returns_503(tmp_path, monkeypatch):
    """Giả lập NoAudioReceived liên tục: retry tối đa 2 lần (tổng 3 lần) rồi trả HTTP 503."""
    monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
    tts.ensure_audio_dir(tmp_path)

    import edge_tts.exceptions
    attempts = 0

    class PersistentFailCommunicate:
        def __init__(self, text: str, voice: str):
            self.text = text
            self.voice = voice

        async def save(self, filepath: str):
            nonlocal attempts
            attempts += 1
            raise edge_tts.exceptions.NoAudioReceived("No audio received from Edge TTS")

    monkeypatch.setattr("edge_tts.Communicate", PersistentFailCommunicate)

    resp = client.post(
        "/text-to-speech",
        json={"text": "Thử nghiệm lỗi kéo dài", "lang": "vi"},
    )
    assert resp.status_code == 503
    # 1 lần đầu + 2 lần retry = 3 lần
    assert attempts == 3
    data = resp.json()
    assert data["detail"]["code"] == "tts_unavailable"
    assert "NoAudioReceived" in data["detail"]["message"]

