"""content-service (port 8001). Đọc 5 khu vực từ trên xuống:
1 MIGRATION | 2 GPS | 3 WIKIMEDIA | 4 SCHEMA (JSON vào/ra) | 5 ROUTES."""
from __future__ import annotations

import math, os, re, shutil, sqlite3, time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Base, DB_PATH, Location, NarrationCache, SessionLocal, engine

# ============ 1. MIGRATION (create_all không tự thêm cột vào bảng cũ) ============
NEW_COLUMNS = [("description_source", "TEXT"), ("latitude", "REAL"), ("longitude", "REAL"),
               ("radius", "REAL"), ("source_lang", "TEXT"), ("source", "TEXT"),
               ("source_title", "TEXT"), ("source_page_id", "INTEGER"), ("source_url", "TEXT"),
               ("last_synced_at", "TEXT"), ("is_ready", "INTEGER")]


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _migrate_v2(conn):
    """Tuần 3: thêm cột GPS + nguồn, backfill record cũ (không gán tọa độ 0,0), unique index."""
    if not _has_table(conn, "locations"):
        return  # DB mới: create_all() tạo đủ cột
    have = {r[1] for r in conn.execute("PRAGMA table_info(locations)")}
    for col, typ in NEW_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE locations ADD COLUMN {col} {typ}")
    conn.execute("""UPDATE locations SET description_source = description_vi,
        source_lang = COALESCE(source_lang, 'vi'), source = COALESCE(source, 'manual'),
        radius = COALESCE(radius, 80), is_ready = COALESCE(is_ready, 0)
        WHERE description_source IS NULL""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_locations_source "
                 "ON locations (source, source_lang, source_page_id)")


def _migrate_v3(conn):
    """Tuần 4: bảng narration_cache (FK tới locations, xóa theo cascade)."""
    if not _has_table(conn, "locations"):
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS narration_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_id INTEGER NOT NULL REFERENCES locations (id) ON DELETE CASCADE,
        requested_lang VARCHAR NOT NULL, lang VARCHAR NOT NULL, text TEXT NOT NULL,
        source_url VARCHAR, target_title VARCHAR, is_fallback BOOLEAN NOT NULL DEFAULT 0,
        reason VARCHAR, voice VARCHAR, text_hash VARCHAR, audio_filename VARCHAR,
        audio_status VARCHAR NOT NULL, created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL, expires_at DATETIME NOT NULL,
        CONSTRAINT ux_narration_location_lang UNIQUE (location_id, requested_lang))""")


MIGRATIONS = {2: _migrate_v2, 3: _migrate_v3}  # thêm version mới = thêm 1 dòng


def run_migrations(verbose: bool = True) -> list:
    """Chạy các version chưa áp dụng (mỗi version 1 transaction). Chạy lặp an toàn."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [v for v in sorted(MIGRATIONS) if v not in done]
        if not pending:
            return []
        if os.path.getsize(DB_PATH) > 0:  # sao lưu CHỈ khi có version chờ
            backup = f"{DB_PATH}.bak-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
            shutil.copy2(DB_PATH, backup)
            verbose and print(f"[migrate] Đã sao lưu DB -> {backup}")
        for v in pending:
            conn.execute("BEGIN")
            try:
                MIGRATIONS[v](conn)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                             (v, datetime.now(timezone.utc).isoformat()))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            verbose and print(f"[migrate] Đã áp dụng version {v}.")
        return pending
    finally:
        conn.close()


# ============ 2. GPS ============
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Khoảng cách 2 điểm GPS, đơn vị MÉT."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def validate_coords(lat: Optional[float], lon: Optional[float]) -> None:
    if lat is None or lon is None:
        return
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("Tọa độ phải là số hữu hạn")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("latitude phải trong [-90,90], longitude trong [-180,180]")


# ============ 3. WIKIMEDIA (cache RAM 60s theo ô ~100m) ============
WIKI_API_URL = "https://vi.wikipedia.org/w/api.php"
WIKI_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
WIKI_CACHE_TTL_SECONDS = 60
_wiki_cache: dict = {}


class WikimediaError(Exception):
    """code: 'timeout' | 'rate_limited' | 'upstream_error'."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _wiki_request(client: httpx.Client, params: dict) -> dict:
    """1 request tới Wikimedia, retry tối đa 1 lần khi 429/5xx (tôn trọng Retry-After)."""
    ua = os.environ.get("WIKIMEDIA_USER_AGENT") or "content-service-do-an-cnpm/1.0"
    for attempt in (1, 2):
        try:
            resp = client.get(WIKI_API_URL, params=params, headers={"User-Agent": ua})
        except httpx.TimeoutException as exc:
            raise WikimediaError("timeout", "Wikimedia không phản hồi kịp") from exc
        except httpx.RequestError as exc:
            raise WikimediaError("upstream_error", f"Lỗi kết nối: {exc}") from exc
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt == 1:
            wait = resp.headers.get("Retry-After", "")
            time.sleep(min(float(wait), 3.0) if wait.isdigit() else 1.0)
            continue
        break
    if resp.status_code == 429:
        raise WikimediaError("rate_limited", "Wikimedia trả 429 quá số lần retry")
    if resp.status_code >= 400:
        raise WikimediaError("upstream_error", f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise WikimediaError("upstream_error", "Phản hồi không phải JSON") from exc


def wikimedia_discover(lat: float, lon: float, radius: float, limit: int):
    """Trả (candidates, cache_hit). Bỏ bài thiếu tọa độ/giới thiệu; GPS lấy từ bài, không từ người dùng."""
    key = (round(lat, 3), round(lon, 3), radius, limit)
    cached = _wiki_cache.get(key)
    if cached and cached[1] > time.monotonic():
        return cached[0], True

    with httpx.Client(timeout=WIKI_TIMEOUT) as client:
        geo = _wiki_request(client, {
            "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}",
            "gsradius": int(radius), "gsnamespace": 0, "gslimit": limit, "format": "json"})
        geo_by_id = {g["pageid"]: g for g in geo.get("query", {}).get("geosearch", []) if "pageid" in g}
        pages = {}
        if geo_by_id:
            ext = _wiki_request(client, {
                "action": "query", "prop": "extracts|info", "exintro": 1, "explaintext": 1,
                "inprop": "url", "pageids": "|".join(map(str, geo_by_id)), "format": "json"})
            pages = ext.get("query", {}).get("pages", {})

    synced_at = datetime.now(timezone.utc)
    candidates = [{
        "source_page_id": p["pageid"], "source_title": p.get("title"),
        "description_source": p["extract"], "source_url": p.get("fullurl") or p.get("canonicalurl"),
        "latitude": geo_by_id[p["pageid"]].get("lat"), "longitude": geo_by_id[p["pageid"]].get("lon"),
        "last_synced_at": synced_at,
    } for p in pages.values() if p.get("extract") and p.get("pageid") in geo_by_id]
    _wiki_cache[key] = (candidates, time.monotonic() + WIKI_CACHE_TTL_SECONDS)  # cache cả khi rỗng
    return candidates, False


# ============ 4. SCHEMA (Pydantic: JSON vào/ra) ============
class LocationCreate(BaseModel):
    name: str
    description_vi: str
    target_languages: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = Field(80, gt=0)

    @field_validator("name", "description_vi", "target_languages")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Không được để trống")
        return v

    @model_validator(mode="after")
    def _coords(self):
        validate_coords(self.latitude, self.longitude)
        return self


class LocationResponse(BaseModel):
    id: int
    name: str
    description_vi: str
    target_languages: str
    description_source: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius: Optional[float] = None
    source_lang: Optional[str] = None
    source: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    is_ready: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


class DiscoverCandidate(BaseModel):
    """Location rút gọn cho /discover; /nearby thêm distance_m."""
    id: int
    name: str
    description_source: Optional[str] = None
    latitude: float
    longitude: float
    radius: Optional[float] = None
    source: Optional[str] = None
    source_lang: Optional[str] = None
    source_title: Optional[str] = None
    source_page_id: Optional[int] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    is_ready: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


class NearbyCandidate(DiscoverCandidate):
    distance_m: float


VALID_LANGS = {"vi", "en", "ja"}
AUDIO_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$", re.IGNORECASE)


def utcnow() -> datetime:
    """Giờ UTC không tzinfo (SQLite lưu datetime không múi giờ)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def narration_ttl() -> timedelta:
    try:
        days = float(os.environ.get("NARRATION_TTL_DAYS", "7"))
    except ValueError:
        days = 7.0
    return timedelta(days=days if days > 0 else 7.0)


class NarrationPut(BaseModel):
    """Body PUT narration. extra=forbid: trường lạ (vd audio_url) -> 422."""
    model_config = ConfigDict(extra="forbid")

    requested_lang: Optional[str] = None  # nếu gửi phải khớp {language} trên path
    lang: str
    text: str
    source_url: Optional[str] = None
    target_title: Optional[str] = None
    is_fallback: bool = False
    reason: Optional[str] = None
    voice: Optional[str] = None
    text_hash: Optional[str] = None
    audio_filename: Optional[str] = None
    audio_status: str
    expires_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _check(self):
        for lang in (self.requested_lang, self.lang):
            if lang is not None and lang not in VALID_LANGS:
                raise ValueError(f"Ngôn ngữ phải thuộc {sorted(VALID_LANGS)}")
        if not self.text.strip() or len(self.text) > 10000:
            raise ValueError("text không được rỗng và tối đa 10000 ký tự")
        if self.audio_status not in ("text_ready", "audio_ready"):
            raise ValueError("audio_status phải là text_ready hoặc audio_ready")
        fn = self.audio_filename
        if fn is not None and (".." in fn or not AUDIO_FILENAME_RE.match(fn)):
            raise ValueError("audio_filename phải là tên file .mp3 thuần (không chứa đường dẫn)")
        if self.source_url is not None and not re.match(r"^https?://\S+$", self.source_url):
            raise ValueError("source_url phải là URL http/https")
        for v in (self.target_title, self.reason, self.voice, self.text_hash):
            if v is not None and not v.strip():
                raise ValueError("Không được là chuỗi rỗng (hãy dùng null)")
        if self.voice and not self.voice.startswith(self.lang + "-"):
            raise ValueError(f"voice '{self.voice}' không khớp lang '{self.lang}'")
        if self.is_fallback != bool(self.reason):
            raise ValueError("is_fallback=true thì phải có reason, false thì reason=null")
        if self.audio_status == "audio_ready":
            if not (fn and self.voice and self.text_hash):
                raise ValueError("audio_ready cần đủ audio_filename, voice, text_hash")
        elif fn is not None:
            raise ValueError("text_ready không được có audio_filename")
        return self


def narration_to_dict(n: NarrationCache) -> dict:
    return {c.name: getattr(n, c.name) for c in NarrationCache.__table__.columns if c.name != "id"}


def _find_narration(db: Session, location_id: int, requested_lang: str):
    return db.execute(select(NarrationCache).where(
        NarrationCache.location_id == location_id,
        NarrationCache.requested_lang == requested_lang)).scalar_one_or_none()


def _ready_languages(db: Session, location_id: int) -> list:
    """Các ngôn ngữ audio_ready còn hạn."""
    db.flush()  # session autoflush=False -> flush tay để đọc đúng
    return list(db.execute(select(NarrationCache.requested_lang).where(
        NarrationCache.location_id == location_id,
        NarrationCache.audio_status == "audio_ready",
        NarrationCache.expires_at > utcnow()).order_by(NarrationCache.requested_lang)).scalars())


def _recompute_is_ready(db: Session, location: Location) -> bool:
    """is_ready = có ÍT NHẤT 1 narration audio_ready còn hạn (bất kỳ ngôn ngữ nào)."""
    location.is_ready = bool(_ready_languages(db, location.id))
    return location.is_ready


def _invalidate_narrations(db: Session, location: Location) -> None:
    db.execute(delete(NarrationCache).where(NarrationCache.location_id == location.id))
    location.is_ready = False


# ============ 5. FASTAPI APP + ROUTES ============
app = FastAPI(title="content-service")
run_migrations()                       # DB cũ (tuần 2)
Base.metadata.create_all(bind=engine)  # DB mới hoàn toàn


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_location_or_404(db: Session, location_id: int) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
    return location


def _check_language(language: str) -> None:
    if language not in VALID_LANGS:
        raise HTTPException(status_code=422, detail=f"language phải thuộc {sorted(VALID_LANGS)}")


def _check_gps(latitude: float, longitude: float) -> None:
    try:
        validate_coords(latitude, longitude)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/health")
def health_check():
    return {"service": "content-service", "status": "ok"}


# 2 route TĨNH phải đứng TRƯỚC /locations/{location_id}, nếu không "nearby" bị bắt làm id.
@app.get("/locations/nearby")
def locations_nearby(latitude: float = Query(...), longitude: float = Query(...),
                     max_distance: float = Query(500, gt=0), db: Session = Depends(get_db)):
    """Tìm trong dữ liệu ĐÃ LƯU (không gọi Wikimedia); mọi điểm có GPS trong max_distance."""
    _check_gps(latitude, longitude)
    rows = db.execute(select(Location).where(
        Location.latitude.isnot(None), Location.longitude.isnot(None))).scalars()
    found = []
    for loc in rows:
        d = haversine_m(latitude, longitude, loc.latitude, loc.longitude)
        if d <= max_distance:
            base = DiscoverCandidate.model_validate(loc).model_dump()
            found.append(NearbyCandidate(**base, distance_m=d))
    found.sort(key=lambda c: c.distance_m)
    return {"candidates": [c.model_dump() for c in found]}


def _stored_wikimedia_near(db: Session, lat: float, lon: float, radius: float, limit: int):
    """Bài Wikimedia ĐÃ LƯU trong SQLite nằm trong radius, gần nhất trước."""
    rows = db.execute(select(Location).where(
        Location.source == "wikimedia", Location.latitude.isnot(None),
        Location.longitude.isnot(None))).scalars()
    pairs = [(haversine_m(lat, lon, r.latitude, r.longitude), r) for r in rows]
    return [r for d, r in sorted((p for p in pairs if p[0] <= radius), key=lambda p: p[0])[:limit]]


@app.get("/locations/discover")
def locations_discover(latitude: float = Query(...), longitude: float = Query(...),
                       radius: float = Query(500, gt=0), limit: int = Query(5, gt=0, le=50),
                       db: Session = Depends(get_db)):
    """Tìm bài Wikipedia quanh tọa độ rồi UPSERT vào CSDL."""
    _check_gps(latitude, longitude)
    try:
        raw, cache_hit = wikimedia_discover(latitude, longitude, radius, limit)
    except WikimediaError as exc:
        # Upstream lỗi: có dữ liệu đã lưu -> phục vụ (stale, không sửa DB); không có -> 503.
        stored = _stored_wikimedia_near(db, latitude, longitude, radius, limit)
        if stored:
            return {"candidates": [DiscoverCandidate.model_validate(x).model_dump() for x in stored],
                    "cache_hit": True, "stale": True, "upstream_error": exc.code}
        return JSONResponse(status_code=503,
                            content={"detail": {"code": exc.code, "message": exc.message}})
    result = [DiscoverCandidate.model_validate(_upsert_wikimedia_location(db, item, not cache_hit)).model_dump()
              for item in raw]
    return {"candidates": result, "cache_hit": cache_hit}


def _find_wikimedia(db: Session, source_page_id: int, source_lang: str = "vi"):
    return db.execute(select(Location).where(
        Location.source == "wikimedia", Location.source_lang == source_lang,
        Location.source_page_id == source_page_id)).scalar_one_or_none()


def _upsert_wikimedia_location(db: Session, item: dict, fetched_now: bool = True) -> Location:
    """Thêm/cập nhật 1 bài Wikimedia (chống trùng theo pageid).
    fetched_now=False (chỉ đọc cache 60s): record có sẵn giữ nguyên, không đổi last_synced_at."""
    existing = _find_wikimedia(db, item["source_page_id"])
    if existing:
        if not fetched_now:
            return existing
        changed = (existing.description_source, existing.latitude, existing.longitude) != (
            item["description_source"], item["latitude"], item["longitude"])
        existing.name = existing.source_title = item["source_title"]
        existing.description_source = item["description_source"]
        existing.source_url = item["source_url"]
        existing.latitude, existing.longitude = item["latitude"], item["longitude"]
        existing.last_synced_at = item["last_synced_at"]
        existing.radius = existing.radius or 80
        if changed:  # nội dung/tọa độ đổi -> narration đã cache không còn đúng
            _invalidate_narrations(db, existing)
        db.commit()
        db.refresh(existing)
        return existing

    new_loc = Location(
        name=item["source_title"], description_vi=item["description_source"], target_languages="",
        description_source=item["description_source"], latitude=item["latitude"],
        longitude=item["longitude"], radius=80, source_lang="vi", source="wikimedia",
        source_title=item["source_title"], source_page_id=item["source_page_id"],
        source_url=item["source_url"], last_synced_at=item["last_synced_at"], is_ready=False)
    db.add(new_loc)
    try:
        db.commit()
    except IntegrityError:  # 2 discovery đồng thời cùng pageid: rollback rồi đọc lại bản của request thắng
        db.rollback()
        return _find_wikimedia(db, item["source_page_id"])
    db.refresh(new_loc)
    return new_loc


# --- CRUD ---
@app.post("/locations", response_model=LocationResponse)
def create_location(body: LocationCreate, db: Session = Depends(get_db)):
    loc = Location(
        name=body.name, description_vi=body.description_vi, target_languages=body.target_languages,
        description_source=body.description_vi, latitude=body.latitude, longitude=body.longitude,
        radius=body.radius if body.latitude is not None else None,
        source_lang="vi", source="manual", is_ready=False)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@app.get("/locations", response_model=List[LocationResponse])
def get_locations(db: Session = Depends(get_db)):
    return db.execute(select(Location)).scalars().all()


@app.get("/locations/{location_id}", response_model=LocationResponse)
def get_location(location_id: int, db: Session = Depends(get_db)):
    return _get_location_or_404(db, location_id)


@app.put("/locations/{location_id}", response_model=LocationResponse)
def update_location(location_id: int, body: LocationCreate, db: Session = Depends(get_db)):
    loc = _get_location_or_404(db, location_id)
    old = (loc.description_source, loc.latitude, loc.longitude)
    loc.name, loc.description_vi, loc.target_languages = body.name, body.description_vi, body.target_languages
    loc.description_source = body.description_vi
    loc.latitude, loc.longitude = body.latitude, body.longitude
    loc.radius = body.radius if body.latitude is not None else None
    if old != (loc.description_source, loc.latitude, loc.longitude):
        _invalidate_narrations(db, loc)  # chỉ đổi name/target_languages/radius thì giữ cache
    db.commit()
    db.refresh(loc)
    return loc


@app.delete("/locations/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    loc = _get_location_or_404(db, location_id)
    db.execute(delete(NarrationCache).where(NarrationCache.location_id == location_id))
    db.delete(loc)
    db.commit()
    return {"deleted": True, "id": location_id}


# --- Narration cache + is_ready ---
@app.get("/locations/{location_id}/narrations/{language}")
def get_narration(location_id: int, language: str, db: Session = Depends(get_db)):
    _get_location_or_404(db, location_id)
    _check_language(language)
    row = _find_narration(db, location_id, language)
    if row is None or row.expires_at <= utcnow():  # chưa có hoặc hết hạn = miss
        return {"cache_hit": False, "narration": None}
    return {"cache_hit": True, "narration": narration_to_dict(row)}


@app.put("/locations/{location_id}/narrations/{language}")
def put_narration(location_id: int, language: str, body: NarrationPut, db: Session = Depends(get_db)):
    """Upsert 1 narration theo (location_id, requested_lang=language)."""
    location = _get_location_or_404(db, location_id)
    _check_language(language)
    if body.requested_lang not in (None, language):
        raise HTTPException(422, "requested_lang trong body phải khớp {language} trên path")
    if body.is_fallback == (body.lang == language):
        raise HTTPException(422, "is_fallback=true khi và chỉ khi lang khác requested_lang")

    now = utcnow()
    expires_at = body.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at <= now:
            raise HTTPException(422, "expires_at phải ở tương lai")
    values = body.model_dump(exclude={"requested_lang", "expires_at"})
    values.update(expires_at=expires_at or now + narration_ttl(), updated_at=now)

    def save(row):
        for k, v in values.items():
            setattr(row, k, v)
        _recompute_is_ready(db, location)
        db.commit()

    row, created = _find_narration(db, location_id, language), False
    try:
        if row is None:
            row, created = NarrationCache(location_id=location_id, requested_lang=language,
                                          created_at=now, **values), True
            db.add(row)
            _recompute_is_ready(db, location)
            db.commit()
        else:
            save(row)
    except IntegrityError:  # 2 PUT cùng key đồng thời (hoặc location vừa bị xóa)
        db.rollback()
        location = db.get(Location, location_id)
        if location is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy địa điểm")
        row, created = _find_narration(db, location_id, language), False
        if row is None:
            raise HTTPException(status_code=409, detail="Xung đột khi lưu cache, thử lại")
        save(row)
    db.refresh(row)
    return {"created": created, "narration": narration_to_dict(row)}


@app.delete("/locations/{location_id}/narrations/{language}")
def delete_narration(location_id: int, language: str, db: Session = Depends(get_db)):
    """B loại cache hỏng/hết hạn. Idempotent: key chưa có vẫn 200 (deleted=false)."""
    location = _get_location_or_404(db, location_id)
    _check_language(language)
    row = _find_narration(db, location_id, language)
    if row is not None:
        db.delete(row)
    _recompute_is_ready(db, location)
    db.commit()
    return {"deleted": row is not None, "id": location_id, "language": language}


@app.patch("/locations/{location_id}/mark-ready")
def mark_ready(location_id: int, db: Session = Depends(get_db)):
    """Tính lại is_ready TỪ CACHE (không nhận giá trị từ client)."""
    location = _get_location_or_404(db, location_id)
    ready = _recompute_is_ready(db, location)
    db.commit()
    return {"id": location_id, "is_ready": ready,
            "audio_ready_languages": _ready_languages(db, location_id)}
