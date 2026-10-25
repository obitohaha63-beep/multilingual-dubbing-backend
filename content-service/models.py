
"""
Model dia diem, cache API cu va ban thuyet minh luu lau dai.

- Co DATABASE_URL: su dung PostgreSQL Supabase.
- Khong co DATABASE_URL: su dung SQLite cho CRUD/test cu.
"""

import os

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import declarative_base, sessionmaker


# ============================================================
# 1. CAU HINH DATABASE
# ============================================================

DB_PATH = os.getenv("DB_PATH", "content.db")
DATABASE_URL = os.getenv("DATABASE_URL")


def normalize_database_url(url: str) -> str:
    """
    Chuan hoa URL PostgreSQL de dung voi SQLAlchemy va psycopg 3.

    Cac dang duoc ho tro:
    - postgresql://             -> postgresql+psycopg://
    - postgres://               -> postgresql+psycopg://
    - postgresqlpsycopg://      -> postgresql+psycopg://
    - postgresql+psycopg://     -> giu nguyen
    """

    url = url.strip()

    if url.startswith("postgresqlpsycopg://"):
        url = url.replace(
            "postgresqlpsycopg://",
            "postgresql+psycopg://",
            1,
        )

    elif url.startswith("postgres://"):
        url = url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )

    elif url.startswith("postgresql://"):
        url = url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    # Cac dang PostgreSQL khac can duoc kiem tra ro rang,
    # tranh de SQLAlchemy bao loi dialect kho hieu.
    if url.startswith("postgresql+psycopg://"):
        return url

    if url.startswith("sqlite:"):
        return url

    raise ValueError(
        "DATABASE_URL khong hop le. "
        "Hay dung postgresql+psycopg://... cho PostgreSQL "
        "hoac sqlite:///... cho SQLite."
    )


if DATABASE_URL and DATABASE_URL.strip():
    SQLALCHEMY_DATABASE_URL = normalize_database_url(DATABASE_URL)
else:
    if os.path.isabs(DB_PATH):
        SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
    else:
        SQLALCHEMY_DATABASE_URL = f"sqlite:///./{DB_PATH}"


IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite:")


# ============================================================
# 2. TAO DATABASE ENGINE
# ============================================================

if IS_SQLITE:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={
            "check_same_thread": False,
        },
    )
else:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "prepare_threshold": None,
        },
    )


# ============================================================
# 3. BAT KIEM TRA KHOA NGOAI CHO SQLITE
# ============================================================

@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """
    SQLite mac dinh tat kiem tra khoa ngoai.
    Bat FOREIGN KEY cho tung ket noi SQLite.
    """

    if not IS_SQLITE:
        return

    cursor = dbapi_connection.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


# ============================================================
# 4. SESSION VA BASE
# ============================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# ============================================================
# 5. MODEL LOCATION
# ============================================================

class Location(Base):
    """
    Mot dia diem.

    source:
    - manual: nguoi dung tu nhap.
    - wikimedia: dia diem duoc lay tu Wikipedia.

    Dia diem manual co the khong co GPS.
    Dia diem Wikimedia can co GPS va metadata nguon.
    """

    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)

    # Giu tuong thich voi code cu.
    description_vi = Column(String, nullable=False)
    target_languages = Column(String, nullable=False)

    # Noi dung mo ta goc theo nguon.
    description_source = Column(String, nullable=True)

    # Toa do va ban kinh kich hoat audio.
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    radius = Column(Float, nullable=True)

    # Thong tin nguon du lieu.
    source_lang = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_title = Column(String, nullable=True)
    source_page_id = Column(Integer, nullable=True)
    source_url = Column(String, nullable=True)

    last_synced_at = Column(DateTime, nullable=True)
    is_ready = Column(Boolean, nullable=True, default=False)

    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_lang",
            "source_page_id",
            name="ux_locations_source",
        ),
    )


# ============================================================
# 6. MODEL NARRATION CACHE
# ============================================================

class NarrationCache(Base):
    """
    Cache ban thuyet minh theo dia diem va ngon ngu yeu cau.

    requested_lang:
        Ngon ngu nguoi dung yeu cau.

    lang:
        Ngon ngu thuc te cua noi dung text.

    audio_status:
        text_ready: da co noi dung text.
        audio_ready: da co file MP3.

    audio_filename:
        Chi luu ten file MP3, khong luu URL.

    expires_at:
        Thoi diem cache het han.

    Moi dia diem chi co mot dong cache cho moi requested_lang.
    """

    __tablename__ = "narration_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)

    location_id = Column(
        Integer,
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
    )

    requested_lang = Column(String, nullable=False)
    lang = Column(String, nullable=False)

    text = Column(Text, nullable=False)
    source_url = Column(String, nullable=True)
    target_title = Column(String, nullable=True)

    is_fallback = Column(Boolean, nullable=False, default=False)
    reason = Column(String, nullable=True)

    voice = Column(String, nullable=True)
    text_hash = Column(String, nullable=True)
    audio_filename = Column(String, nullable=True)

    audio_status = Column(String, nullable=False)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "location_id",
            "requested_lang",
            name="ux_narration_location_lang",
        ),
    )


# ============================================================
# 7. MODEL NARRATION
# ============================================================

class Narration(Base):
    """
    Ban thuyet minh luu lau dai.

    Khong co TTL.
    Moi phien ban noi dung la mot dong rieng.

    location_snapshot:
        JSON luu thong tin dia diem tai thoi diem tao ban thuyet minh.

    storage_path:
        Duong dan luu tru file audio, neu co.
    """

    __tablename__ = "narrations"

    id = Column(String, primary_key=True)

    location_id = Column(
        Integer,
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    language = Column(String, nullable=False)
    voice = Column(String, nullable=False)
    content_version = Column(String, nullable=False)

    location_snapshot = Column(Text, nullable=False)

    source_text = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    final_text = Column(Text, nullable=True)

    text_hash = Column(String, nullable=True)
    audio_filename = Column(String, nullable=True)
    storage_path = Column(String, nullable=True)

    status = Column(String, nullable=False, default="pending")

    attempts = Column(Integer, nullable=False, default=0)

    lease_token = Column(String, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    retry_at = Column(DateTime, nullable=True)

    error_code = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "location_id",
            "language",
            "voice",
            "content_version",
            name="ux_narrations_version",
        ),
    )
