"""Chạy riêng từng service để tránh trùng tên module main.py/models.py."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / ".pytest-work").mkdir(exist_ok=True)
commands = [
    ("content-service", ["test_content_service.py", "test_narration_pipeline.py", "test_postgres_claim.py"]),
    ("gateway-service", ["test_gateway_service.py", "test_admin_dashboard.py"]),
    ("translate-audio-service", ["tests/test_translate_audio.py"]),
]
failed = False
for directory, files in commands:
    result = subprocess.run([sys.executable, "-m", "pytest", *files, "-q", "--tb=short",
        f"--basetemp={root / '.pytest-work' / directory}"], cwd=root / directory)
    failed = failed or result.returncode != 0
result = subprocess.run(["node", "--test", "tests/test_client_logic.test.js", "tests/test_client_i18n.test.js"], cwd=root)
sys.exit(1 if failed or result.returncode else 0)
