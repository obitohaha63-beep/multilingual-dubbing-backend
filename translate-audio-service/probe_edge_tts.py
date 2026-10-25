"""Run on Render: python probe_edge_tts.py --controls --api-url http://127.0.0.1:8002

Makes one fresh synthesis per Vietnamese voice, optionally English/Japanese controls,
then checks the running /text-to-speech endpoint with the real fallback policy.
Does not call Gemini or change narration jobs. Nonzero exit if Vietnamese fails.
"""
import argparse
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx
import tts

TEXT = "Xin chào. Đây là bài kiểm tra giọng đọc tiếng Việt có dấu, rõ ràng và tự nhiên."


async def run(controls=False, api_url=None):
    print(json.dumps({"event": "probe_start", "running_on_render": bool(os.getenv("RENDER")),
                      "edge_tts_version": tts.EDGE_TTS_VERSION, "configured_voice": tts.get_voice_for_lang("vi")}))
    names = await tts.available_voice_names()
    print(json.dumps({"event": "voice_catalog", "connected": names is not None,
                      "vietnamese_voices": sorted(v for v in names or [] if v.startswith("vi-VN-"))}))
    cases = [(v, "vi", TEXT) for v in tts.VI_VOICES]
    if controls:
        cases += [(tts.get_voice_for_lang("en"), "en", "Hello. This is an audio connection test."),
                  (tts.get_voice_for_lang("ja"), "ja", "こんにちは。これは音声テストです。")]
    vi_success = False
    with tempfile.TemporaryDirectory(prefix="edge-probe-") as directory:
        for voice, lang, text in cases:
            try:
                result = await tts.synthesize_speech(text, lang, audio_dir=Path(directory),
                    timeout_seconds=10, voice=voice, allow_fallback=False)
                valid = tts.is_valid_mp3_file(Path(directory) / result["audio_filename"])
                if lang == "vi":
                    vi_success |= valid
                print(json.dumps({"event": "voice_probe", "voice": voice,
                    "valid_mp3": valid, "attempts": result["attempts"]}), flush=True)
            except tts.TTSError as exc:
                print(json.dumps({"event": "voice_probe", "voice": voice, "valid_mp3": False,
                    "cause": exc.cause, "attempts": exc.attempts}), flush=True)
    api_success = api_url is None
    if api_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(api_url.rstrip("/") + "/text-to-speech",
                    json={"text": TEXT, "lang": "vi"}, timeout=90)
                data = response.json()
                details = data if response.status_code == 200 else data.get("detail", {})
                print(json.dumps({"event": "api_probe", "http_status": response.status_code,
                    **{k: details.get(k) for k in ["voice", "requested_voice", "voice_fallback", "attempts", "cause"]}}))
                if response.status_code == 200:
                    audio = await client.get(api_url.rstrip("/") + data["audio_url"], timeout=15)
                    with tempfile.TemporaryDirectory(prefix="edge-api-probe-") as directory:
                        path = Path(directory) / "probe.mp3"
                        path.write_bytes(audio.content)
                        api_success = audio.status_code == 200 and tts.is_valid_mp3_file(path)
                    print(json.dumps({"event": "api_mp3_verified", "valid_mp3": api_success,
                                      "bytes": len(audio.content)}))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            print(json.dumps({"event": "api_probe_failed", "error_type": type(exc).__name__}))
    return 0 if (vi_success or (api_url is not None and api_success)) and api_success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--api-url")
    args = parser.parse_args()
    log = logging.getLogger("uvicorn.error")
    log.setLevel(logging.INFO)
    log.addHandler(logging.StreamHandler())
    raise SystemExit(asyncio.run(run(args.controls, args.api_url)))
