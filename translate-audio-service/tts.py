"""
tts.py — Module Text-to-Speech sử dụng Edge TTS cho translate-audio-service.

Đặc điểm kỹ thuật:
1. Thư viện cộng đồng Edge TTS (edge-tts):
   - Sử dụng async edge_tts.Communicate(text, voice).save(filepath).
   - Có timeout hữu hạn (TTS_TIMEOUT_SECONDS, mặc định 10.0s).
   - Không sử dụng gTTS hay deep-translator; không tạo thêm service thứ tư.
2. Voice mapping và cấu hình:
   - Mẫu mặc định:
       * vi -> vi-VN-HoaiMyNeural
       * en -> en-US-JennyNeural
       * ja -> ja-JP-NanamiNeural
   - Hỗ trợ cấu hình override bằng biến môi trường (TTS_VOICE_VI, TTS_VOICE_EN, TTS_VOICE_JA).
   - Có thể kiểm tra danh sách voice hiện hành trực tuyến bằng lệnh: edge-tts --list-voices.
   - Ngôn ngữ ngoài allowlist {vi, en, ja} sẽ phát sinh UnsupportedLanguageError (HTTP 422).
3. Cache file bền vững trong audio_files/:
   - Key SHA256: hash của (text đã chuẩn hóa + lang + voice + phiên bản thiết lập TTS).
   - Tên file sinh trực tiếp từ hash: <cache_key>.mp3 (tránh sinh UUID mới sau mỗi GPS tick).
   - Khóa đồng thời theo key (asyncio.Lock per key) để các request gửi đồng thời cho cùng
     nội dung không gọi TTS trùng lặp.
   - Bản demo chạy trên một worker: khóa in-memory có giới hạn là không đồng bộ xuyên process
     nếu triển khai nhiều process (multi-worker uvicorn/gunicorn). Khi đó cần khóa phân tán
     (như Redis lock) hoặc khóa file ở mức hệ điều hành (fcntl/portalocker).
   - Ghi file tạm (.tmp) trên cùng thư mục/filesystem rồi thực hiện atomic rename (os.replace).
   - Chỉ trả kết quả khi file đã có dữ liệu thực tế (> 0 bytes).
   - Nếu file vật lý bị mất trên đĩa, tự động tạo lại, không trả cache_hit giả.
4. An toàn thông tin:
   - Không log toàn bộ nội dung text nhạy cảm, chỉ ghi nhận text_hash, lang, voice và độ dài.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import edge_tts

logger = logging.getLogger("translate_audio.tts")

# Danh sách ngôn ngữ được hỗ trợ
ALLOWED_LANGS = {"vi", "en", "ja"}

# Giới hạn độ dài văn bản
MAX_TEXT_LENGTH = 1200

# Mapping voice mặc định theo contract
DEFAULT_VOICES: Dict[str, str] = {
    "vi": "vi-VN-HoaiMyNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
}

# Cấu hình từ môi trường
TTS_CONFIG_VERSION: str = os.getenv("TTS_CONFIG_VERSION", "v1")
TTS_TIMEOUT_SECONDS: float = float(os.getenv("TTS_TIMEOUT_SECONDS", "10.0"))

# Thư mục lưu audio cache mặc định: translate-audio-service/audio_files
DEFAULT_AUDIO_DIR = Path(__file__).resolve().parent / "audio_files"
AUDIO_DIR: Path = Path(os.getenv("AUDIO_DIR", str(DEFAULT_AUDIO_DIR))).resolve()

# Regex chuẩn hóa khoảng trắng
_MULTI_WHITESPACE = re.compile(r"\s+")

# Regex validate filename an toàn (tương thích tiêu chuẩn Content Service A)
SAFE_AUDIO_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.mp3$",
    re.IGNORECASE,
)


class UnsupportedLanguageError(Exception):
    """Ngoại lệ khi mã ngôn ngữ không nằm trong allowlist."""

    def __init__(self, lang: str):
        self.lang = lang
        super().__init__(
            f"Mã ngôn ngữ '{lang}' không được hỗ trợ. "
            f"Chỉ chấp nhận: {sorted(ALLOWED_LANGS)}"
        )


class TTSError(Exception):
    """Ngoại lệ khi quá trình chuyển văn bản thành giọng nói gặp lỗi."""

    pass


class TTSTimeoutError(TTSError):
    """Ngoại lệ khi gọi Edge TTS bị timeout."""

    pass


def get_voice_for_lang(lang: str) -> str:
    """
    Lấy tên giọng đọc (voice) tương ứng với ngôn ngữ.
    Hỗ trợ override qua biến môi trường TTS_VOICE_<LANG> (vd: TTS_VOICE_VI).
    """
    lang_clean = lang.strip().lower()
    if lang_clean not in ALLOWED_LANGS:
        raise UnsupportedLanguageError(lang_clean)

    env_key = f"TTS_VOICE_{lang_clean.upper()}"
    return os.getenv(env_key, DEFAULT_VOICES[lang_clean])


def normalize_tts_text(text: str) -> str:
    """
    Chuẩn hóa khoảng trắng của text: thu gọn khoảng trắng liên tiếp và cắt viền.
    """
    if not text:
        return ""
    return _MULTI_WHITESPACE.sub(" ", text).strip()


def compute_text_hash(normalized_text: str) -> str:
    """
    Tính mã hash SHA256 của text đã chuẩn hóa.
    Mã này đại diện cho nội dung văn bản (dùng để lưu trường text_hash trong cache của A).
    """
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def compute_cache_key(
    normalized_text: str,
    lang: str,
    voice: str,
    config_version: str = TTS_CONFIG_VERSION,
) -> str:
    """
    Tính cache key SHA256 bền vững từ:
    (text đã chuẩn hóa + lang + voice + phiên bản thiết lập TTS).
    """
    raw_key = f"{normalized_text}:{lang}:{voice}:{config_version}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def validate_audio_filename(filename: str) -> bool:
    """
    Kiểm tra tính an toàn của tên file âm thanh nhằm ngăn chặn path traversal.
    - Không được rỗng.
    - Không chứa '..', '/', '\\', ':'.
    - Phải kết thúc bằng .mp3 và khớp regex SAFE_AUDIO_FILENAME_RE.
    """
    if not filename:
        return False
    if ".." in filename or "/" in filename or "\\" in filename or ":" in filename:
        return False
    return bool(SAFE_AUDIO_FILENAME_RE.match(filename))


# ---------------------------------------------------------------------------
# Khóa đồng bộ theo Key (Concurrency Lock Manager)
# ---------------------------------------------------------------------------
# Lưu ý kỹ thuật cho bản demo:
# _key_locks hoạt động trong bộ nhớ (in-memory) của tiến trình đơn (single worker).
# Nếu ứng dụng chạy nhiều process (gunicorn/uvicorn workers > 1), khóa in-memory
# không thể chia sẻ qua các tiến trình hệ điều hành khác nhau. Trong môi trường
# multi-process production, cần thay thế bằng Distributed Lock (ví dụ Redis Lock)
# hoặc file advisory lock (portalocker/fcntl).
_key_locks: Dict[str, asyncio.Lock] = {}
_manager_lock = asyncio.Lock()


async def get_key_lock(cache_key: str) -> asyncio.Lock:
    """Lấy hoặc khởi tạo asyncio.Lock gắn với một cache_key cụ thể."""
    async with _manager_lock:
        if cache_key not in _key_locks:
            _key_locks[cache_key] = asyncio.Lock()
        return _key_locks[cache_key]


def ensure_audio_dir(directory: Optional[Path] = None) -> Path:
    """Đảm bảo thư mục lưu trữ file audio tồn tại."""
    target = directory if directory is not None else AUDIO_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# Hàm tổng hợp âm thanh chính
# ---------------------------------------------------------------------------
async def synthesize_speech(
    text: str,
    lang: str,
    audio_dir: Optional[Path] = None,
    timeout_seconds: Optional[float] = None,
    voice: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Tạo file MP3 từ văn bản qua Edge TTS hoặc trả về cache có sẵn.

    Tham số:
    - text: nội dung văn bản (tối đa 1200 ký tự)
    - lang: mã ngôn ngữ thật của text ('vi', 'en', 'ja')
    - audio_dir: thư mục lưu file (mặc định lấy theo cấu hình AUDIO_DIR)
    - timeout_seconds: thời gian chờ tối đa khi gọi TTS (mặc định TTS_TIMEOUT_SECONDS)

    Trả về:
    {
        "lang": str,
        "voice": str,
        "text_hash": str,
        "audio_filename": str,
        "audio_url": str,
        "cache_hit": bool,
    }
    """
    target_dir = ensure_audio_dir(audio_dir)
    timeout = (
        timeout_seconds if timeout_seconds is not None else TTS_TIMEOUT_SECONDS
    )

    # 1. Kiểm tra ngôn ngữ
    lang_clean = lang.strip().lower()
    if lang_clean not in ALLOWED_LANGS:
        raise UnsupportedLanguageError(lang_clean)

    # 2. Chuẩn hóa và kiểm tra độ dài text
    normalized_text = normalize_tts_text(text)
    if not normalized_text:
        raise ValueError("text không được để trống")
    if len(normalized_text) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"text không được vượt quá {MAX_TEXT_LENGTH} ký tự "
            f"(nhận {len(normalized_text)} ký tự)"
        )

    default_voice = get_voice_for_lang(lang_clean)
    allowed_voices = {default_voice, *os.getenv(f"TTS_ALLOWED_VOICES_{lang_clean.upper()}", "").split(",")}
    voice = voice or default_voice
    if voice not in allowed_voices or not voice.startswith(lang_clean + "-"):
        raise ValueError("Giọng đọc không thuộc danh sách cho phép của ngôn ngữ")
    text_hash = compute_text_hash(normalized_text)
    cache_key = compute_cache_key(
        normalized_text, lang_clean, voice, TTS_CONFIG_VERSION
    )
    filename = f"{cache_key}.mp3"
    filepath = target_dir / filename

    # An toàn thông tin: không log text nhạy cảm, chỉ log siêu dữ liệu
    logger.info(
        "Xử lý TTS: lang=%s, voice=%s, text_hash=%s, text_len=%d",
        lang_clean,
        voice,
        text_hash,
        len(normalized_text),
    )

    # 3. Đồng bộ hóa các request đồng thời vào cùng một key
    key_lock = await get_key_lock(cache_key)
    async with key_lock:
        # Kiểm tra file cache bền vững trên đĩa: phải tồn tại và kích thước > 0
        if filepath.is_file() and filepath.stat().st_size > 0:
            logger.info("Cache hit cho file audio: %s", filename)
            return {
                "lang": lang_clean,
                "voice": voice,
                "text_hash": text_hash,
                "audio_filename": filename,
                "audio_url": f"/audio/{filename}",
                "cache_hit": True,
            }

        # Nếu file chưa tồn tại hoặc bị xóa/hỏng -> tạo mới (không trả cache_hit giả)
        logger.info(
            "Cache miss (hoặc file vật lý bị mất), bắt đầu tạo audio: %s",
            filename,
        )

        # Atomic write: ghi ra file tạm trong cùng thư mục rồi đổi tên
        temp_file = target_dir / f"tmp_{cache_key}_{uuid.uuid4().hex}.tmp"
        try:
            communicate = edge_tts.Communicate(normalized_text, voice)
            await asyncio.wait_for(
                communicate.save(str(temp_file)),
                timeout=timeout,
            )

            if not temp_file.is_file() or temp_file.stat().st_size == 0:
                raise TTSError(
                    "File âm thanh sau khi tạo bị rỗng hoặc không tồn tại"
                )

            # Atomic rename (trên cùng filesystem)
            os.replace(temp_file, filepath)

        except asyncio.TimeoutError as exc:
            if temp_file.is_file():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            logger.error(
                "Edge TTS timeout sau %.1f giây: text_hash=%s", timeout, text_hash
            )
            raise TTSTimeoutError(
                f"Edge TTS không phản hồi sau {timeout} giây"
            ) from exc
        except Exception as exc:
            if temp_file.is_file():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            logger.error(
                "Edge TTS gặp lỗi: text_hash=%s, err_type=%s",
                text_hash,
                type(exc).__name__,
            )
            if isinstance(exc, (TTSError, TTSTimeoutError)):
                raise
            raise TTSError(
                f"Lỗi tạo âm thanh với Edge TTS: {str(exc)}"
            ) from exc

        # Kiểm tra xác nhận file cuối cùng đã ghi đĩa thành công
        if not filepath.is_file() or filepath.stat().st_size == 0:
            raise TTSError("Không thể xác minh file âm thanh sau khi ghi đĩa")

        return {
            "lang": lang_clean,
            "voice": voice,
            "text_hash": text_hash,
            "audio_filename": filename,
            "audio_url": f"/audio/{filename}",
            "cache_hit": False,
        }
