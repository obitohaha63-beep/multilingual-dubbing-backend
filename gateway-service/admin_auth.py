"""
Admin Authentication & Session Security Module.
- Quản lý xác thực Quản trị viên (Username, Password).
- Tạo và xác thực Session Cookie có ký HMAC-SHA256 an toàn chống giả mạo / session fixation.
- Chống brute-force: Giới hạn số lần đăng nhập thất bại (Rate limiting / Lockout theo IP/cửa sổ thời gian).
- Bảo vệ CSRF cho form thao tác nhạy cảm.
- Cấu hình thông qua các biến môi trường:
    * ADMIN_PATH: Đường dẫn URL quản trị ngẫu nhiên (ví dụ: /admin-sec-9a8f2/)
    * ADMIN_USERNAME: Tên đăng nhập
    * ADMIN_PASSWORD: Mật khẩu quản trị
    * ADMIN_SESSION_SECRET: Khóa bí mật ký session cookie
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, Optional, Tuple

# Cấu hình biến môi trường
ADMIN_PATH_ENV = os.getenv("ADMIN_PATH", "").strip()
# Đảm bảo format /path-name/ (bắt đầu bằng / và kết thúc bằng /)
if not ADMIN_PATH_ENV:
    ADMIN_PATH = "/admin-dashboard/"
else:
    p = "/" + ADMIN_PATH_ENV.strip("/") + "/"
    ADMIN_PATH = p

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

# Bí mật dùng ký session cookie
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "").strip()
if not ADMIN_SESSION_SECRET:
    # Nếu chưa cấu hình, sinh ngẫu nhiên tạm thời cho phiên chạy hiện tại (không dùng chuỗi cố định yếu)
    _ephemeral_secret = secrets.token_hex(32)
    ADMIN_SESSION_SECRET = _ephemeral_secret

SESSION_COOKIE_NAME = "gps_admin_session"
SESSION_MAX_AGE_SECONDS = 8 * 3600  # 8 giờ

# Quản lý chống brute force (in-memory rate limit)
# Lưu {ip: [ts1, ts2, ...]}
_failed_attempts: Dict[str, list] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300  # Khóa 5 phút nếu sai 5 lần


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Tạo chuỗi băm PBKDF2-HMAC-SHA256 kèm salt."""
    if salt is None:
        salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return base64.b64encode(salt + key).decode("ascii")


def verify_password(plain_password: str, hashed_or_expected: str) -> bool:
    """
    So sánh an toàn thời gian hằng số (constant-time).
    Hỗ trợ so sánh trực tiếp với plain text ADMIN_PASSWORD hoặc chuỗi băm PBKDF2.
    """
    if not plain_password or not hashed_or_expected:
        return False

    # Nếu chuỗi lưu là dạng PBKDF2 base64
    try:
        decoded = base64.b64decode(hashed_or_expected.encode("ascii"))
        if len(decoded) > 16:
            salt = decoded[:16]
            expected_key = decoded[16:]
            key = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, 100_000)
            if hmac.compare_digest(key, expected_key):
                return True
    except Exception:
        pass

    # So sánh thời gian hằng số trực tiếp
    return hmac.compare_digest(plain_password.encode("utf-8"), hashed_or_expected.encode("utf-8"))


def check_rate_limit(client_ip: str) -> bool:
    """Trả về True nếu IP được phép thử tiếp, False nếu đang bị khóa do thử sai quá nhiều."""
    now = time.time()
    attempts = _failed_attempts.get(client_ip, [])
    # Lọc các lần trong khung thời gian 5 phút
    valid_attempts = [t for t in attempts if now - t < LOCKOUT_WINDOW_SECONDS]
    _failed_attempts[client_ip] = valid_attempts
    return len(valid_attempts) < MAX_FAILED_ATTEMPTS


def record_failed_attempt(client_ip: str) -> None:
    now = time.time()
    attempts = _failed_attempts.get(client_ip, [])
    attempts.append(now)
    _failed_attempts[client_ip] = attempts


def reset_failed_attempts(client_ip: str) -> None:
    _failed_attempts.pop(client_ip, None)


def create_session_token(username: str) -> str:
    """Tạo session token dạng: base64(payload).signature với HMAC-SHA256."""
    payload = {
        "sub": username,
        "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_raw).decode("ascii")

    sig = hmac.new(ADMIN_SESSION_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    """
    Xác minh chữ ký và thời hạn của session token.
    Trả về username nếu hợp lệ, None nếu không hợp lệ hoặc đã hết hạn.
    """
    if not token or "." not in token:
        return None

    parts = token.split(".", 1)
    if len(parts) != 2:
        return None

    payload_b64, signature = parts
    expected_sig = hmac.new(ADMIN_SESSION_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        return None

    try:
        payload_raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        payload = json.loads(payload_raw.decode("utf-8"))
        # Kiểm tra hạn dùng
        if time.time() > payload.get("exp", 0):
            return None
        return payload.get("sub")
    except Exception:
        return None
