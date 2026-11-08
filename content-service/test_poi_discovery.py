"""
Bài test cho content-service kiểm tra:
1. wikimedia_discover lọc bỏ bài chính thể/quốc gia 'Việt Nam Dân chủ Cộng hòa' và xếp hạng POI chuẩn.
2. /locations/nearby bỏ qua các record phi địa danh đã lưu trong database.
3. Tối ưu HTTP request thành 1 lượt (generator=geosearch).
"""
import importlib.util
from pathlib import Path
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

CONTENT_DIR = Path(__file__).resolve().parent

if str(CONTENT_DIR) not in sys.path:
    sys.path.insert(0, str(CONTENT_DIR))

spec = importlib.util.spec_from_file_location("content_main", CONTENT_DIR / "main.py")
content_main = importlib.util.module_from_spec(spec)
sys.modules["content_main"] = content_main
spec.loader.exec_module(content_main)

wikimedia_discover = content_main.wikimedia_discover
_wiki_cache = content_main._wiki_cache
_upsert_wikimedia_location = content_main._upsert_wikimedia_location
is_valid_travel_poi = content_main.is_valid_travel_poi
rank_candidates = content_main.rank_candidates


def test_wikimedia_discover_filters_out_vietnam_democratic_republic():
    """
    Giả lập Wikimedia API trả về cả 'Việt Nam Dân chủ Cộng hòa' (18.9m) và 'Đền Ngọc Sơn' (314m).
    wikimedia_discover phải loại 'Việt Nam Dân chủ Cộng hòa' và chỉ giữ 'Đền Ngọc Sơn'.
    """
    _wiki_cache.clear()

    mock_pages = {
        "3366": {
            "pageid": 3366,
            "title": "Việt Nam Dân chủ Cộng hòa",
            "extract": "Việt Nam Dân chủ Cộng hòa là một nhà nước tồn tại từ năm 1945 đến năm 1976...",
            "fullurl": "https://vi.wikipedia.org/wiki/Vi%E1%BB%87t_Nam_D%C3%A2n_ch%E1%BB%A7_C%E1%BB%99ng_h%C3%B2a",
            "coordinates": [{"lat": 21.028333, "lon": 105.854167}],
            "categories": [{"title": "Thể loại:Quốc gia trong lịch sử Việt Nam"}],
        },
        "25334": {
            "pageid": 25334,
            "title": "Đền Ngọc Sơn",
            "extract": "Đền Ngọc Sơn là một ngôi đền thờ nằm trên đảo Ngọc của hồ Hoàn Kiếm, Hà Nội.",
            "fullurl": "https://vi.wikipedia.org/wiki/%C4%90%E1%BB%81n_Ng%E1%BB%8Dc_S%C6%A1n",
            "coordinates": [{"lat": 21.030722, "lon": 105.852333}],
            "categories": [{"title": "Thể loại:Di tích tại Hà Nội"}],
        },
    }

    mock_response = {"query": {"pages": mock_pages}}

    with patch.object(content_main, "_wiki_request", return_value=mock_response) as mock_req:
        candidates, cache_hit = wikimedia_discover(21.02850, 105.85420, 500.0, 5)

    assert mock_req.call_count == 1, "Chỉ được gọi 1 request HTTP duy nhất tới Wikimedia (generator=geosearch)"
    assert not cache_hit

    titles = [c["source_title"] for c in candidates]
    assert "Việt Nam Dân chủ Cộng hòa" not in titles, "Bài Việt Nam Dân chủ Cộng hòa phải bị loại bỏ!"
    assert "Đền Ngọc Sơn" in titles, "Bài Đền Ngọc Sơn phải được giữ lại!"
    assert candidates[0]["source_title"] == "Đền Ngọc Sơn"


def test_upsert_wikimedia_location_ignores_invalid_poi():
    """
    Nếu item là bài chính thể/quốc gia, _upsert_wikimedia_location trả về None và không lưu DB.
    """
    mock_db = MagicMock()
    item = {
        "source_page_id": 3366,
        "source_title": "Việt Nam Dân chủ Cộng hòa",
        "description_source": "Việt Nam Dân chủ Cộng hòa là một nhà nước tồn tại từ năm 1945...",
        "source_url": "https://vi.wikipedia.org/wiki/VN",
        "latitude": 21.028333,
        "longitude": 105.854167,
        "last_synced_at": datetime.now(timezone.utc),
    }

    res = _upsert_wikimedia_location(mock_db, item)
    assert res is None, "Không được lưu bài phi địa danh vào DB"
    assert not mock_db.add.called
