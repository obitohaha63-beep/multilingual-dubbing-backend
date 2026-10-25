"""Test content-service (tuần 3-5). Chạy: cd content-service && python -m pytest tests/ -v
Mỗi test dùng 1 DB tạm riêng; Wikimedia luôn được mock (không gọi mạng thật)."""
import importlib
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
GPS = {"latitude": 10.77, "longitude": 106.7}


def _load(tmp_path, monkeypatch):
    """Nạp lại models+main trong thư mục tạm (giả lập 'khởi động service')."""
    monkeypatch.chdir(tmp_path)
    import models, main
    for mod in (models, main):
        importlib.reload(mod)
    return main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    with TestClient(_load(tmp_path, monkeypatch).app) as c:
        yield c


# ------------------------------- helper -------------------------------------
def _loc(client, **kw):
    body = {"name": "Dia diem", "description_vi": "Noi dung goc", "target_languages": "en,ja",
            "latitude": 10.77, "longitude": 106.70, **kw}
    r = client.post("/locations", json=body)
    assert r.status_code == 200
    return r.json()["id"]


def _text(lang="vi", **kw):
    return {"lang": lang, "text": "Noi dung", "source_url": "https://vi.wikipedia.org/wiki/X",
            "target_title": "X", "is_fallback": False, "reason": None, "voice": None,
            "text_hash": None, "audio_filename": None, "audio_status": "text_ready", **kw}


def _audio(lang="ja", **kw):
    return _text(lang, **{"voice": f"{lang}-XX-TestNeural", "text_hash": "abc123",
                          "audio_filename": "abc123.mp3", "audio_status": "audio_ready", **kw})


def _wiki(pageid=111, **kw):
    return {"source_page_id": pageid, "source_title": "Cho Ben Thanh",
            "description_source": "Mo ta Wikipedia", "source_url": "https://vi.wikipedia.org/wiki/X",
            "latitude": 10.7721, "longitude": 106.698,
            "last_synced_at": datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc), **kw}


def _discover(client, items, cache_hit=False):
    with patch("main.wikimedia_discover", return_value=(items, cache_hit)):
        return client.get("/locations/discover", params=GPS)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _put(client, loc, lang, body):
    return client.put(f"/locations/{loc}/narrations/{lang}", json=body)


def _get(client, loc, lang):
    return client.get(f"/locations/{loc}/narrations/{lang}").json()


# --------------------- CRUD, validate, route, Haversine ---------------------
def test_health_and_crud(client):
    assert client.get("/health").json() == {"service": "content-service", "status": "ok"}
    loc = _loc(client)
    assert client.get(f"/locations/{loc}").status_code == 200
    assert len(client.get("/locations").json()) == 1
    r = client.put(f"/locations/{loc}", json={"name": "Moi", "description_vi": "S", "target_languages": "en"})
    assert r.json()["name"] == "Moi"
    assert client.delete(f"/locations/{loc}").json() == {"deleted": True, "id": loc}
    for call in (client.get, client.delete):  # id không tồn tại -> 404
        assert call(f"/locations/{loc}").status_code == 404


@pytest.mark.parametrize("extra", [
    {"latitude": 999, "longitude": 10}, {"latitude": 10, "longitude": 999},
    {"radius": 0}, {"radius": -5}, {"name": "  "}, {"description_vi": ""}])
def test_invalid_input_rejected(client, extra):
    body = {"name": "A", "description_vi": "x", "target_languages": "en", **extra}
    assert client.post("/locations", json=body).status_code == 422


def test_static_routes_not_captured_by_id_route(client):
    assert client.get("/locations/nearby", params={"latitude": 0, "longitude": 0}).status_code == 200
    assert _discover(client, []).status_code == 200


def test_haversine_known_distance():
    from main import haversine_m
    assert 1_100_000 < haversine_m(21.0285, 105.8542, 10.7769, 106.7009) < 1_200_000
    assert haversine_m(10, 106, 10, 106) == pytest.approx(0, abs=1e-6)


# ----------------------------- /nearby --------------------------------------
def test_nearby_returns_all_in_range_sorted_and_skips_no_gps(client):
    _loc(client, name="Gan", latitude=10.7770, longitude=106.7009)
    _loc(client, name="Xa Hon", latitude=10.7800, longitude=106.7009)  # ~330m
    _loc(client, name="Ngoai", latitude=10.9, longitude=106.7)
    client.post("/locations", json={"name": "Khong GPS", "description_vi": "x", "target_languages": "en"})
    with patch("main.wikimedia_discover") as wiki:
        r = client.get("/locations/nearby", params={"latitude": 10.7769, "longitude": 106.7009})
    wiki.assert_not_called()  # nearby không bao giờ gọi Wikimedia
    cands = r.json()["candidates"]
    assert [c["name"] for c in cands] == ["Gan", "Xa Hon"]
    assert isinstance(cands[0]["distance_m"], float) and "is_ready" in cands[0]
    no_gps = [x for x in client.get("/locations").json() if x["name"] == "Khong GPS"][0]
    assert no_gps["latitude"] is None  # vẫn đọc được qua CRUD


def test_fixture_one_point_in_activation_radius_other_outside_and_missing_ja(client):
    a = _loc(client, name="A", latitude=10.7725, longitude=106.6980)
    b = _loc(client, name="B", latitude=10.7798, longitude=106.6990)  # ~800m
    _put(client, b, "ja", _audio("ja"))
    q = {"latitude": 10.7725, "longitude": 106.6980, "max_distance": 1000}
    c = client.get("/locations/nearby", params=q).json()["candidates"]
    assert [x["id"] for x in c] == [a, b]
    assert c[0]["distance_m"] <= c[0]["radius"] < c[1]["distance_m"]  # A trong 80m, B ngoài
    assert _get(client, a, "ja")["cache_hit"] is False and _get(client, b, "ja")["cache_hit"] is True


# ----------------------------- /discover ------------------------------------
def test_discover_upsert_duplicate_and_empty(client):
    r = _discover(client, [_wiki()]).json()
    assert r["cache_hit"] is False and r["candidates"][0]["source"] == "wikimedia"
    _discover(client, [_wiki(description_source="Moi hon")])
    rows = [x for x in client.get("/locations").json() if x["source_page_id"] == 111]
    assert len(rows) == 1 and rows[0]["description_source"] == "Moi hon"  # không trùng
    assert _discover(client, [], True).json() == {"candidates": [], "cache_hit": True}


@pytest.mark.parametrize("code", ["timeout", "rate_limited", "upstream_error"])
def test_discover_upstream_error_is_503_not_empty_without_stored_data(client, code):
    import main
    _loc(client)  # điểm manual không được dùng làm cache Wikimedia
    with patch("main.wikimedia_discover", side_effect=main.WikimediaError(code, "loi")):
        r = client.get("/locations/discover", params=GPS)
    assert r.status_code == 503 and r.json()["detail"]["code"] == code


def test_discover_upstream_error_serves_stored_data_as_stale(client):
    import main
    _discover(client, [_wiki()])
    with patch("main.wikimedia_discover", side_effect=main.WikimediaError("rate_limited", "429")):
        r = client.get("/locations/discover", params={"latitude": 10.7721, "longitude": 106.698})
    b = r.json()
    assert r.status_code == 200 and b["stale"] is True and b["upstream_error"] == "rate_limited"
    assert b["candidates"][0]["last_synced_at"].startswith("2026-10-01T08:00")  # metadata cũ


def test_wiki_cache_also_caches_empty_area(client):
    import main
    main._wiki_cache.clear()
    with patch("main._wiki_request", return_value={"query": {"geosearch": []}}) as req:
        first = main.wikimedia_discover(10.0, 106.0, 500, 5)
        second = main.wikimedia_discover(10.0001, 106.0001, 500, 5)  # cùng ô ~100m
    assert first == ([], False) and second == ([], True) and req.call_count == 1


def test_discover_cache_hit_keeps_last_synced_at_and_fetch_refreshes_it(client):
    _discover(client, [_wiki()])
    t2 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    _discover(client, [_wiki(last_synced_at=t2, description_source="Khac")], cache_hit=True)
    row = [x for x in client.get("/locations").json() if x["source_page_id"] == 111][0]
    assert row["last_synced_at"].startswith("2026-10-01T08:00") and row["description_source"] == "Mo ta Wikipedia"
    _discover(client, [_wiki(last_synced_at=t2)])  # fetch thật -> refresh
    row = [x for x in client.get("/locations").json() if x["source_page_id"] == 111][0]
    assert row["last_synced_at"].startswith("2026-10-01T09:00")


def test_concurrent_discovery_unique_conflict_creates_no_duplicate(client):
    import main
    _discover(client, [_wiki()])
    real, calls = main._find_wikimedia, {"n": 0}

    def racing(db, page_id, lang="vi"):  # lần đọc đầu "không thấy" -> insert đụng unique
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, page_id, lang)

    with patch("main._find_wikimedia", side_effect=racing):
        r = _discover(client, [_wiki()])
    assert r.status_code == 200 and len(r.json()["candidates"]) == 1 and calls["n"] == 2
    assert len([x for x in client.get("/locations").json() if x["source_page_id"] == 111]) == 1


# ------------------------------ migration -----------------------------------
def _week2_db():
    conn = sqlite3.connect("content.db")
    conn.execute("CREATE TABLE locations (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "description_vi TEXT NOT NULL, target_languages TEXT NOT NULL)")
    conn.execute("INSERT INTO locations VALUES (1, 'Cu', 'Mo ta cu', 'en')")
    conn.commit()
    conn.close()


def test_migration_from_week2_keeps_data_idempotent_and_adds_narration_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _week2_db()
    main = _load(tmp_path, monkeypatch)  # import tự chạy migration
    assert main.run_migrations(verbose=False) == []  # chạy lại: không làm gì
    conn = sqlite3.connect("content.db")
    row = conn.execute("SELECT id, name, description_source, latitude, radius, source, is_ready "
                       "FROM locations").fetchone()
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    fks = conn.execute("PRAGMA foreign_key_list(narration_cache)").fetchall()
    conn.close()
    assert row == (1, "Cu", "Mo ta cu", None, 80, "manual", 0)  # không gán tọa độ 0,0
    assert versions == {2, 3} and fks and fks[0][2] == "locations"
    assert len([f for f in os.listdir(".") if ".bak-" in f]) == 1  # chỉ sao lưu 1 lần, lúc có migration


def test_foreign_keys_are_enforced(client):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    import main
    with main.SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        now = _now()
        db.add(main.NarrationCache(location_id=99999, requested_lang="vi", lang="vi", text="x",
                                   audio_status="text_ready", created_at=now, updated_at=now,
                                   expires_at=now + timedelta(days=1)))
        with pytest.raises(IntegrityError):
            db.commit()


# --------------------------- narration cache --------------------------------
def test_narration_miss_hit_404_and_default_ttl(client):
    loc = _loc(client)
    assert _get(client, loc, "vi") == {"cache_hit": False, "narration": None}
    assert client.get("/locations/99999/narrations/vi").status_code == 404
    assert _put(client, 99999, "vi", _text()).status_code == 404
    assert client.get(f"/locations/{loc}/narrations/xx").status_code == 422
    _put(client, loc, "vi", _text())
    n = _get(client, loc, "vi")["narration"]
    assert n["requested_lang"] == "vi" and n["audio_status"] == "text_ready" and "audio_url" not in n
    delta = datetime.fromisoformat(n["expires_at"]) - _now()
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_narration_ttl_configurable_and_explicit_expiry_must_be_future(client, monkeypatch):
    monkeypatch.setenv("NARRATION_TTL_DAYS", "1")
    loc = _loc(client)
    n = _put(client, loc, "vi", _text()).json()["narration"]
    assert timedelta(hours=23) < datetime.fromisoformat(n["expires_at"]) - _now() <= timedelta(days=1)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _put(client, loc, "vi", _text(expires_at=past)).status_code == 422


def test_expired_narration_is_miss_and_not_counted_ready(client):
    import main
    loc = _loc(client)
    _put(client, loc, "vi", _audio("vi"))
    with main.SessionLocal() as db:
        db.query(main.NarrationCache).one().expires_at = _now() - timedelta(seconds=1)
        db.commit()
    assert _get(client, loc, "vi")["cache_hit"] is False
    r = client.patch(f"/locations/{loc}/mark-ready").json()
    assert r["is_ready"] is False and r["audio_ready_languages"] == []


def test_fallback_keeps_real_language(client):
    loc = _loc(client)
    _put(client, loc, "ja", _text("vi", is_fallback=True, reason="target_language_unavailable"))
    n = _get(client, loc, "ja")["narration"]
    assert (n["requested_lang"], n["lang"], n["is_fallback"]) == ("ja", "vi", True)


@pytest.mark.parametrize("body", [
    _text("vi"),                                              # lang khác mà không đánh dấu fallback
    _text("ja", is_fallback=True, reason="empty_extract"),    # fallback nhưng lang trùng
    _text("vi", is_fallback=True, reason=None),               # fallback thiếu reason
    _text("ja", reason="empty_extract"),                      # không fallback mà có reason
    _text("ja", requested_lang="en"),                         # requested_lang lệch path
    _text("ja", voice="en-US-GuyNeural"),                     # voice lệch lang
    _text("ja", text="   "), _text("xx"), _text("ja", audio_status="done"),
    _text("ja", source_url="ftp://x"),
    {**_text("ja"), "audio_url": "http://content:8001/a.mp3"},  # không được lưu URL
    _audio("ja", audio_filename="../evil.mp3"), _audio("ja", audio_filename="a/b.mp3"),
    _audio("ja", audio_filename="a\\b.mp3"), _audio("ja", audio_filename="C:x.mp3"),
    _audio("ja", audio_filename="x.wav"), _audio("ja", audio_filename="http://h/x.mp3"),
    _audio("ja", audio_filename=None), _audio("ja", voice=None), _audio("ja", text_hash=None),
    _text("ja", audio_filename="x.mp3"),                      # text_ready không được có file
])
def test_narration_put_validation(client, body):
    loc = _loc(client)
    assert _put(client, loc, "ja", body).status_code == 422
    assert _get(client, loc, "ja")["cache_hit"] is False  # không lưu gì


def test_upsert_same_key_twice_keeps_one_row(client):
    import main
    loc = _loc(client)
    first = _put(client, loc, "ja", _text("ja", text="Ban 1")).json()
    second = _put(client, loc, "ja", _audio("ja", text="Ban 2")).json()
    assert first["created"] is True and second["created"] is False
    assert second["narration"]["created_at"] == first["narration"]["created_at"]
    with main.SessionLocal() as db:
        rows = db.query(main.NarrationCache).all()
        assert len(rows) == 1 and rows[0].text == "Ban 2" and rows[0].audio_status == "audio_ready"


def test_mark_ready_computed_from_cache_per_language(client):
    loc = _loc(client)
    assert client.patch(f"/locations/{loc}/mark-ready", json={"is_ready": True}).json()["is_ready"] is False
    _put(client, loc, "en", _text("en"))  # chỉ text -> chưa sẵn sàng
    assert client.patch(f"/locations/{loc}/mark-ready").json()["is_ready"] is False
    _put(client, loc, "ja", _audio("ja"))
    r = client.patch(f"/locations/{loc}/mark-ready").json()
    assert r["is_ready"] is True and r["audio_ready_languages"] == ["ja"]
    assert _get(client, loc, "en")["narration"]["audio_status"] == "text_ready"  # is_ready không thay cache theo ngôn ngữ
    _put(client, loc, "ja", _text("ja"))  # hạ xuống text_ready -> is_ready tự về false
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False
    assert client.patch("/locations/99999/mark-ready").status_code == 404


def test_delete_narration_is_idempotent_and_updates_is_ready(client):
    loc = _loc(client)
    _put(client, loc, "ja", _audio("ja"))
    assert client.delete(f"/locations/{loc}/narrations/ja").json()["deleted"] is True
    assert _get(client, loc, "ja")["cache_hit"] is False
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False
    assert client.delete(f"/locations/{loc}/narrations/ja").json()["deleted"] is False  # lần 2 vẫn 200
    assert client.delete("/locations/99999/narrations/ja").status_code == 404
    assert client.delete(f"/locations/{loc}/narrations/zh").status_code == 422


# ----------------- invalidation, xóa location, restart ----------------------
def test_updating_content_or_coordinates_invalidates_cache(client):
    loc = _loc(client)
    base = {"name": "Dia diem", "description_vi": "Noi dung goc", "target_languages": "en,ja", **GPS}
    _put(client, loc, "ja", _audio("ja"))
    client.put(f"/locations/{loc}", json={**base, "name": "Ten khac", "radius": 50})  # đổi tên/radius: giữ cache
    assert _get(client, loc, "ja")["cache_hit"] is True
    client.put(f"/locations/{loc}", json={**base, "description_vi": "Moi"})  # đổi nội dung: xóa cache
    assert _get(client, loc, "ja")["cache_hit"] is False
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False
    _put(client, loc, "vi", _text("vi"))
    client.put(f"/locations/{loc}", json={**base, "description_vi": "Moi", "latitude": 10.80})  # đổi tọa độ
    assert _get(client, loc, "vi")["cache_hit"] is False


def test_discovery_refresh_with_changed_content_invalidates_narrations(client):
    loc = _discover(client, [_wiki()]).json()["candidates"][0]["id"]
    _put(client, loc, "ja", _audio("ja"))
    _discover(client, [_wiki()])  # bài không đổi -> giữ cache
    assert _get(client, loc, "ja")["cache_hit"] is True
    _discover(client, [_wiki(description_source="Bai da sua")])
    assert _get(client, loc, "ja")["cache_hit"] is False
    assert client.get(f"/locations/{loc}").json()["is_ready"] is False


def test_deleting_location_removes_cache_without_orphans(client):
    from sqlalchemy import text
    import main
    loc, other = _loc(client), _loc(client, name="Khac")
    for lid in (loc, other):
        _put(client, lid, "ja", _audio("ja"))
    _put(client, loc, "en", _text("en"))
    client.delete(f"/locations/{loc}")
    with main.SessionLocal() as db:
        assert [n.location_id for n in db.query(main.NarrationCache).all()] == [other]
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_text_ready_to_audio_ready_survives_restart(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    with TestClient(_load(tmp_path, monkeypatch).app) as c:
        loc = _loc(c)
        fb = {"is_fallback": True, "reason": "target_language_unavailable"}
        _put(c, loc, "ja", _text("vi", **fb))  # TTS lỗi: chỉ có text
        assert c.patch(f"/locations/{loc}/mark-ready").json()["is_ready"] is False
        _put(c, loc, "ja", _audio("vi", **fb))
        assert c.patch(f"/locations/{loc}/mark-ready").json()["audio_ready_languages"] == ["ja"]
    with TestClient(_load(tmp_path, monkeypatch).app) as c:  # "restart"
        n = _get(c, loc, "ja")
        assert n["cache_hit"] is True and n["narration"]["lang"] == "vi"
        assert c.get(f"/locations/{loc}").json()["is_ready"] is True
