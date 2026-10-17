"""
Test cho content-service tuần 3.
Chạy: cd content-service && python -m pytest tests/ -v

Dùng DB SQLite tạm riêng cho test (không đụng vào content.db thật) bằng
cách set biến môi trường trước khi import main - xem fixture `client` bên dưới.
"""

import os
import sys
import importlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Mỗi test chạy trên 1 file DB tạm riêng biệt (xóa sau khi test xong),
    tránh các test ảnh hưởng lẫn nhau hoặc đụng vào content.db thật đang
    dùng để chạy demo.
    """
    db_file = tmp_path / "test_content.db"
    monkeypatch.chdir(tmp_path)

    import models as models_module
    import migrate as migrate_module
    import main as main_module

    for mod in (models_module, migrate_module, main_module):
        importlib.reload(mod)

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as c:
        yield c


# --------------------------------- CRUD cũ ---------------------------------


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"service": "content-service", "status": "ok"}


def test_crud_locations_basic(client):
    r = client.post(
        "/locations",
        json={
            "name": "Cho Ben Thanh",
            "description_vi": "Mo ta",
            "target_languages": "en,ja",
        },
    )
    assert r.status_code == 200
    loc_id = r.json()["id"]

    assert client.get("/locations").status_code == 200
    assert client.get(f"/locations/{loc_id}").status_code == 200

    r = client.put(
        f"/locations/{loc_id}",
        json={"name": "Moi", "description_vi": "Sua", "target_languages": "en"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Moi"

    assert client.get("/locations/999999").status_code == 404


# --------------------------------- DELETE -----------------------------------


def test_delete_location(client):
    r = client.post(
        "/locations",
        json={"name": "A", "description_vi": "x", "target_languages": "en"},
    )
    loc_id = r.json()["id"]

    r = client.delete(f"/locations/{loc_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    assert client.get(f"/locations/{loc_id}").status_code == 404
    assert client.delete(f"/locations/{loc_id}").status_code == 404
    assert client.delete("/locations/999999").status_code == 404


# ----------------------------- Validate tọa độ -------------------------------


@pytest.mark.parametrize(
    "lat,lon",
    [(999, 10), (10, 999), (-999, -999)],
)
def test_invalid_coordinates_rejected(client, lat, lon):
    # Lưu ý: NaN/Infinity không phải giá trị JSON hợp lệ nên client thật
    # (trình duyệt/app di động) không thể gửi lên được - validate_coords()
    # trong utils.py vẫn chặn chúng ở tầng code, nhưng không test được qua
    # đường JSON ở đây.
    r = client.post(
        "/locations",
        json={
            "name": "Bad",
            "description_vi": "x",
            "target_languages": "en",
            "latitude": lat,
            "longitude": lon,
        },
    )
    assert r.status_code == 422


# ------------------------------- Route ordering ------------------------------


def test_nearby_and_discover_not_captured_by_id_route(client):
    """
    /locations/nearby và /locations/discover phải KHÔNG bị route động
    /locations/{location_id} bắt nhầm (tức không được trả lỗi 422 do
    FastAPI cố convert "nearby"/"discover" thành int).
    """
    r = client.get("/locations/nearby", params={"latitude": 0, "longitude": 0})
    assert r.status_code == 200

    with patch("wikimedia_client.discover", return_value=([], False)):
        r = client.get(
            "/locations/discover", params={"latitude": 0, "longitude": 0}
        )
        assert r.status_code == 200


# ----------------------------------- Haversine --------------------------------


def test_haversine_known_distance():
    from utils import haversine_m

    # Khoảng cách Hà Nội - TP.HCM thực tế ~1140-1160km đường chim bay.
    d = haversine_m(21.0285, 105.8542, 10.7769, 106.7009)
    assert 1_100_000 < d < 1_200_000

    # Cùng 1 điểm -> khoảng cách = 0.
    assert haversine_m(10.0, 106.0, 10.0, 106.0) == pytest.approx(0, abs=1e-6)


def test_nearby_filters_by_max_distance(client):
    client.post(
        "/locations",
        json={
            "name": "Gan",
            "description_vi": "x",
            "target_languages": "en",
            "latitude": 10.7769,
            "longitude": 106.7009,
        },
    )
    client.post(
        "/locations",
        json={
            "name": "Xa",
            "description_vi": "x",
            "target_languages": "en",
            "latitude": 21.0285,
            "longitude": 105.8542,
        },
    )

    r = client.get(
        "/locations/nearby",
        params={"latitude": 10.7769, "longitude": 106.7009, "max_distance": 1000},
    )
    names = [c["name"] for c in r.json()["candidates"]]
    assert names == ["Gan"]


# ------------------------------- /locations/discover ---------------------------


def _fake_candidate(pageid=111, title="Cho Ben Thanh"):
    return {
        "source_page_id": pageid,
        "source_title": title,
        "description_source": "Mo ta tu Wikipedia",
        "source_url": f"https://vi.wikipedia.org/wiki/{title}",
        "latitude": 10.7721,
        "longitude": 106.698,
        "last_synced_at": datetime.now(timezone.utc),
    }


def test_discover_with_results_upserts_into_db(client):
    with patch(
        "wikimedia_client.discover", return_value=([_fake_candidate()], False)
    ):
        r = client.get(
            "/locations/discover", params={"latitude": 10.77, "longitude": 106.7}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["cache_hit"] is False
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["source"] == "wikimedia"

    # Gọi lại CRUD list -> phải thấy record vừa upsert.
    all_locations = client.get("/locations").json()
    assert any(loc["source_page_id"] == 111 for loc in all_locations)


def test_discover_duplicate_pageid_updates_not_duplicates(client):
    with patch(
        "wikimedia_client.discover", return_value=([_fake_candidate()], False)
    ):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    updated = _fake_candidate()
    updated["description_source"] = "Mo ta moi hon"
    with patch("wikimedia_client.discover", return_value=([updated], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    all_locations = client.get("/locations").json()
    matches = [loc for loc in all_locations if loc["source_page_id"] == 111]
    assert len(matches) == 1  # không bị tạo trùng
    assert matches[0]["description_source"] == "Mo ta moi hon"


def test_discover_empty_result(client):
    with patch("wikimedia_client.discover", return_value=([], True)):
        r = client.get(
            "/locations/discover", params={"latitude": 10.77, "longitude": 106.7}
        )
    assert r.status_code == 200
    assert r.json() == {"candidates": [], "cache_hit": True}


def test_discover_upstream_timeout_returns_503(client):
    import wikimedia_client

    with patch(
        "wikimedia_client.discover",
        side_effect=wikimedia_client.WikimediaError("timeout", "qua han"),
    ):
        r = client.get(
            "/locations/discover", params={"latitude": 10.77, "longitude": 106.7}
        )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "timeout"


def test_discover_rate_limited_returns_503(client):
    import wikimedia_client

    with patch(
        "wikimedia_client.discover",
        side_effect=wikimedia_client.WikimediaError("rate_limited", "429"),
    ):
        r = client.get(
            "/locations/discover", params={"latitude": 10.77, "longitude": 106.7}
        )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "rate_limited"


# ----------------------------------- Migration --------------------------------


def test_migration_preserves_old_data_and_is_idempotent(tmp_path, monkeypatch):
    """
    Giả lập 1 DB schema tuần 2 (chỉ 4 cột), chạy migrate.run_migrations()
    2 lần, kiểm tra: không lỗi, dữ liệu cũ (id, name...) không đổi, cột mới
    được thêm + backfill đúng.
    """
    monkeypatch.chdir(tmp_path)
    import sqlite3

    conn = sqlite3.connect("content.db")
    conn.execute(
        """
        CREATE TABLE locations (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description_vi TEXT NOT NULL,
            target_languages TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO locations (id, name, description_vi, target_languages) "
        "VALUES (1, 'Ten cu', 'Mo ta cu', 'en')"
    )
    conn.commit()
    conn.close()

    import migrate as migrate_module

    importlib.reload(migrate_module)

    migrate_module.run_migrations(verbose=False)
    migrate_module.run_migrations(verbose=False)  # chạy lần 2 - phải không lỗi

    conn = sqlite3.connect("content.db")
    row = conn.execute(
        "SELECT id, name, description_vi, description_source, latitude, "
        "radius, source, is_ready FROM locations WHERE id=1"
    ).fetchone()
    conn.close()

    assert row[0] == 1
    assert row[1] == "Ten cu"
    assert row[2] == "Mo ta cu"
    assert row[3] == "Mo ta cu"  # backfill description_source = description_vi
    assert row[4] is None  # latitude cũ giữ NULL, không gán 0,0
    assert row[5] == 80  # radius mặc định 80
    assert row[6] == "manual"
    assert row[7] == 0  # is_ready = false
