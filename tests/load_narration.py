"""Đo tải cục bộ 5/10/20 request: database thật, API ngoài được giả lập.

Không dùng kết quả này để suy ra sức chịu tải của Render hay quota API.
Chạy: .venv/Scripts/python tests/load_narration.py
"""
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import psutil
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "content-service"))
from models import Base, Location
from narration_clients import NarrationClients
from narration_pipeline import NarrationPipeline
from narration_store import NarrationStore
from test_narration_pipeline import LOCATION, MockAPIs


async def measure(users, mode, directory):
    engine = create_engine(f"sqlite:///{directory / (mode + str(users) + '.db')}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        for number in range(1, users + 1):
            db.add(Location(id=number, name=f"Chợ {number}", description_vi="Nguồn", target_languages="vi"))
    apis = MockAPIs()
    original = httpx.AsyncClient
    process = psutil.Process()
    with patch("httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(apis.respond), **kw)):
        clients = NarrationClients()
        clients.tts_url = "http://tts:8002"
        clients.url = "https://project.supabase.co"
        pipeline = NarrationPipeline(NarrationStore(sessions), clients)
        if mode == "cache_hit":
            await pipeline.request(LOCATION, "vi")
            await asyncio.gather(*list(pipeline.tasks))
        before = apis.calls.copy()
        peak = process.memory_info().rss
        sampling = True
        async def sample_ram():
            nonlocal peak
            while sampling:
                peak = max(peak, process.memory_info().rss)
                await asyncio.sleep(0.01)
        sampler = asyncio.create_task(sample_ram())
        cpu_before = sum(process.cpu_times()[:2])
        start = time.perf_counter()
        first_times, complete_times, statuses, initial_cache_hits = [], [], [], []

        async def user(number):
            location = {**LOCATION, "id": number + 1} if mode == "new_distinct" else LOCATION
            began = time.perf_counter()
            first = True
            for _ in range(1000):
                result = await pipeline.request(location, "vi")
                if first:
                    first_times.append((time.perf_counter() - began) * 1000)
                    initial_cache_hits.append(bool(result.get("cache_hit")))
                    first = False
                if result["narration_status"] in {"audio_ready", "error"}:
                    complete_times.append((time.perf_counter() - began) * 1000)
                    statuses.append(result)
                    return
                await asyncio.sleep(0.02)
            statuses.append({"narration_status": "error"})

        await asyncio.gather(*(user(n) for n in range(users)))
        wall = time.perf_counter() - start
        cpu = sum(process.cpu_times()[:2]) - cpu_before
        sampling = False
        await sampler
        await pipeline.close()
    engine.dispose()
    ordered = sorted(first_times)
    return {"mode": mode, "users": users, "first_response_p50_ms": round(statistics.median(first_times), 2),
            "first_response_p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
            "completion_p50_ms": round(statistics.median(complete_times), 2) if complete_times else None,
            "wall_seconds": round(wall, 3), "peak_rss_mb": round(peak / 1024 ** 2, 2),
            "cpu_one_core_percent": round(cpu / wall * 100, 2),
            "errors": sum(x["narration_status"] == "error" for x in statuses),
            "initial_cache_hit_ratio": round(sum(initial_cache_hits) / users, 3),
            "external_calls": {k: apis.calls[k] - before[k] for k in ["wiki", "gemini", "tts", "upload"]}}


async def main():
    results = []
    (ROOT / ".pytest-work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".pytest-work") as folder:
        for users in [5, 10, 20]:
            for mode in ["cache_hit", "new_same", "new_distinct"]:
                result = await measure(users, mode, Path(folder))
                results.append(result)
                print(json.dumps(result), flush=True)
    report = {"environment": "Local Windows, SQLite; mocked Wikimedia/Gemini/TTS/Storage; no HTTP Gateway or Render",
              "api_latency": "zero; does not model upstream latency or quota", "results": results}
    (ROOT / "docs/load-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
