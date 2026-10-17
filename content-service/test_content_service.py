"""
Test cho content-service tuần 3.
Chạy: cd content-service && python -m pytest tests/ -v
"""

import math
import os
import sys
import time
import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Mỗi test chạy trên 1 DB tạm riêng, xóa sau khi xong."""
    monkeypatch.chdir(tmp_path)

    import models as models_module
    import main as main_module

    for mod in (models_module, main_module):
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
        json={"name": "Cho Ben Thanh", "description_vi": "Mo ta", "target_languages": "en,ja"},
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
        "/locations", json={"name": "A", "description_vi": "x", "target_languages": "en"}
    )
    loc_id = r.json()["id"]

    r = client.delete(f"/locations/{loc_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    assert client.get(f"/locations/{loc_id}").status_code == 404
    assert client.delete(f"/locations/{loc_id}").status_code == 404
    assert client.delete("/locations/999999").status_code == 404


# ----------------------------- Validate tọa độ -------------------------------


@pytest.mark.parametrize("lat,lon", [(999, 10), (10, 999), (-999, -999)])
def test_invalid_coordinates_rejected(client, lat, lon):
    r = client.post(
        "/locations",
        json={
            "name": "Bad", "description_vi": "x", "target_languages": "en",
            "latitude": lat, "longitude": lon,
        },
    )
    assert r.status_code == 422


# ------------------------------- Route ordering ------------------------------


def test_nearby_and_discover_not_captured_by_id_route(client):
    r = client.get("/locations/nearby", params={"latitude": 0, "longitude": 0})
    assert r.status_code == 200

    with patch("main.wikimedia_discover", return_value=([], False)):
        r = client.get("/locations/discover", params={"latitude": 0, "longitude": 0})
        assert r.status_code == 200


# ----------------------------------- Haversine --------------------------------


def test_haversine_known_distance():
    from main import haversine_m

    d = haversine_m(21.0285, 105.8542, 10.7769, 106.7009)
    assert 1_100_000 < d < 1_200_000
    assert haversine_m(10.0, 106.0, 10.0, 106.0) == pytest.approx(0, abs=1e-6)


def test_nearby_filters_by_max_distance(client):
    client.post(
        "/locations",
        json={"name": "Gan", "description_vi": "x", "target_languages": "en",
              "latitude": 10.7769, "longitude": 106.7009},
    )
    client.post(
        "/locations",
        json={"name": "Xa", "description_vi": "x", "target_languages": "en",
              "latitude": 21.0285, "longitude": 105.8542},
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
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert r.status_code == 200
    body = r.json()
    assert body["cache_hit"] is False
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["source"] == "wikimedia"

    all_locations = client.get("/locations").json()
    assert any(loc["source_page_id"] == 111 for loc in all_locations)


def test_discover_duplicate_pageid_updates_not_duplicates(client):
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    updated = _fake_candidate()
    updated["description_source"] = "Mo ta moi hon"
    with patch("main.wikimedia_discover", return_value=([updated], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    all_locations = client.get("/locations").json()
    matches = [loc for loc in all_locations if loc["source_page_id"] == 111]
    assert len(matches) == 1
    assert matches[0]["description_source"] == "Mo ta moi hon"


def test_discover_empty_result(client):
    with patch("main.wikimedia_discover", return_value=([], True)):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == [] and body["cache_hit"] is True
    assert body["served_from"] == "memory_cache" and body["upstream_error"] is None


def test_discover_upstream_timeout_returns_503(client):
    import main as main_module

    with patch(
        "main.wikimedia_discover",
        side_effect=main_module.WikimediaError("timeout", "qua han"),
    ):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "timeout"


def test_discover_rate_limited_returns_503(client):
    import main as main_module

    with patch(
        "main.wikimedia_discover",
        side_effect=main_module.WikimediaError("rate_limited", "429"),
    ):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "rate_limited"


# ----------------------------------- Migration --------------------------------


def test_migration_preserves_old_data_and_is_idempotent(tmp_path, monkeypatch):
    """
    Giả lập DB schema tuần 2 (4 cột), chạy run_migrations() 2 lần, kiểm tra
    không lỗi và dữ liệu cũ (id, name...) giữ nguyên.
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

    import main as main_module

    importlib.reload(main_module)
    main_module.run_migrations(verbose=False)
    main_module.run_migrations(verbose=False)  # chạy lần 2 - không lỗi

    conn = sqlite3.connect("content.db")
    row = conn.execute(
        "SELECT id, name, description_vi, description_source, latitude, "
        "radius, source, is_ready FROM locations WHERE id=1"
    ).fetchone()
    conn.close()

    assert row[0] == 1
    assert row[1] == "Ten cu"
    assert row[2] == "Mo ta cu"
    assert row[3] == "Mo ta cu"
    assert row[4] is None
    assert row[5] == 80
    assert row[6] == "manual"
    assert row[7] == 0


# =============================================================================
# TUẦN 4: migration v3, NarrationCache, mark-ready, invalidation, discovery
# =============================================================================


def _make_location(client, **overrides):
    payload = {"name": "Dia diem", "description_vi": "Noi dung goc", "target_languages": "en,ja",
               "latitude": 10.77, "longitude": 106.70}
    payload.update(overrides)
    r = client.post("/locations", json=payload)
    assert r.status_code == 200
    return r.json()["id"]


def _text_payload(lang="vi", **overrides):
    body = {"lang": lang, "text": "Noi dung thuyet minh", "source_url": "https://vi.wikipedia.org/wiki/X",
            "target_title": "X", "is_fallback": False, "reason": None, "voice": None,
            "text_hash": None, "audio_filename": None, "audio_status": "text_ready"}
    body.update(overrides)
    return body


def _audio_payload(lang="ja", **overrides):
    body = _text_payload(lang, voice=f"{lang}-XX-TestNeural", text_hash="abc123def",
                         audio_filename="abc123def.mp3", audio_status="audio_ready")
    body.update(overrides)
    return body


def _session():
    import main as main_module
    return main_module.SessionLocal()


# ------------------------------ Migration v3 --------------------------------


def test_migration_v3_adds_narration_cache_idempotent(tmp_path, monkeypatch):
    """DB schema tuần 3 (đã có version 2) -> chạy migration -> có bảng narration_cache."""
    monkeypatch.chdir(tmp_path)
    import sqlite3

    conn = sqlite3.connect("content.db")
    conn.execute("CREATE TABLE locations (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "description_vi TEXT NOT NULL, target_languages TEXT NOT NULL)")
    conn.execute("INSERT INTO locations VALUES (1, 'Cu', 'Mo ta cu', 'en')")
    conn.commit()
    conn.close()

    import models as models_module
    import main as main_module
    importlib.reload(models_module)
    importlib.reload(main_module)

    # Tái tạo trạng thái "mới tới tuần 3": xóa bảng v3 + version 3 rồi chạy lại.
    conn = sqlite3.connect("content.db")
    conn.execute("DROP TABLE narration_cache")
    conn.execute("DELETE FROM schema_migrations WHERE version = 3")
    conn.commit()
    conn.close()

    assert main_module.run_migrations(verbose=False) == [3]
    assert main_module.run_migrations(verbose=False) == []  # lần 2: không làm gì

    conn = sqlite3.connect("content.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(narration_cache)")}
    fks = conn.execute("PRAGMA foreign_key_list(narration_cache)").fetchall()
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    old_row = conn.execute("SELECT id, name, description_source FROM locations WHERE id=1").fetchone()
    conn.close()

    assert {"location_id", "requested_lang", "lang", "text", "source_url", "target_title",
            "is_fallback", "reason", "voice", "text_hash", "audio_filename", "audio_status",
            "created_at", "updated_at", "expires_at"} <= cols
    assert fks and fks[0][2] == "locations"
    assert versions == {2, 3}
    assert old_row == (1, "Cu", "Mo ta cu")  # dữ liệu tuần 2 vẫn nguyên


def test_foreign_keys_are_enforced(client):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    import main as main_module

    with main_module.SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(main_module.NarrationCache(
            location_id=99999, requested_lang="vi", lang="vi", text="x",
            audio_status="text_ready", created_at=now, updated_at=now,
            expires_at=now + timedelta(days=1)))
        with pytest.raises(IntegrityError):
            db.commit()


# --------------------------- GET / PUT narration cache -----------------------


def test_narration_miss_hit_and_404(client):
    loc = _make_location(client)
    r = client.get(f"/locations/{loc}/narrations/vi")
    assert r.status_code == 200 and r.json() == {"cache_hit": False, "narration": None}

    assert client.get("/locations/99999/narrations/vi").status_code == 404
    assert client.put("/locations/99999/narrations/vi", json=_text_payload()).status_code == 404
    assert client.get(f"/locations/{loc}/narrations/xx").status_code == 422

    assert client.put(f"/locations/{loc}/narrations/vi", json=_text_payload()).status_code == 200
    r = client.get(f"/locations/{loc}/narrations/vi")
    body = r.json()
    assert body["cache_hit"] is True
    n = body["narration"]
    assert n["requested_lang"] == "vi" and n["lang"] == "vi" and n["audio_status"] == "text_ready"
    assert n["audio_filename"] is None and "audio_url" not in n
    # TTL mặc định ~7 ngày
    exp = datetime.fromisoformat(n["expires_at"])
    delta = exp - datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_narration_expired_is_miss(client):
    loc = _make_location(client)
    client.put(f"/locations/{loc}/narrations/vi", json=_audio_payload("vi"))
    with _session() as db:
        row = db.query(__import__("main").NarrationCache).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/locations/{loc}/narrations/vi").json() == {"cache_hit": False, "narration": None}
    # cache audio hết hạn không được tính vào is_ready
    r = client.patch(f"/locations/{loc}/mark-ready")
    assert r.json()["is_ready"] is False and r.json()["audio_ready_languages"] == []


def test_narration_ttl_configurable(client, monkeypatch):
    monkeypatch.setenv("NARRATION_TTL_DAYS", "1")
    loc = _make_location(client)
    n = client.put(f"/locations/{loc}/narrations/vi", json=_text_payload()).json()["narration"]
    delta = datetime.fromisoformat(n["expires_at"]) - datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(hours=23) < delta <= timedelta(days=1)


def test_narration_explicit_expiry_must_be_future(client):
    loc = _make_location(client)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.put(f"/locations/{loc}/narrations/vi",
                      json=_text_payload(expires_at=past)).status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    assert client.put(f"/locations/{loc}/narrations/vi",
                      json=_text_payload(expires_at=future)).status_code == 200


def test_narration_fallback_keeps_real_language(client):
    loc = _make_location(client)
    body = _text_payload("vi", is_fallback=True, reason="target_language_unavailable")
    r = client.put(f"/locations/{loc}/narrations/ja", json=body)
    assert r.status_code == 200
    n = client.get(f"/locations/{loc}/narrations/ja").json()["narration"]
    assert n["requested_lang"] == "ja" and n["lang"] == "vi"
    assert n["is_fallback"] is True and n["reason"] == "target_language_unavailable"


@pytest.mark.parametrize("path_lang,body", [
    ("ja", _text_payload("vi")),                                              # lang khác mà không đánh dấu fallback
    ("ja", _text_payload("ja", is_fallback=True, reason="empty_extract")),    # fallback nhưng lang trùng
    ("ja", _text_payload("vi", is_fallback=True, reason=None)),               # fallback thiếu reason
    ("ja", _text_payload("ja", reason="empty_extract")),                      # không fallback mà có reason
    ("ja", _text_payload("ja", requested_lang="en")),                         # requested_lang lệch path
    ("ja", _text_payload("ja", voice="en-US-GuyNeural")),                     # voice lệch lang
    ("ja", _text_payload("ja", text="   ")),                                  # text rỗng
    ("ja", _text_payload("xx")),                                              # lang không hỗ trợ
    ("ja", _text_payload("ja", audio_status="done")),                         # trạng thái lạ
    ("ja", _text_payload("ja", source_url="ftp://x")),                        # URL không http(s)
    ("ja", {**_text_payload("ja"), "audio_url": "http://content:8001/a.mp3"}),# không được lưu URL
])
def test_narration_put_validation(client, path_lang, body):
    loc = _make_location(client)
    assert client.put(f"/locations/{loc}/narrations/{path_lang}", json=body).status_code == 422
    assert client.get(f"/locations/{loc}/narrations/{path_lang}").json()["cache_hit"] is False


@pytest.mark.parametrize("filename", [
    "../evil.mp3", "a/b.mp3", "a\\b.mp3", "/etc/x.mp3", "C:x.mp3", "x.wav", "x.mp3.exe",
    ".mp3", "..mp3", "x..mp3", "http://host/x.mp3", "x.mp3 ", "", "con fig.mp3",
])
def test_narration_rejects_bad_audio_filename(client, filename):
    loc = _make_location(client)
    r = client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja", audio_filename=filename))
    assert r.status_code == 422


def test_narration_audio_ready_needs_complete_metadata(client):
    loc = _make_location(client)
    for missing in ("audio_filename", "voice", "text_hash"):
        body = _audio_payload("ja")
        body[missing] = None
        assert client.put(f"/locations/{loc}/narrations/ja", json=body).status_code == 422
    # text_ready không được kèm tên file
    assert client.put(f"/locations/{loc}/narrations/ja",
                      json=_text_payload("ja", audio_filename="x.mp3")).status_code == 422
    assert client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja")).status_code == 200


def test_narration_upsert_same_key_twice_keeps_one_row(client):
    import main as main_module
    loc = _make_location(client)
    first = client.put(f"/locations/{loc}/narrations/ja", json=_text_payload("ja", text="Ban 1")).json()
    second = client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja", text="Ban 2")).json()
    assert first["created"] is True and second["created"] is False
    assert second["narration"]["created_at"] == first["narration"]["created_at"]
    assert second["narration"]["updated_at"] >= first["narration"]["updated_at"]
    with _session() as db:
        rows = db.query(main_module.NarrationCache).all()
        assert len(rows) == 1 and rows[0].text == "Ban 2" and rows[0].audio_status == "audio_ready"


# ------------------------------- mark-ready ---------------------------------


def test_mark_ready_is_computed_per_language_not_client_supplied(client):
    loc = _make_location(client)
    # Client không thể ép is_ready=true: body bị bỏ qua.
    r = client.patch(f"/locations/{loc}/mark-ready", json={"is_ready": True})
    assert r.status_code == 200 and r.json()["is_ready"] is False

    client.put(f"/locations/{loc}/narrations/en", json=_text_payload("en"))      # chỉ text
    assert client.patch(f"/locations/{loc}/mark-ready").json()["is_ready"] is False

    client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja"))     # có audio
    r = client.patch(f"/locations/{loc}/mark-ready").json()
    assert r["is_ready"] is True and r["audio_ready_languages"] == ["ja"]
    assert client.get(f"/locations/{loc}").json()["is_ready"] is True
    # is_ready=true không thay thế cache theo ngôn ngữ: "en" vẫn chưa có audio
    en = client.get(f"/locations/{loc}/narrations/en").json()["narration"]
    assert en["audio_status"] == "text_ready"

    # hạ ja xuống text_ready -> không còn audio nào -> is_ready tự về false
    client.put(f"/locations/{loc}/narrations/ja", json=_text_payload("ja"))
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False
    assert client.patch("/locations/99999/mark-ready").status_code == 404


# ------------------------------- invalidation -------------------------------


def test_updating_content_or_coordinates_invalidates_cache(client):
    loc = _make_location(client)
    base = {"name": "Dia diem", "description_vi": "Noi dung goc", "target_languages": "en,ja",
            "latitude": 10.77, "longitude": 106.70}
    client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja"))
    client.patch(f"/locations/{loc}/mark-ready")

    # Chỉ đổi tên hiển thị -> giữ cache
    assert client.put(f"/locations/{loc}", json={**base, "name": "Ten khac"}).status_code == 200
    assert client.get(f"/locations/{loc}/narrations/ja").json()["cache_hit"] is True
    assert client.get(f"/locations/{loc}").json()["is_ready"] is True

    # Đổi nội dung -> xóa cache, is_ready=false
    assert client.put(f"/locations/{loc}", json={**base, "description_vi": "Noi dung moi"}).status_code == 200
    assert client.get(f"/locations/{loc}/narrations/ja").json()["cache_hit"] is False
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False

    # Đổi tọa độ -> cũng xóa cache
    client.put(f"/locations/{loc}/narrations/vi", json=_text_payload("vi"))
    assert client.put(f"/locations/{loc}", json={**base, "description_vi": "Noi dung moi",
                                                 "latitude": 10.80}).status_code == 200
    assert client.get(f"/locations/{loc}/narrations/vi").json()["cache_hit"] is False


def test_deleting_location_removes_cache_without_orphans(client):
    from sqlalchemy import text
    import main as main_module
    loc = _make_location(client)
    other = _make_location(client, name="Khac")
    client.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja"))
    client.put(f"/locations/{loc}/narrations/en", json=_text_payload("en"))
    client.put(f"/locations/{other}/narrations/ja", json=_audio_payload("ja"))

    assert client.delete(f"/locations/{loc}").status_code == 200
    with _session() as db:
        remaining = db.query(main_module.NarrationCache).all()
        assert [n.location_id for n in remaining] == [other]
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    assert client.get(f"/locations/{loc}/narrations/ja").status_code == 404


# ------------------------------ discovery (tuần 4) ---------------------------


def test_discovery_cache_hit_does_not_fake_sync_time(client):
    t1 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    first = {**_fake_candidate(), "last_synced_at": t1}
    with patch("main.wikimedia_discover", return_value=([first], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    loc = [x for x in client.get("/locations").json() if x["source_page_id"] == 111][0]
    assert loc["last_synced_at"].startswith("2026-10-01T08:00")

    # Lần 2 chỉ đọc cache (cache_hit=True): last_synced_at KHÔNG được đổi.
    cached = {**_fake_candidate(), "last_synced_at": t2, "description_source": "Khac"}
    with patch("main.wikimedia_discover", return_value=([cached], True)):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert r.json()["cache_hit"] is True
    loc = [x for x in client.get("/locations").json() if x["source_page_id"] == 111][0]
    assert loc["last_synced_at"].startswith("2026-10-01T08:00")
    assert loc["description_source"] == "Mo ta tu Wikipedia"

    # Lần 3 fetch thật (cache_hit=False): được refresh last_synced_at.
    with patch("main.wikimedia_discover", return_value=([{**_fake_candidate(), "last_synced_at": t2}], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    loc = [x for x in client.get("/locations").json() if x["source_page_id"] == 111][0]
    assert loc["last_synced_at"].startswith("2026-10-01T09:00")


def test_discovery_refresh_with_changed_content_invalidates_narrations(client):
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    loc_id = r.json()["candidates"][0]["id"]
    client.put(f"/locations/{loc_id}/narrations/ja", json=_audio_payload("ja"))
    assert client.get(f"/locations/{loc_id}").json()["is_ready"] is True

    # Fetch lại, nội dung bài không đổi -> giữ cache
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert client.get(f"/locations/{loc_id}/narrations/ja").json()["cache_hit"] is True

    # Fetch lại, bài đã đổi nội dung -> cache narration bị xóa
    changed = {**_fake_candidate(), "description_source": "Noi dung bai da sua"}
    with patch("main.wikimedia_discover", return_value=([changed], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})
    assert client.get(f"/locations/{loc_id}/narrations/ja").json()["cache_hit"] is False
    assert client.get(f"/locations/{loc_id}").json()["is_ready"] is False


def test_concurrent_discovery_unique_conflict_creates_no_duplicate(client):
    """Mô phỏng 2 discovery đồng thời: request thua cuộc không thấy record ở lần
    đọc đầu, insert bị unique chặn -> rollback -> đọc lại record của request thắng."""
    import main as main_module
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    real_find = main_module._find_wikimedia
    calls = {"n": 0}

    def racing_find(db, page_id, source_lang="vi"):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(db, page_id, source_lang)

    with patch("main._find_wikimedia", side_effect=racing_find), \
         patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        r = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7})

    assert r.status_code == 200 and len(r.json()["candidates"]) == 1
    assert calls["n"] == 2  # đã đi qua nhánh IntegrityError -> đọc lại
    assert len([x for x in client.get("/locations").json() if x["source_page_id"] == 111]) == 1
    assert client.get("/health").status_code == 200  # DB/session vẫn dùng được sau rollback


def test_nearby_and_discover_keep_source_fields_and_distance(client):
    with patch("main.wikimedia_discover", return_value=([_fake_candidate()], False)):
        disc = client.get("/locations/discover", params={"latitude": 10.77, "longitude": 106.7}).json()
    for key in ("source_title", "source_page_id", "source_url", "source", "source_lang"):
        assert disc["candidates"][0][key] is not None
    near = client.get("/locations/nearby", params={"latitude": 10.7721, "longitude": 106.698}).json()
    c = near["candidates"][0]
    assert c["source_page_id"] == 111 and c["source_title"] and c["source_url"]
    assert isinstance(c["distance_m"], float)


# =============================================================================
# TUẦN 5: ổn định Content - fixture offline, nearby, cache, DELETE, discovery
# =============================================================================

import httpx
from types import SimpleNamespace

KM = 111_194.9  # mét / 1 độ vĩ độ


def _approx_dist(lat1, lon1, lat2, lon2):
    """Công thức phẳng độc lập (không dùng haversine_m) để đối chiếu distance_m."""
    return math.hypot((lat2 - lat1) * KM,
                      (lon2 - lon1) * KM * math.cos(math.radians((lat1 + lat2) / 2)))


@pytest.fixture()
def seeded(client):
    """Client + bộ dữ liệu mẫu OFFLINE (record cũ không GPS, 2 điểm có GPS, thiếu ja)."""
    import seed_test_data
    import main as main_module

    with main_module.SessionLocal() as db:
        ids = seed_test_data.insert_fixtures(db)
    return SimpleNamespace(client=client, ids=ids, seed=seed_test_data)


# ------------------------------ fixture offline -------------------------------


def test_seed_fixture_is_offline_manual_data_with_expected_shape(seeded):
    c, ids = seeded.client, seeded.ids
    rows = {r["id"]: r for r in c.get("/locations").json()}
    assert len(rows) == 3
    assert all(r["source"] == "manual" and r["source_url"] is None
               and r["source_page_id"] is None for r in rows.values())  # không giả làm kết quả online
    assert rows[ids["legacy"]]["latitude"] is None and rows[ids["legacy"]]["longitude"] is None
    assert rows[ids["legacy"]]["radius"] == 80 and rows[ids["legacy"]]["is_ready"] is False
    for key in ("ben_thanh", "doc_lap"):
        assert rows[ids[key]]["latitude"] is not None and rows[ids[key]]["radius"] == 80

    def has(loc, lang):
        return c.get(f"/locations/{ids[loc]}/narrations/{lang}").json()["cache_hit"]

    assert [has("ben_thanh", l) for l in ("vi", "en", "ja")] == [True, True, True]
    assert [has("doc_lap", l) for l in ("vi", "en", "ja")] == [True, True, False]  # thiếu ja
    # fixture không tuyên bố có file MP3
    n = c.get(f"/locations/{ids['ben_thanh']}/narrations/ja").json()["narration"]
    assert n["audio_status"] == "text_ready" and n["audio_filename"] is None


def test_seed_script_never_touches_real_db_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import seed_test_data

    (tmp_path / "content.db").write_text("DU LIEU THAT")
    ids = seed_test_data.seed_database("demo_content.db")
    assert set(ids) == {"legacy", "ben_thanh", "doc_lap"}
    assert (tmp_path / "content.db").read_text() == "DU LIEU THAT"

    with pytest.raises(SystemExit):  # DB thật của service
        seed_test_data.seed_database("content.db", reset=True)
    with pytest.raises(SystemExit):  # đã tồn tại, không --reset
        seed_test_data.seed_database("demo_content.db")
    (tmp_path / "mydata.db").write_text("DU LIEU KHAC")
    with pytest.raises(SystemExit):  # --reset chỉ cho file demo/test
        seed_test_data.seed_database("mydata.db", reset=True)
    assert (tmp_path / "mydata.db").read_text() == "DU LIEU KHAC"
    assert (tmp_path / "content.db").read_text() == "DU LIEU THAT"

    assert set(seed_test_data.seed_database("demo_content.db", reset=True)) == {"legacy", "ben_thanh", "doc_lap"}


# ---------------------------- Location / nearby (GPS) ---------------------------


def test_location_response_contains_all_documented_fields(seeded):
    loc = seeded.client.get(f"/locations/{seeded.ids['ben_thanh']}").json()
    expected = {"id", "name", "description_source", "latitude", "longitude", "radius", "source",
                "source_lang", "source_title", "source_page_id", "source_url", "last_synced_at",
                "is_ready", "description_vi", "target_languages"}
    assert expected <= set(loc)

    near = seeded.client.get("/locations/nearby", params={"latitude": 10.7726, "longitude": 106.6981}).json()
    cand = near["candidates"][0]
    assert {"id", "name", "description_source", "latitude", "longitude", "radius", "distance_m",
            "source", "source_lang", "source_title", "source_page_id", "source_url",
            "last_synced_at", "is_ready"} <= set(cand)


def test_manual_record_without_gps_is_readable_but_never_a_candidate(seeded):
    c, ids = seeded.client, seeded.ids
    assert c.get(f"/locations/{ids['legacy']}").status_code == 200
    near = c.get("/locations/nearby", params={"latitude": 10.7725, "longitude": 106.698,
                                              "max_distance": 1_000_000}).json()
    got = [x["id"] for x in near["candidates"]]
    assert ids["legacy"] not in got and set(got) == {ids["ben_thanh"], ids["doc_lap"]}


def test_nearby_returns_every_candidate_in_range_sorted_by_distance(seeded):
    c, ids = seeded.client, seeded.ids
    lat, lon = 10.7726, 106.6981
    cands = c.get("/locations/nearby", params={"latitude": lat, "longitude": lon,
                                                "max_distance": 1000}).json()["candidates"]
    assert [x["id"] for x in cands] == [ids["ben_thanh"], ids["doc_lap"]]  # cả 2, gần nhất trước
    for x in cands:  # đối chiếu với công thức độc lập (sai số < 1%)
        assert x["distance_m"] == pytest.approx(_approx_dist(lat, lon, x["latitude"], x["longitude"]), rel=0.01)
    only_a = c.get("/locations/nearby", params={"latitude": lat, "longitude": lon,
                                                 "max_distance": 100}).json()["candidates"]
    assert [x["id"] for x in only_a] == [ids["ben_thanh"]]


def test_candidate_outside_activation_radius_is_listed_then_second_point_in_radius(seeded):
    """Content chỉ trả ứng viên + distance_m; việc kích hoạt (distance_m <= radius) là của Gateway."""
    c, ids = seeded.client, seeded.ids
    a_lat, a_lon = 10.7725, 106.6980
    p1 = (a_lat + 120 / KM, a_lon)  # cách chợ Bến Thành ~120m (> radius 80)
    first = c.get("/locations/nearby", params={"latitude": p1[0], "longitude": p1[1],
                                                "max_distance": 500}).json()["candidates"]
    assert ids["ben_thanh"] in [x["id"] for x in first]
    assert all(x["distance_m"] > x["radius"] for x in first)  # chưa điểm nào đủ gần để kích hoạt

    b_lat, b_lon = 10.7770, 106.6953
    p2 = (b_lat - 30 / KM, b_lon)  # cách Dinh Độc Lập ~30m (< radius 80)
    second = c.get("/locations/nearby", params={"latitude": p2[0], "longitude": p2[1],
                                                 "max_distance": 500}).json()["candidates"]
    assert [x["id"] for x in second] == [ids["doc_lap"]]
    assert second[0]["distance_m"] <= second[0]["radius"]


def test_nearby_never_calls_wikimedia(seeded):
    def boom(*a, **k):
        raise AssertionError("/nearby không được gọi Wikimedia")

    with patch("main._wiki_request", side_effect=boom), patch("main.wikimedia_discover", side_effect=boom):
        for _ in range(5):
            r = seeded.client.get("/locations/nearby", params={"latitude": 10.7726, "longitude": 106.6981})
            assert r.status_code == 200


# ------------------------------ cache narration (tuần 5) -------------------------


def test_fallback_text_and_real_language_preserved_exactly(seeded):
    c, ids = seeded.client, seeded.ids
    text = "Dinh Độc Lập — ghi dấu 30/4/1975.\n\nĐoạn 2  (hai khoảng trắng) 独立宮殿 "
    body = _text_payload("vi", text=text, is_fallback=True, reason="target_language_unavailable")
    assert c.put(f"/locations/{ids['doc_lap']}/narrations/ja", json=body).status_code == 200
    n = c.get(f"/locations/{ids['doc_lap']}/narrations/ja").json()["narration"]
    assert n["text"] == text  # không cắt/chuẩn hóa/đổi bytes
    assert n["requested_lang"] == "ja" and n["lang"] == "vi" and n["is_fallback"] is True


def test_tts_failure_keeps_text_ready_without_blocking_text(seeded):
    c, ids = seeded.client, seeded.ids
    loc = ids["doc_lap"]
    # B lưu text rồi TTS lỗi -> vẫn text_ready, không audio_filename
    c.put(f"/locations/{loc}/narrations/ja",
          json=_text_payload("vi", is_fallback=True, reason="target_language_unavailable"))
    n = c.get(f"/locations/{loc}/narrations/ja").json()["narration"]
    assert n["audio_status"] == "text_ready" and n["audio_filename"] is None and n["text"]
    assert c.patch(f"/locations/{loc}/mark-ready").json()["is_ready"] is False
    assert c.get(f"/locations/{loc}").json()["is_ready"] is False


def test_integration_text_ready_to_audio_ready_to_mark_ready(seeded):
    c, ids = seeded.client, seeded.ids
    loc = ids["doc_lap"]
    assert c.get(f"/locations/{loc}/narrations/ja").json() == {"cache_hit": False, "narration": None}

    fb = dict(is_fallback=True, reason="target_language_unavailable")
    assert c.put(f"/locations/{loc}/narrations/ja", json=_text_payload("vi", **fb)).json()["created"] is True
    assert c.get(f"/locations/{loc}/narrations/ja").json()["narration"]["audio_status"] == "text_ready"
    assert c.patch(f"/locations/{loc}/mark-ready").json()["is_ready"] is False

    up = c.put(f"/locations/{loc}/narrations/ja",
               json=_audio_payload("vi", voice="vi-VN-HoaiMyNeural", audio_filename="doclap_vi.mp3", **fb))
    assert up.status_code == 200 and up.json()["created"] is False
    ready = c.patch(f"/locations/{loc}/mark-ready").json()
    assert ready == {"id": loc, "is_ready": True, "audio_ready_languages": ["ja"]}
    # is_ready chỉ là cờ tổng quát: en vẫn chỉ có text
    assert c.get(f"/locations/{loc}/narrations/en").json()["narration"]["audio_status"] == "text_ready"
    cand = c.get("/locations/nearby", params={"latitude": 10.7770, "longitude": 106.6953}).json()["candidates"][0]
    assert cand["id"] == loc and cand["is_ready"] is True


def test_db_persistence_across_restart(seeded):
    c, ids = seeded.client, seeded.ids
    loc = ids["ben_thanh"]
    c.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja", audio_filename="bt_ja.mp3"))
    c.patch(f"/locations/{loc}/mark-ready")

    import models as models_module
    import main as main_module
    importlib.reload(models_module)  # "restart": nạp lại toàn bộ module, mở lại cùng file content.db
    importlib.reload(main_module)
    assert main_module.run_migrations(verbose=False) == []  # DB đã mới nhất, không migrate lại

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c2:
        n = c2.get(f"/locations/{loc}/narrations/ja").json()
        assert n["cache_hit"] is True and n["narration"]["audio_filename"] == "bt_ja.mp3"
        assert c2.get(f"/locations/{loc}").json()["is_ready"] is True
        assert len(c2.get("/locations").json()) == 3
        near = c2.get("/locations/nearby", params={"latitude": 10.7725, "longitude": 106.698}).json()
        assert loc in [x["id"] for x in near["candidates"]]


def test_editing_content_invalidates_all_languages_without_orphans(seeded):
    from sqlalchemy import text
    import main as main_module
    c, ids = seeded.client, seeded.ids
    loc, other = ids["ben_thanh"], ids["doc_lap"]
    c.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja", audio_filename="bt_ja.mp3"))
    assert c.get(f"/locations/{loc}").json()["is_ready"] is True

    base = c.get(f"/locations/{loc}").json()
    body = {k: base[k] for k in ("name", "description_vi", "target_languages", "latitude", "longitude", "radius")}
    body["description_vi"] = "Nội dung đã được chỉnh sửa."
    assert c.put(f"/locations/{loc}", json=body).status_code == 200

    assert [c.get(f"/locations/{loc}/narrations/{l}").json()["cache_hit"] for l in ("vi", "en", "ja")] == [False] * 3
    assert c.get(f"/locations/{loc}").json()["is_ready"] is False
    assert c.get(f"/locations/{other}/narrations/vi").json()["cache_hit"] is True  # điểm khác không bị ảnh hưởng
    with main_module.SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_delete_narration_is_idempotent_and_recomputes_is_ready(seeded):
    c, ids = seeded.client, seeded.ids
    loc = ids["ben_thanh"]
    assert c.delete("/locations/99999/narrations/ja").status_code == 404
    assert c.delete(f"/locations/{loc}/narrations/xx").status_code == 422

    c.put(f"/locations/{loc}/narrations/ja", json=_audio_payload("ja", audio_filename="bt_ja.mp3"))
    assert c.get(f"/locations/{loc}").json()["is_ready"] is True

    first = c.delete(f"/locations/{loc}/narrations/ja")
    assert first.status_code == 200
    assert first.json() == {"deleted": True, "location_id": loc, "requested_lang": "ja", "is_ready": False}
    again = c.delete(f"/locations/{loc}/narrations/ja")  # idempotent
    assert again.status_code == 200 and again.json()["deleted"] is False and again.json()["is_ready"] is False
    assert c.get(f"/locations/{loc}/narrations/ja").json()["cache_hit"] is False
    assert c.get(f"/locations/{loc}/narrations/vi").json()["cache_hit"] is True  # key khác còn nguyên
    assert c.get(f"/locations/{loc}").json()["is_ready"] is False


# ------------------------------ Wikimedia client (tuần 5) ---------------------------


class _FakeResp:
    def __init__(self, status=200, data=None, headers=None, bad_json=False):
        self.status_code, self._data, self.headers, self._bad = status, data, headers or {}, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._data


class _FakeClient:
    """get() trả lần lượt các phần tử (hoặc ném nếu là Exception); hết thì lặp phần tử cuối."""

    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


OK_BODY = {"query": {"geosearch": []}}


@pytest.fixture()
def wiki(client, monkeypatch):
    import main as main_module
    sleeps = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: sleeps.append(s))
    return SimpleNamespace(m=main_module, sleeps=sleeps)


@pytest.mark.parametrize("body,code", [
    ({"error": {"code": "ratelimited", "info": "x"}}, "rate_limited"),   # 200 nhưng là lỗi rate limit
    ({"error": {"code": "badcoord"}}, "upstream_error"),
    ({"warnings": {}}, "upstream_error"),                                # thiếu "query" -> không coi là rỗng
    ([], "upstream_error"),
])
def test_wiki_error_inside_http_200_is_not_treated_as_empty_result(wiki, body, code):
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(_FakeClient(_FakeResp(200, body)), {})
    assert e.value.code == code


def test_wiki_invalid_json_and_http_errors(wiki):
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(_FakeClient(_FakeResp(200, bad_json=True)), {})
    assert e.value.code == "upstream_error"

    client = _FakeClient(_FakeResp(404))
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(client, {})
    assert e.value.code == "upstream_error" and client.calls == 1  # 4xx không retry


def test_wiki_429_retries_once_honoring_retry_after_then_rate_limited(wiki):
    client = _FakeClient(_FakeResp(429, headers={"Retry-After": "2"}))
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(client, {})
    assert e.value.code == "rate_limited" and e.value.retry_after == 2.0
    assert client.calls == 2 and wiki.sleeps == [2.0]  # đúng 1 retry, chờ đúng Retry-After


def test_wiki_429_then_success_and_retry_after_is_capped(wiki):
    client = _FakeClient(_FakeResp(429), _FakeResp(200, OK_BODY))
    assert wiki.m._wiki_request(client, {}) == OK_BODY
    assert wiki.sleeps == [1.0]  # không có Retry-After -> chờ mặc định 1s

    wiki.sleeps.clear()
    client = _FakeClient(_FakeResp(429, headers={"Retry-After": "100"}), _FakeResp(200, OK_BODY))
    wiki.m._wiki_request(client, {})
    assert wiki.sleeps == [3.0]  # không ngủ quá 3s dù Wikimedia yêu cầu 100s


def test_wiki_never_sleeps_past_the_request_deadline(wiki):
    client = _FakeClient(_FakeResp(429, headers={"Retry-After": "3"}))
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(client, {}, deadline=time.monotonic() + 1.0)
    assert e.value.code == "rate_limited" and client.calls == 1 and wiki.sleeps == []

    expired = _FakeClient(_FakeResp(200, OK_BODY))
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(expired, {}, deadline=time.monotonic() - 1)
    assert e.value.code == "timeout" and expired.calls == 0


def test_wiki_5xx_retries_once_and_network_errors_are_classified(wiki):
    assert wiki.m._wiki_request(_FakeClient(_FakeResp(500), _FakeResp(200, OK_BODY)), {}) == OK_BODY
    client = _FakeClient(_FakeResp(500))
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(client, {})
    assert e.value.code == "upstream_error" and client.calls == 2

    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(_FakeClient(httpx.ReadTimeout("slow")), {})
    assert e.value.code == "timeout"
    with pytest.raises(wiki.m.WikimediaError) as e:
        wiki.m._wiki_request(_FakeClient(httpx.ConnectError("down")), {})
    assert e.value.code == "upstream_error"


# --------------------------- discovery cache + lỗi upstream -------------------------


def _wiki_script(geo_items=None, pages=None, log=None):
    def fake(client, params, deadline=None):
        if log is not None:
            log.append(params.get("list") or params.get("prop"))
        if params.get("list") == "geosearch":
            return {"query": {"geosearch": geo_items or []}}
        return {"query": {"pages": pages or {}}}
    return fake


GEO = [{"pageid": 1, "lat": 10.7725, "lon": 106.698}]
PAGES = {"1": {"pageid": 1, "title": "Chợ Bến Thành", "extract": "Mô tả từ Wikipedia",
               "fullurl": "https://vi.wikipedia.org/wiki/Ch%E1%BB%A3_B%E1%BA%BFn_Th%C3%A0nh"}}


def _discover(client, lat=10.7726, lon=106.6981, **extra):
    return client.get("/locations/discover", params={"latitude": lat, "longitude": lon, **extra})


def test_discovery_memory_cache_covers_empty_cells_and_grid(client):
    import main as main_module
    log = []
    with patch("main._wiki_request", side_effect=_wiki_script(log=log)):
        first = _discover(client).json()
        assert first["candidates"] == [] and first["cache_hit"] is False and first["served_from"] == "wikimedia"
        assert log == ["geosearch"]  # ô không có địa điểm: chỉ 1 request

        second = _discover(client).json()
        near_same_cell = _discover(client, lat=10.7727, lon=106.6982).json()  # cùng ô ~100m
        assert second["cache_hit"] is True and second["served_from"] == "memory_cache"
        assert near_same_cell["cache_hit"] is True and len(log) == 1  # kể cả ô rỗng vẫn được cache

        assert _discover(client, lat=10.7800, lon=106.6981).json()["cache_hit"] is False  # ô khác
        assert _discover(client, radius=300).json()["cache_hit"] is False                 # radius khác
        assert len(log) == 3

        for k, (cands, exp) in list(main_module._wiki_cache.items()):  # hết TTL 60s
            main_module._wiki_cache[k] = (cands, exp - 1000)
        assert _discover(client).json()["cache_hit"] is False and len(log) == 4


@pytest.mark.parametrize("code,retry_after", [("timeout", None), ("rate_limited", 2.0), ("upstream_error", None)])
def test_upstream_error_without_stored_data_is_503_with_stable_code_never_empty(client, code, retry_after):
    import main as main_module
    err = main_module.WikimediaError(code, "loi", retry_after=retry_after)
    with patch("main._wiki_request", side_effect=err):
        r = _discover(client)
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]["code"] == code and "candidates" not in body
    if retry_after:
        assert body["detail"]["retry_after_s"] == 2.0 and r.headers["Retry-After"] == "2"


def test_upstream_error_is_not_cached_so_next_call_recovers(client):
    import main as main_module
    err = main_module.WikimediaError("rate_limited", "429")
    with patch("main._wiki_request", side_effect=err):
        assert _discover(client).status_code == 503
    with patch("main._wiki_request", side_effect=_wiki_script(GEO, PAGES)):
        r = _discover(client)
    assert r.status_code == 200 and r.json()["cache_hit"] is False and len(r.json()["candidates"]) == 1


def test_upstream_error_serves_stored_wikimedia_data_and_keeps_old_metadata(client):
    import main as main_module
    t1 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    first = {**_fake_candidate(pageid=7, title="Cho Ben Thanh"), "last_synced_at": t1,
             "latitude": 10.7725, "longitude": 106.698}
    with patch("main.wikimedia_discover", return_value=([first], False)):
        _discover(client)
    before = [x for x in client.get("/locations").json() if x["source_page_id"] == 7][0]

    err = main_module.WikimediaError("rate_limited", "429")
    with patch("main.wikimedia_discover", side_effect=err):
        r = _discover(client)
    body = r.json()
    assert r.status_code == 200 and body["served_from"] == "sqlite" and body["cache_hit"] is True
    assert body["upstream_error"]["code"] == "rate_limited"  # không giấu việc dữ liệu có thể cũ
    cand = body["candidates"][0]
    assert cand["source_page_id"] == 7 and cand["last_synced_at"].startswith("2026-10-01T08:00")
    assert cand["distance_m"] == pytest.approx(_approx_dist(10.7726, 106.6981, 10.7725, 106.698), rel=0.02)
    after = [x for x in client.get("/locations").json() if x["source_page_id"] == 7][0]
    assert after == before  # phục vụ từ cache không sửa record


def test_sqlite_fallback_only_uses_wikimedia_records_inside_radius_respecting_limit(client):
    import main as main_module
    items = [{**_fake_candidate(pageid=i, title=f"Bai {i}"), "latitude": 10.7725 + i * 0.0006, "longitude": 106.698}
             for i in (1, 2, 3)]
    far = {**_fake_candidate(pageid=9, title="Xa"), "latitude": 10.9, "longitude": 106.698}
    with patch("main.wikimedia_discover", return_value=(items + [far], False)):
        _discover(client)
    client.post("/locations", json={"name": "Thu cong gan", "description_vi": "x", "target_languages": "vi",
                                    "latitude": 10.7725, "longitude": 106.698})  # manual: không thuộc discovery

    err = main_module.WikimediaError("timeout", "cham")
    with patch("main.wikimedia_discover", side_effect=err):
        body = _discover(client, lat=10.7725, lon=106.698, radius=500, limit=2).json()
    assert [c["source_page_id"] for c in body["candidates"]] == [1, 2]  # gần nhất trước, đúng limit
    assert all(c["source"] == "wikimedia" for c in body["candidates"])

    with patch("main.wikimedia_discover", side_effect=err):
        assert _discover(client, lat=10.95, lon=106.698, radius=500).status_code == 503  # ngoài radius -> lỗi, không rỗng


def test_discover_candidates_have_distance_sorted_and_is_ready(client):
    items = [{**_fake_candidate(pageid=2, title="Xa hon"), "latitude": 10.7745, "longitude": 106.698},
             {**_fake_candidate(pageid=1, title="Gan hon"), "latitude": 10.7727, "longitude": 106.698}]
    with patch("main.wikimedia_discover", return_value=(items, False)):
        cands = _discover(client, lat=10.7725, lon=106.698).json()["candidates"]
    assert [c["source_page_id"] for c in cands] == [1, 2]
    assert cands[0]["distance_m"] < cands[1]["distance_m"]
    assert cands[0]["is_ready"] is False and cands[0]["radius"] == 80
