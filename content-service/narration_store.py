"""Các thao tác database ngắn; không giữ transaction trong lúc gọi API ngoài."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models import Location, Narration, SessionLocal
from narration_clients import RETRYABLE_CODES


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_dict(row):
    return {c.name: getattr(row, c.name) for c in Narration.__table__.columns}


class NarrationStore:
    def __init__(self, sessions=SessionLocal):
        self.sessions = sessions

    def find(self, key):
        with self.sessions() as db:
            row = db.execute(select(Narration).filter_by(**key)).scalar_one_or_none()
            return as_dict(row) if row else None

    def claim(self, key, snapshot, max_attempts=3, lease_seconds=240):
        """UNIQUE + UPDATE có điều kiện: chỉ một instance lấy được quyền tạo.

        Token mới ngăn worker cũ ghi đè sau khi lease đã hết hạn.
        INSERT và UPDATE đều nằm trong cùng một transaction.
        """
        current = now()
        token = uuid.uuid4().hex
        with self.sessions.begin() as db:
            insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
            db.execute(insert(Narration).values(
                id=uuid.uuid4().hex, **key, location_snapshot=snapshot,
                status="pending", attempts=0, created_at=current, updated_at=current,
            ).on_conflict_do_nothing(index_elements=[
                "location_id", "language", "voice", "content_version"]))
            eligible = or_(
                Narration.status == "pending",
                and_(Narration.status == "failed", Narration.retry_at <= current,
                     Narration.error_code.in_(RETRYABLE_CODES)),
                and_(Narration.status == "processing", Narration.lease_until <= current))
            row = db.execute(update(Narration).filter_by(**key).where(
                eligible, Narration.attempts < max_attempts).values(
                    status="processing", lease_token=token,
                    lease_until=current + timedelta(seconds=lease_seconds),
                    attempts=Narration.attempts + 1, error_code=None,
                    updated_at=current).returning(Narration)).scalar_one_or_none()
            if row:
                return as_dict(row), True
            row = db.execute(select(Narration).filter_by(**key)).scalar_one()
            result = as_dict(row)
            # Worker chết ở lần cuối: vẫn phải trả trạng thái lỗi sau khi hết lease.
            if result["status"] == "processing" and result["lease_until"] <= current:
                db.execute(update(Narration).where(Narration.id == row.id,
                    Narration.status == "processing", Narration.lease_until <= current)
                    .values(status="failed", error_code="attempts_exhausted", updated_at=current))
                result.update(status="failed", error_code="attempts_exhausted")
            return result, False

    def save(self, row, **values):
        """Checkpoint chỉ được ghi bởi worker còn giữ lease hợp lệ."""
        with self.sessions.begin() as db:
            result = db.execute(update(Narration).where(
                Narration.id == row["id"], Narration.lease_token == row["lease_token"],
                Narration.status == "processing", Narration.lease_until > now()
            ).values(**values, updated_at=now()))
            if result.rowcount != 1:
                raise RuntimeError("lease_lost")
            if values.get("status") == "ready":
                db.execute(update(Location).where(Location.id == row["location_id"]).values(is_ready=True))
        row.update(values)

    def invalidate_audio(self, row):
        """Chỉ hạ ready nếu Storage xác nhận file mất; không xóa văn bản/version."""
        with self.sessions.begin() as db:
            db.execute(update(Narration).where(
                Narration.id == row["id"], Narration.status == "ready",
                Narration.updated_at == row["updated_at"]
            ).values(status="pending", attempts=0, updated_at=now()))
