"""Run inside the Render container, using its real environment; never print credentials.

python diagnose_narration.py                         # verify configured model/key
python diagnose_narration.py --location-id 12 --language vi  # real end-to-end job
"""
import argparse
import asyncio
import logging
import time

import httpx

from narration_clients import NarrationClients, PipelineError
from urllib.parse import quote


async def diagnose(location_id=None, language="vi"):
    clients = NarrationClients()
    if not clients.gemini_key:
        clients.diagnostic("configuration_error", api_key_present=False)
        return 1
    async with httpx.AsyncClient() as http:
        # GET metadata does not generate text or consume a generation retry.
        response = await http.get(
            f"https://generativelanguage.googleapis.com/v1beta/models/{quote(clients.model, safe='')}",
            headers={"x-goog-api-key": clients.gemini_key}, timeout=15)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        clients.diagnostic("model_verification", model=clients.model, http_status=response.status_code,
            model_name=data.get("name"), methods=data.get("supportedGenerationMethods"),
            input_limit=data.get("inputTokenLimit"), output_limit=data.get("outputTokenLimit"),
            error=data.get("error"))
        if response.is_error or "generateContent" not in data.get("supportedGenerationMethods", []):
            return 1
    if location_id is None:
        return 0

    from models import IS_SQLITE, Location, SessionLocal
    from narration_pipeline import NarrationPipeline
    if IS_SQLITE or not clients.configured():
        clients.diagnostic("configuration_error", postgres_configured=not IS_SQLITE,
                           storage_configured=bool(clients.url and clients.secret))
        return 1
    with SessionLocal() as db:
        location = db.get(Location, location_id)
        if location is None:
            clients.diagnostic("location_missing", location_id=location_id)
            return 1
        snapshot = {column.name: getattr(location, column.name) for column in Location.__table__.columns}
    pipeline = NarrationPipeline(clients=clients)
    started = time.monotonic()
    try:
        while time.monotonic() - started < 240:
            result = await pipeline.request(snapshot, language)
            clients.diagnostic("smoke_result", location_id=location_id, language=language,
                elapsed_seconds=round(time.monotonic() - started, 2),
                **{key: result.get(key) for key in ["narration_status", "reason", "cache_hit", "job_id"]})
            if result["narration_status"] == "error":
                return 1
            if result["narration_status"] == "audio_ready":
                async with pipeline.connection() as http:
                    async with http.stream("GET", result["audio_url"], timeout=20) as audio:
                        header = bytearray()
                        async for chunk in audio.aiter_bytes():
                            header.extend(chunk[:128 - len(header)])
                            if len(header) >= 128:
                                break
                        valid = (audio.status_code == 200 and len(header) >= 128 and
                            (header[:3] == b"ID3" or (header[0] == 255 and header[1] & 224 == 224)))
                        clients.diagnostic("mp3_verified", http_status=audio.status_code, valid_mp3=valid)
                        return 0 if valid else 1
            await asyncio.sleep(3)
        clients.diagnostic("smoke_timeout", location_id=location_id)
        return 1
    finally:
        if pipeline.tasks:
            await asyncio.gather(*list(pipeline.tasks), return_exceptions=True)
        await pipeline.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location-id", type=int)
    parser.add_argument("--language", choices=["vi", "en", "ja"], default="vi")
    args = parser.parse_args()
    log = logging.getLogger("uvicorn.error")
    log.setLevel(logging.INFO)
    log.addHandler(logging.StreamHandler())
    try:
        result = asyncio.run(diagnose(args.location_id, args.language))
    except (PipelineError, httpx.HTTPError) as exc:
        # HTTP exception strings can contain signed URLs: report only their type/code.
        log.error("diagnostic_failed code=%s", exc.code if isinstance(exc, PipelineError) else type(exc).__name__)
        result = 1
    raise SystemExit(result)
