"""Kiểm thử luồng thật, dùng database file tạm và giả lập các API ngoài."""
import asyncio
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from models import Base, Location, Narration
from narration_clients import NarrationClients, PipelineError, check_text, clean_source
from narration_pipeline import NarrationPipeline, choose_voice, make_key
from narration_store import NarrationStore, now

LOCATION = {"id": 1, "name": "Chợ Bến Thành", "source_lang": "vi", "source_page_id": 123,
            "description_source": "Ngôi chợ lịch sử tại Thành phố Hồ Chí Minh.",
            "latitude": 10.77, "longitude": 106.7}
SOURCE = "<p>Chợ Bến Thành là ngôi chợ lâu đời tại Thành phố Hồ Chí Minh, có giá trị văn hóa.</p>"
TEXT = "Chợ Bến Thành là ngôi chợ lâu đời tại Thành phố Hồ Chí Minh. Đây là nơi mang giá trị văn hóa của thành phố."


class MockAPIs:
    def __init__(self):
        self.calls = Counter()
        self.objects = {}
        self.audio = {}
        self.fail = None
        self.invalid_text = None
        self.last_tts_text = None

    def respond(self, request):
        path = request.url.path
        if "wikipedia.org" in request.url.host:
            stage = "wiki"
        elif "googleapis.com" in request.url.host:
            stage = "gemini"
        elif path == "/text-to-speech":
            stage = "tts"
        elif path.startswith("/audio/"):
            stage = "audio"
        elif "/object/info/" in path:
            stage = "info"
        elif "/object/sign/" in path:
            stage = "sign"
        else:
            stage = "upload"
        self.calls[stage] += 1
        if stage == self.fail:
            return httpx.Response(503, json={"error": "upstream_failed"})
        if stage == "wiki":
            return httpx.Response(200, json={"query": {"pages": [{"pageid": 123,
                "extract": SOURCE, "fullurl": "https://vi.wikipedia.org/wiki/Cho"}]}})
        if stage == "gemini":
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                "content": {"parts": [{"text": self.invalid_text if self.invalid_text is not None else TEXT}]}}]})
        if stage == "tts":
            data = json.loads(request.content)
            self.last_tts_text = data["text"]
            digest = hashlib.sha256(data["text"].encode()).hexdigest()
            filename = hashlib.sha256((digest + data["voice"]).encode()).hexdigest() + ".mp3"
            self.audio[filename] = b"ID3" + bytes(200)
            return httpx.Response(200, json={"audio_filename": filename,
                "voice": data["voice"], "text_hash": digest})
        if stage == "audio":
            filename = path.split("/")[2]
            if path.endswith("/status"):
                return httpx.Response(200, json={"exists": filename in self.audio})
            return httpx.Response(200, content=self.audio[filename]) if filename in self.audio else httpx.Response(404)
        if stage == "info":
            key = path.split("/narration-audio/", 1)[1]
            return httpx.Response(200, json={"size": len(self.objects[key])}) if key in self.objects else httpx.Response(404)
        if stage == "sign":
            key = path.split("/narration-audio/", 1)[1]
            return httpx.Response(200, json={"signedURL": f"/object/sign/narration-audio/{key}?token=test"})
        key = path.split("/narration-audio/", 1)[1]
        self.objects[key] = request.content
        return httpx.Response(200, json={"Key": key})


@pytest.fixture
def setup_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("TTS_SERVICE_URL", "http://tts:8002")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        db.add(Location(id=1, name=LOCATION["name"], description_vi=SOURCE, target_languages="vi,en,ja"))
    store = NarrationStore(sessions)
    apis = MockAPIs()
    original = httpx.AsyncClient
    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(apis.respond))
        return original(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    pipeline = NarrationPipeline(store, NarrationClients())
    yield pipeline, store, apis, sessions
    engine.dispose()


async def finish(pipeline, language="vi", voice=None):
    result = await pipeline.request(LOCATION, language, voice)
    if pipeline.tasks:
        await asyncio.gather(*list(pipeline.tasks))
    return result


def read_row(sessions):
    with sessions() as db:
        return db.execute(select(Narration)).scalar_one()


def allow_retry(sessions):
    with sessions.begin() as db:
        db.execute(update(Narration).values(retry_at=now() - timedelta(seconds=1)))


def test_new_cached_restart_and_exact_text(setup_pipeline):
    pipeline, store, apis, sessions = setup_pipeline
    async def run():
        result = await finish(pipeline)
        assert result["narration_status"] == "processing"
        row = read_row(sessions)
        assert row.status == "ready"
        assert "<p>" not in row.source_text
        assert row.final_text == apis.last_tts_text == TEXT
        assert row.storage_path in apis.objects
        counts = apis.calls.copy()
        # Pipeline mới không có cache RAM, không có file audio cũ trên Render.
        apis.audio.clear()
        restarted = NarrationPipeline(NarrationStore(sessions), NarrationClients())
        cached = await restarted.request(LOCATION, "vi")
        assert cached["narration_status"] == "audio_ready"
        assert cached["audio_url"].startswith("https://project.supabase.co/storage/v1/object/sign/")
        assert cached["cache_hit"]
        for stage in ["wiki", "gemini", "tts", "upload"]:
            assert apis.calls[stage] == counts[stage] == 1
    asyncio.run(run())


def test_concurrent_requests_two_instances_one_job(setup_pipeline):
    pipeline, store, apis, sessions = setup_pipeline
    other = NarrationPipeline(NarrationStore(sessions), NarrationClients())
    async def run():
        await asyncio.gather(*(p.request(LOCATION, "vi") for p in [pipeline, other] * 10))
        await asyncio.gather(*list(pipeline.tasks | other.tasks))
        assert read_row(sessions).status == "ready"
        assert apis.calls["gemini"] == apis.calls["tts"] == 1
    asyncio.run(run())


def test_atomic_claim_in_independent_threads(setup_pipeline):
    _, store, _, sessions = setup_pipeline
    key, snapshot = make_key(LOCATION, "vi", choose_voice("vi"))
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: NarrationStore(sessions).claim(key, snapshot), range(20)))
    assert sum(claimed for _, claimed in results) == 1


def test_languages_voices_versions_are_separate(setup_pipeline, monkeypatch):
    pipeline, store, apis, sessions = setup_pipeline
    monkeypatch.setenv("TTS_ALLOWED_VOICES_VI", "vi-VN-NamMinhNeural")
    async def run():
        await finish(pipeline, "vi")
        await finish(pipeline, "en")
        await finish(pipeline, "vi", "vi-VN-NamMinhNeural")
        monkeypatch.setenv("CONTENT_VERSION", "v2")
        await finish(pipeline, "vi")
        with sessions() as db:
            rows = db.execute(select(Narration)).scalars().all()
            assert len(rows) == 4 and all(r.status == "ready" for r in rows)
        assert apis.calls["gemini"] == 4
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["wiki", "gemini", "tts", "upload", "info"])
def test_failure_not_ready_and_retry_from_checkpoint(setup_pipeline, stage):
    pipeline, store, apis, sessions = setup_pipeline
    async def run():
        apis.fail = stage
        await finish(pipeline)
        assert read_row(sessions).status == "failed"
        counts = apis.calls.copy()
        apis.fail = None
        allow_retry(sessions)
        await finish(pipeline)
        assert read_row(sessions).status == "ready"
        if stage in {"tts", "upload", "info"}:
            assert apis.calls["gemini"] == counts["gemini"] == 1
        if stage == "upload":
            assert apis.calls["tts"] == counts["tts"] == 1
    asyncio.run(run())


def test_crash_after_upload_reuses_storage(setup_pipeline):
    pipeline, store, apis, sessions = setup_pipeline
    async def run():
        await finish(pipeline)
        with sessions.begin() as db:
            db.execute(update(Narration).values(status="processing", lease_until=now()-timedelta(seconds=1)))
        apis.audio.clear()
        await finish(pipeline)
        assert read_row(sessions).status == "ready"
        assert apis.calls["gemini"] == apis.calls["tts"] == apis.calls["upload"] == 1
    asyncio.run(run())


def test_expired_worker_cannot_overwrite(setup_pipeline):
    _, store, _, sessions = setup_pipeline
    key, snapshot = make_key(LOCATION, "vi", choose_voice("vi"))
    old, _ = store.claim(key, snapshot)
    with sessions.begin() as db:
        db.execute(update(Narration).values(lease_until=now()-timedelta(seconds=1)))
    new, claimed = NarrationStore(sessions).claim(key, snapshot)
    assert claimed and new["lease_token"] != old["lease_token"]
    with pytest.raises(RuntimeError, match="lease_lost"):
        store.save(old, final_text="worker cũ")


def test_missing_storage_rebuilds_audio_without_gemini(setup_pipeline):
    pipeline, _, apis, sessions = setup_pipeline
    async def run():
        await finish(pipeline)
        apis.objects.clear()
        apis.audio.clear()
        await finish(pipeline)
        assert read_row(sessions).status == "ready"
        assert apis.calls["gemini"] == 1 and apis.calls["tts"] == 2
    asyncio.run(run())


@pytest.mark.parametrize("text", ["", "x" * 1201, "<p>Văn bản không phù hợp để đọc.</p>",
                                  "# Tiêu đề Markdown không phù hợp", "123456789012345678901234"])
def test_invalid_gemini_never_calls_tts(setup_pipeline, text):
    pipeline, _, apis, sessions = setup_pipeline
    apis.invalid_text = text
    asyncio.run(finish(pipeline))
    assert read_row(sessions).status == "failed" and apis.calls["tts"] == 0


def test_fixed_failure_never_retries_on_polling(setup_pipeline):
    pipeline, _, apis, sessions = setup_pipeline
    apis.invalid_text = ""
    async def run():
        await finish(pipeline)
        await finish(pipeline)
        assert read_row(sessions).attempts == 1
        for _ in range(4):
            allow_retry(sessions)
            await finish(pipeline)
        allow_retry(sessions)
        result = await finish(pipeline)
        assert result["narration_status"] == "error" and read_row(sessions).attempts == 1
        assert apis.calls["gemini"] == 1 and apis.calls["tts"] == 0
    asyncio.run(run())


def test_storage_outage_does_not_invalidate_ready(setup_pipeline):
    pipeline, _, apis, sessions = setup_pipeline
    async def run():
        await finish(pipeline)
        apis.fail = "info"
        with pytest.raises(PipelineError, match="storage_unavailable"):
            await pipeline.request(LOCATION, "vi")
        assert read_row(sessions).status == "ready"
    asyncio.run(run())


def test_database_failure_does_not_start_upstream(setup_pipeline, monkeypatch):
    pipeline, store, apis, _ = setup_pipeline
    def fail(*args):
        raise RuntimeError("database_unavailable")
    monkeypatch.setattr(store, "claim", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline.request(LOCATION, "vi"))
    assert not apis.calls


def test_cleaning_and_version_ignores_sync_time():
    assert clean_source("<script>bad()</script><p>Nguồn [12]</p>\n== Tham khảo ==\nBỏ") == "Nguồn"
    key, _ = make_key(LOCATION, "vi", choose_voice("vi"))
    same, _ = make_key({**LOCATION, "last_synced_at": "2026", "is_ready": True}, "vi", choose_voice("vi"))
    assert key == same


@pytest.mark.parametrize("status_code,expected_error", [
    (400, "gemini_invalid_request"),
    (401, "gemini_auth_error"),
    (403, "gemini_auth_error"),
    (404, "gemini_model_not_found"),
    (429, "gemini_quota_exceeded"),
    (500, "gemini_server_error"),
    (503, "gemini_server_error"),
])
def test_gemini_http_error_codes(status_code, expected_error):
    clients = NarrationClients()
    clients.gemini_key = "test_key"

    def respond(req):
        return httpx.Response(status_code, json={"error": {"message": "Test error", "status": "FAILED"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(PipelineError) as exc_info:
        asyncio.run(clients.compose(client, "Nội dung kiểm tra địa điểm", "vi", job_id="test_job"))
    assert exc_info.value.code == expected_error


def test_gemini_timeout():
    clients = NarrationClients()
    clients.gemini_key = "test_key"

    def respond(req):
        raise httpx.ReadTimeout("Read timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(PipelineError) as exc_info:
        asyncio.run(clients.compose(client, "Nội dung kiểm tra địa điểm", "vi", job_id="test_job"))
    assert exc_info.value.code == "gemini_timeout"


@pytest.mark.parametrize("source", [None, {}, [], 123, "", " \t\n ", "\u200b\ufeff", "abc\x00def", "x" * 12001])
def test_source_rejected_before_network(source):
    clients = NarrationClients()
    clients.gemini_key = "secret-for-test"
    calls = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
            with pytest.raises(PipelineError):
                await clients.compose(client, source, "vi")
        assert calls == []
    asyncio.run(run())


@pytest.mark.parametrize("payload,code", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "gemini_blocked"),
    ({"candidates": []}, "gemini_empty_text"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "gemini_blocked"),
    ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "gemini_incomplete"),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": True, "text": "Reasoning only"}]}}]}, "gemini_empty_text"),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": None}]}}]}, "gemini_invalid_response"),
    ({"candidates": "wrong_type"}, "gemini_invalid_response"),
    ([], "gemini_invalid_response"),
])
def test_actual_response_classification_without_retry(payload, code):
    clients = NarrationClients()
    clients.gemini_key = "secret-for-test"
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json=payload)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(PipelineError, match=code):
                await clients.compose(client, "Nguồn Wikimedia có dữ kiện lịch sử.", "ja")
        assert len(calls) == 1
    asyncio.run(run())


def test_parts_request_and_safe_diagnostics(caplog):
    clients = NarrationClients()
    clients.gemini_key = "secret-for-test"
    caplog.set_level("INFO")
    def respond(req):
        assert req.url.path.endswith(f"/models/{clients.model}:generateContent")
        assert "key" not in req.url.params
        assert req.headers["x-goog-api-key"] == clients.gemini_key
        body = json.loads(req.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "Nguồn gốc đầy đủ."}]}]
        assert body["systemInstruction"]["parts"][0]["text"]
        return httpx.Response(200, json={"modelVersion": clients.model,
            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 12, "totalTokenCount": 52},
            "promptFeedback": {}, "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"thought": True, "text": "private reasoning"}, {"text": "京都は"}, {"text": "古都です。"}]}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await clients.compose(client, " Nguồn gốc\n đầy đủ. ", "ja") == "京都は古都です。"
    asyncio.run(run())
    clients.diagnostic("api_error", error={"message": "key=" + clients.gemini_key, "apiKey": "other-key"})
    assert clients.gemini_key not in caplog.text and "other-key" not in caplog.text
    assert "private reasoning" not in caplog.text and "Nguồn gốc đầy đủ" not in caplog.text
    assert '"promptTokenCount": 40' in caplog.text and '"http_status": 200' in caplog.text


def test_source_is_never_silently_truncated():
    with pytest.raises(PipelineError, match="source_too_long"):
        clean_source("Đây là dữ kiện lịch sử. " * 1000)
    assert check_text("京都は古都です。") == "京都は古都です。"


def test_tts_no_audio_does_not_restart_job(setup_pipeline, monkeypatch):
    pipeline, _, apis, sessions = setup_pipeline
    async def no_audio(client, row):
        apis.calls["tts"] += 1
        raise PipelineError("tts_no_audio")
    monkeypatch.setattr(pipeline.clients, "speech", no_audio)
    async def run():
        await finish(pipeline)
        for _ in range(4):
            allow_retry(sessions)
            result = await finish(pipeline)
        assert result["narration_status"] == "error"
        assert apis.calls["gemini"] == apis.calls["tts"] == 1
        assert not apis.objects and read_row(sessions).final_text == TEXT
    asyncio.run(run())


def test_tts_terminal_response_not_retried():
    clients = NarrationClients()
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(503, json={"detail": {"code": "tts_unavailable",
            "cause": "NoAudioReceived", "retryable": False}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(PipelineError, match="tts_no_audio"):
                await clients.speech(client, {"final_text": TEXT, "language": "vi", "voice": choose_voice("vi")})
        assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("fallback_works", [True, False])
def test_narrate_through_real_tts_endpoint(setup_pipeline, tmp_path, monkeypatch, fallback_works):
    """Real Content/Translate ASGI routes, real MP3 parser/database; only providers are mocked."""
    import base64
    import importlib.util
    import sys
    from unittest.mock import AsyncMock
    from pathlib import Path
    import main as content_app
    pipeline, _, apis, sessions = setup_pipeline
    root = Path(__file__).resolve().parents[1]
    audio_dir = tmp_path / "edge-audio"
    audio_dir.mkdir()
    monkeypatch.setenv("AUDIO_DIR", str(audio_dir))
    monkeypatch.setenv("TTS_PROVIDER_VI", "edge")
    monkeypatch.setenv("DISABLE_GTTS_FALLBACK", "1")
    monkeypatch.syspath_prepend(str(root / "translate-audio-service"))
    import tts
    monkeypatch.setattr(tts, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(tts, "available_voice_names", AsyncMock(return_value=set(tts.VI_VOICES)))
    spec = importlib.util.spec_from_file_location("edge_integration_app", root / "translate-audio-service/main.py")
    edge_app = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, edge_app)
    spec.loader.exec_module(edge_app)
    mp3 = base64.b64decode((root / "translate-audio-service/tests/fixtures/short-mp3.b64").read_text())
    voices = []
    class Communicate:
        def __init__(self, text, voice):
            assert text == TEXT
            self.voice = voice
            voices.append(voice)
        async def save(self, filename):
            if not fallback_works or self.voice == tts.VI_VOICES[0]:
                raise tts.edge_tts.exceptions.NoAudioReceived("upstream no audio")
            Path(filename).write_bytes(mp3)
    monkeypatch.setattr(tts.edge_tts, "Communicate", Communicate)
    monkeypatch.setattr(content_app, "IS_SQLITE", False)
    monkeypatch.setattr(content_app, "SessionLocal", sessions)
    monkeypatch.setattr(content_app, "narration_pipeline", pipeline)

    async def run():
        edge_transport = httpx.ASGITransport(app=edge_app.app)
        async def upstream(request):
            if request.url.host == "tts":
                return await edge_transport.handle_async_request(request)
            return apis.respond(request)
        pipeline.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=content_app.app), base_url="http://content") as client:
            body = {"location_id": 1, "target_lang": "vi"}
            response = await client.post("/narrate", json=body)
            assert response.status_code == 202
            await asyncio.gather(*list(pipeline.tasks))
            response = await client.post("/narrate", json=body)
            row = read_row(sessions)
            assert row.final_text == TEXT and apis.calls["gemini"] == 1
            if fallback_works:
                assert response.status_code == 200
                assert response.json()["voice"] == tts.VI_VOICES[1]
                assert response.json()["requested_voice"] == tts.VI_VOICES[0]
                assert apis.objects[row.storage_path] == mp3
                assert voices == list(tts.VI_VOICES)
                # A new coordinator recovers the actual voice from durable metadata.
                restarted = NarrationPipeline(NarrationStore(sessions), NarrationClients())
                restarted.http = pipeline.http
                monkeypatch.setattr(content_app, "narration_pipeline", restarted)
                cached = await client.post("/narrate", json=body)
                assert cached.status_code == 200 and cached.json()["cache_hit"]
                assert cached.json()["voice"] == tts.VI_VOICES[1]
                assert voices == list(tts.VI_VOICES) and apis.calls["gemini"] == 1
            else:
                assert response.status_code == 503 and response.json()["reason"] == "tts_no_audio"
                assert voices == [tts.VI_VOICES[0], tts.VI_VOICES[1], tts.VI_VOICES[1]]
                assert row.status == "failed" and not apis.objects
                await client.post("/narrate", json=body)
                assert len(voices) == 3 and apis.calls["gemini"] == 1
        await pipeline.close()
    asyncio.run(run())

