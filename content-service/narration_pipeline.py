"""Luồng chính: database -> Wikimedia -> Gemini -> Edge TTS -> Storage.

Background task chỉ là cách chạy. Quyền tạo và checkpoint nằm trong PostgreSQL.
Sau restart, request tiếp theo sẽ tiếp tục công việc khi lease cũ hết hạn.
"""
import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx

from narration_clients import NarrationClients, PipelineError
from narration_store import NarrationStore, now

DEFAULT_VOICES = {"vi": "vi-VN-HoaiMyNeural", "en": "en-US-JennyNeural", "ja": "ja-JP-NanamiNeural"}


def choose_voice(language, voice=None):
    default = os.getenv(f"TTS_VOICE_{language.upper()}", DEFAULT_VOICES[language])
    # Allowlist nhỏ, cấu hình tại backend, tránh nhận tên voice tùy ý.
    allowed = {default, *os.getenv(f"TTS_ALLOWED_VOICES_{language.upper()}", "").split(",")}
    selected = voice or default
    if selected not in allowed or not selected.startswith(language + "-"):
        raise PipelineError("invalid_voice")
    return selected


def make_key(location, language, voice):
    snapshot = json.dumps(location, ensure_ascii=False, sort_keys=True, default=str)
    config = [os.getenv("CONTENT_VERSION", "v1"), os.getenv("TTS_CONFIG_VERSION", "v1"),
              os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), os.getenv("NARRATION_TARGET_CHARS", "900")]
    # Không đưa is_ready/last_synced_at vào version: polling không tạo version mới.
    revision = {k: location.get(k) for k in ("name", "description_source", "latitude", "longitude",
                                           "source_lang", "source_page_id", "source_title", "source_url")}
    version = hashlib.sha256(json.dumps([config, revision], sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    return dict(location_id=location["id"], language=language, voice=voice, content_version=version), snapshot


class NarrationPipeline:
    def __init__(self, store=None, clients=None):
        self.store = store or NarrationStore()
        self.clients = clients or NarrationClients()
        self.tasks = set()
        self.limit = max(1, int(os.getenv("NARRATION_MAX_CONCURRENT", "2")))
        # Chỉ giữ khóa trong lúc nhận task, không khóa cả thời gian gọi API ngoài.
        self.start_lock = asyncio.Lock()
        self.http = None

    @asynccontextmanager
    async def connection(self):
        # Tái sử dụng kết nối để polling/cache hit không tạo HTTP pool liên tục.
        if self.http is None:
            self.http = httpx.AsyncClient(limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
        yield self.http

    async def close(self):
        if self.http is not None:
            await self.http.aclose()
            self.http = None

    async def request(self, location, language, voice=None):
        voice = choose_voice(language, voice)
        key, snapshot = make_key(location, language, voice)
        row = await asyncio.to_thread(self.store.find, key)
        async with self.connection() as client:
            if row and row["status"] == "ready":
                if await self.clients.exists(client, row["storage_path"]):
                    return await self.ready_response(client, row)
                await asyncio.to_thread(self.store.invalidate_audio, row)
        async with self.start_lock:
            if len(self.tasks) >= self.limit:
                return {"narration_status": "processing", "requested_lang": language,
                        "reason": "generation_busy", "retry_after": 10}
            row, claimed = await asyncio.to_thread(self.store.claim, key, snapshot)
            if claimed:
                task = asyncio.create_task(self.generate(row))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        if row["status"] == "ready":  # instance khác vừa hoàn tất
            async with self.connection() as client:
                if await self.clients.exists(client, row["storage_path"]):
                    return await self.ready_response(client, row)
                await asyncio.to_thread(self.store.invalidate_audio, row)
        exhausted = row["attempts"] >= 3 and row["status"] == "failed"
        return {"narration_status": "error" if exhausted else "processing",
                "requested_lang": language, "job_id": row["id"],
                "reason": row["error_code"], "retry_after": 10, "cache_hit": False}

    async def ready_response(self, client, row):
        return {"requested_lang": row["language"], "lang": row["language"],
                "translated_text": row["final_text"], "voice": row["voice"],
                "audio_url": await self.clients.audio_url(client, row["storage_path"]),
                "source_url": row["source_url"], "text_hash": row["text_hash"],
                "content_version": row["content_version"], "is_fallback": False,
                "narration_status": "audio_ready", "cache_hit": True}

    async def generate(self, row):
        try:
            # Tổng thời gian phải nhỏ hơn lease 240s; không gia hạn vô hạn.
            await asyncio.wait_for(self.generate_steps(row), timeout=180)
        except Exception as exc:
            code = str(exc) if isinstance(exc, PipelineError) else type(exc).__name__
            try:
                await asyncio.to_thread(self.store.save, row, status="failed", error_code=code,
                    retry_at=now() + timedelta(seconds=10 * 2 ** (row["attempts"] - 1)))
            except Exception:
                # Database mất kết nối: lease cho phép request sau phục hồi.
                logging.getLogger(__name__).warning("Không lưu được trạng thái job %s", row["id"])

    async def generate_steps(self, row):
        async with self.connection() as client:
            if not row["source_text"]:
                source, url = await self.clients.source(client, json.loads(row["location_snapshot"]))
                await asyncio.to_thread(self.store.save, row, source_text=source, source_url=url)
            if not row["final_text"]:
                text = await self.clients.compose(client, row["source_text"], row["language"])
                await asyncio.to_thread(self.store.save, row, final_text=text,
                    text_hash=hashlib.sha256(text.encode()).hexdigest())
            path = row["storage_path"] or f"{row['id']}/{row['text_hash']}.mp3"
            # Ghi đường dẫn TRƯỚC upload: crash sau upload vẫn tìm lại được file.
            await asyncio.to_thread(self.store.save, row, storage_path=path)
            if not await self.clients.exists(client, path):
                filename = row["audio_filename"]
                if filename:
                    response = await client.get(f"{self.clients.tts_url}/audio/{filename}/status", timeout=8)
                    if response.status_code != 200 or not response.json().get("exists"):
                        filename = None
                if not filename:
                    filename = await self.clients.speech(client, row)
                    await asyncio.to_thread(self.store.save, row, audio_filename=filename)
                await self.clients.upload(client, filename, path)
            # Storage đã xác nhận file tồn tại, chỉ lúc này mới ready.
            await asyncio.to_thread(self.store.save, row, status="ready", error_code=None,
                                   lease_token=None, lease_until=None)
