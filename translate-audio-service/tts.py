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
import time
import uuid
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

import edge_tts
from aiohttp import ClientConnectionError, ClientResponseError, WSServerHandshakeError
from mutagen.mp3 import MP3
from mutagen import MutagenError

EDGE_TTS_VERSION: str = getattr(edge_tts, "__version__", "unknown")
logger = logging.getLogger("uvicorn.error.translate_audio.tts")
VI_VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")

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

    def __init__(self, message, *, cause=None, retryable=False, attempts=None):
        super().__init__(message)
        self.cause = cause
        self.retryable = retryable
        self.attempts = attempts or []


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
    if not isinstance(text, str):
        raise ValueError("text phải là chuỗi")
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c not in "\r\n\t" for c in text):
        raise ValueError("text chứa ký tự điều khiển không hợp lệ")
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


def is_valid_mp3_file(filepath: Path) -> bool:
    """
    Xác minh file âm thanh tồn tại trên đĩa và có dữ liệu MP3 hợp lệ:
    - Kích thước > 0 bytes.
    - Bắt đầu bằng ID3 tag (ID3v2) hoặc MPEG audio frame sync bits (0xFF, 0xE0).
    """
    if not filepath.is_file():
        return False
    try:
        size = filepath.stat().st_size
        if not 128 <= size <= 2_000_000:
            return False
        info = MP3(filepath).info
        return not info.sketchy and info.length > 0 and info.sample_rate > 0 and info.channels in {1, 2}
    except (OSError, MutagenError, ValueError):
        return False


_catalog_names = None
_catalog_until = 0.0
_catalog_lock = asyncio.Lock()


async def available_voice_names():
    """Best effort catalog, cached for an hour (failed fetches for one minute)."""
    global _catalog_names, _catalog_until
    if time.monotonic() < _catalog_until:
        return _catalog_names
    async with _catalog_lock:
        if time.monotonic() < _catalog_until:
            return _catalog_names
        try:
            voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=2)
            _catalog_names = {v["ShortName"] for v in voices}
            _catalog_until = time.monotonic() + 3600
            logger.info("Edge catalog connected=true voice_count=%d edge_tts_version=%s",
                        len(_catalog_names), EDGE_TTS_VERSION)
        except Exception as exc:
            _catalog_names = None
            _catalog_until = time.monotonic() + 60
            kind, status, _ = classify_error(exc)
            logger.warning("Edge catalog connected=false category=%s error_type=%s http_status=%s",
                           kind, type(exc).__name__, status)
        return _catalog_names


def classify_error(exc):
    status = getattr(exc, "status", None)
    if isinstance(exc, edge_tts.exceptions.NoAudioReceived):
        return "no_audio", status, True
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout", status, True
    if isinstance(exc, WSServerHandshakeError):
        return "websocket", status, status in {429, 500, 502, 503, 504}
    if isinstance(exc, edge_tts.exceptions.WebSocketError):
        return "websocket", status, True
    if isinstance(exc, ClientResponseError):
        return "http", status, status in {429, 500, 502, 503, 504}
    if isinstance(exc, (ClientConnectionError, ConnectionError)):
        return "network", status, True
    return "invalid_audio" if isinstance(exc, TTSError) else "fixed_error", status, False


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
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    """At most three new Edge sessions; Vietnamese no-audio switches voices immediately.

    save() consumes the complete Edge audio stream. Only a parsed MP3 with positive
    duration is published, using atomic rename. Failed/cancelled temporary files go away.
    """
    lang_clean = lang.strip().lower()
    if lang_clean not in ALLOWED_LANGS:
        raise UnsupportedLanguageError(lang_clean)
    normalized_text = normalize_tts_text(text)
    if not normalized_text or not any(c.isalpha() for c in normalized_text):
        raise ValueError("text phải có nội dung có thể đọc")
    if len(normalized_text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text không được vượt quá {MAX_TEXT_LENGTH} ký tự")
    default_voice = get_voice_for_lang(lang_clean).strip()
    allowed = {default_voice, *os.getenv(f"TTS_ALLOWED_VOICES_{lang_clean.upper()}", "").split(",")}
    if lang_clean == "vi":
        allowed.update(VI_VOICES)
    requested_voice = voice or default_voice
    if requested_voice not in allowed or not requested_voice.startswith(lang_clean + "-"):
        raise ValueError("Giọng đọc không thuộc danh sách cho phép của ngôn ngữ")
    candidates = [requested_voice]
    if lang_clean == "vi" and allow_fallback:
        candidates.extend(v for v in VI_VOICES if v != requested_voice)
    target_dir = ensure_audio_dir(audio_dir)
    timeout = timeout_seconds if timeout_seconds is not None else TTS_TIMEOUT_SECONDS
    text_hash = compute_text_hash(normalized_text)
    # Both Vietnamese preferences share one lock; cache files retain the actual voice.
    lock_voice = "vi-fallback-v1" if lang_clean == "vi" and allow_fallback else requested_voice
    key_lock = await get_key_lock(compute_cache_key(normalized_text, lang_clean, lock_voice))
    attempts = []

    def result(actual_voice, filename, cached):
        return {"lang": lang_clean, "voice": actual_voice, "requested_voice": requested_voice,
                "voice_fallback": actual_voice != requested_voice, "text_hash": text_hash,
                "audio_filename": filename, "audio_url": f"/audio/{filename}",
                "cache_hit": cached, "attempts": attempts}

    async with key_lock:
        for candidate in candidates:
            filename = compute_cache_key(normalized_text, lang_clean, candidate) + ".mp3"
            if is_valid_mp3_file(target_dir / filename):
                logger.info("Edge cache_hit=true voice=%s requested_voice=%s text_hash=%s",
                            candidate, requested_voice, text_hash)
                return result(candidate, filename, True)
        if lang_clean == "vi":
            names = await available_voice_names()
            if names is not None:
                for candidate in candidates:
                    logger.info("Edge voice_validation voice=%s listed=%s", candidate, candidate in names)
                candidates = [v for v in candidates if v in names]
                if not candidates:
                    raise TTSError("Không có giọng tiếng Việt yêu cầu trong danh sách Edge hiện tại",
                                   cause="VoiceUnavailable")
        index = 0
        max_attempts = 3 if allow_fallback else 1
        for attempt in range(1, max_attempts + 1):
            actual_voice = candidates[index]
            filename = compute_cache_key(normalized_text, lang_clean, actual_voice) + ".mp3"
            filepath = target_dir / filename
            temp_file = target_dir / f"tmp_{uuid.uuid4().hex}.tmp"
            started = time.monotonic()
            try:
                communicate = edge_tts.Communicate(normalized_text, actual_voice)
                await asyncio.wait_for(communicate.save(str(temp_file)), timeout=timeout)
                if not is_valid_mp3_file(temp_file):
                    raise TTSError("Edge trả dữ liệu không phải MP3 đọc được", cause="InvalidMP3")
                size = temp_file.stat().st_size
                os.replace(temp_file, filepath)
                entry = {"voice": actual_voice, "attempt": attempt, "status": "success",
                         "duration_ms": round((time.monotonic() - started) * 1000, 1), "bytes": size}
                attempts.append(entry)
                logger.info("Edge attempt=%d/%d voice=%s requested_voice=%s edge_tts_version=%s "
                            "status=success duration_ms=%.1f bytes=%d text_len=%d text_hash=%s",
                            attempt, max_attempts, actual_voice, requested_voice, EDGE_TTS_VERSION,
                            entry["duration_ms"], size, len(normalized_text), text_hash)
                return result(actual_voice, filename, False)
            except Exception as exc:
                kind, status, transient = classify_error(exc)
                entry = {"voice": actual_voice, "attempt": attempt, "status": "failed",
                         "duration_ms": round((time.monotonic() - started) * 1000, 1),
                         "error_type": type(exc).__name__, "category": kind, "http_status": status}
                attempts.append(entry)
                # Do not log exception messages: they may contain signed WebSocket URLs.
                logger.warning("Edge attempt=%d/%d voice=%s edge_tts_version=%s category=%s "
                               "error_type=%s http_status=%s duration_ms=%.1f text_len=%d text_hash=%s",
                               attempt, max_attempts, actual_voice, EDGE_TTS_VERSION, kind,
                               entry["error_type"], status, entry["duration_ms"], len(normalized_text), text_hash)
                no_audio = kind == "no_audio"
                can_switch = no_audio and index < len(candidates) - 1
                # A single remaining voice must not be hammered three times for no audio.
                exhausted = attempt == max_attempts or not transient or (no_audio and len(candidates) == 1)
                if exhausted:
                    voices_tried = ", ".join(dict.fromkeys(a["voice"] for a in attempts))
                    message = (f"Edge TTS không tạo được MP3. Giọng đã thử: {voices_tried}. "
                               f"Loại lỗi: {kind} ({type(exc).__name__}).")
                    error_class = TTSTimeoutError if kind == "timeout" else TTSError
                    cause = exc.cause if isinstance(exc, TTSError) else type(exc).__name__
                    raise error_class(message, cause=cause, retryable=transient and not no_audio,
                                      attempts=attempts) from exc
                if can_switch:
                    index += 1
                    logger.info("Edge fallback from_voice=%s to_voice=%s reason=NoAudioReceived",
                                actual_voice, candidates[index])
                else:
                    await asyncio.sleep(min(attempt, 2))
            finally:
                temp_file.unlink(missing_ok=True)
