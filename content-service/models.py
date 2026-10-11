"""
Định nghĩa kết nối CSDL (SQLite) và model Location.

Tuần 2: id, name, description_vi, target_languages.
Tuần 3: bổ sung dữ liệu địa lý (GPS) + metadata nguồn Wikimedia, để phục vụ
2 endpoint mới /locations/nearby (tìm trong các địa điểm đã lưu) và
/locations/discover (tìm bài viết Wikimedia quanh 1 tọa độ rồi lưu lại).
Tuần 4: thêm bảng NarrationCache - kho cache nội dung đa ngôn ngữ + thông tin
audio của từng địa điểm (B ghi/đọc qua REST của content-service, không mở
trực tiếp file content.db).
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

DB_PATH = os.getenv("DB_PATH", "content.db")
SQLALCHEMY_DATABASE_URL = (
    f"sqlite:///{DB_PATH}" if os.path.isabs(DB_PATH) else f"sqlite:///./{DB_PATH}"
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """
    SQLite MẶC ĐỊNH TẮT kiểm tra khóa ngoại (FOREIGN KEY) - phải bật bằng
    PRAGMA cho TỪNG kết nối. Không bật thì narration_cache.location_id trỏ
    tới location không tồn tại vẫn được ghi vào mà không báo lỗi.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Location(Base):
    """
    1 địa điểm. Có 2 "nguồn" (source):
      - "manual": người dùng tự nhập (CRUD tuần 2) -> có thể thiếu GPS.
      - "wikimedia": lấy từ Wikipedia qua /locations/discover -> luôn có
        source_page_id, source_title, source_url, bắt buộc có GPS.
    """

    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    # Giữ lại để tương thích ngược với tuần 2 (translate-audio-service của B
    # vẫn đang đọc field này).
    description_vi = Column(String, nullable=False)
    target_languages = Column(String, nullable=False)

    # ----- Tuần 3: nội dung mô tả theo nguồn gốc -----
    # Với record cũ (manual): description_source = description_vi (backfill).
    # Với record Wikimedia: description_source = đoạn giới thiệu lấy từ bài viết.
    description_source = Column(String, nullable=True)

    # ----- Tuần 3: tọa độ + bán kính kích hoạt audio (mét) -----
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    radius = Column(Float, nullable=True)  # mét, mặc định 80

    # ----- Tuần 3: metadata nguồn -----
    source_lang = Column(String, nullable=True)  # vd: "vi"
    source = Column(String, nullable=True)  # "manual" | "wikimedia"
    source_title = Column(String, nullable=True)  # tiêu đề CHÍNH XÁC trên wiki
    source_page_id = Column(Integer, nullable=True)  # pageid Wikipedia
    source_url = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)  # lần cuối đồng bộ (UTC)
    is_ready = Column(Boolean, nullable=True, default=False)

    __table_args__ = (
        # Chặn trùng: 1 bài Wikipedia (theo pageid + ngôn ngữ) chỉ được lưu
        # 1 lần. Record manual có source_page_id = NULL nên không bị chặn
        # bởi ràng buộc này (SQL coi NULL != NULL, nhiều NULL vẫn hợp lệ).
        UniqueConstraint(
            "source", "source_lang", "source_page_id", name="ux_locations_source"
        ),
    )


class NarrationCache(Base):
    """
    Kho cache nội dung thuyết minh cho 1 địa điểm theo 1 ngôn ngữ YÊU CẦU.

    - requested_lang: ngôn ngữ người dùng yêu cầu (vd "ja") - là phần của khóa.
    - lang: ngôn ngữ THỰC TẾ của `text`. Khác requested_lang khi fallback
      (không có bài tiếng Nhật -> dùng text tiếng Việt, is_fallback=True).
    - audio_status: "text_ready" (mới có text) | "audio_ready" (đã có MP3).
    - audio_filename: chỉ lưu TÊN FILE (basename .mp3), KHÔNG lưu URL/đường dẫn;
      Gateway tự ghép thành /audio/<filename> khi trả cho client.
    - expires_at: hết hạn thì coi như cache miss (mặc định 7 ngày).
    Unique(location_id, requested_lang): mỗi địa điểm chỉ 1 dòng cho mỗi ngôn ngữ.
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
            "location_id", "requested_lang", name="ux_narration_location_lang"
        ),
    )
