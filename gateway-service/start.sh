#!/bin/sh
# gateway-service/start.sh
# Script wrapper giup supervisord doc bien $PORT dong truong khi chay uvicorn.
# Render inject bien PORT vao container luc runtime (khong biet truoc gia tri).
# supervisord khong co kha nang expand shell variable trong "command=",
# nen can dung script ngoai de thuc hien buoc do.

set -e

# Mac dinh cong 8000 neu Render khong inject PORT (phu hop khi test Docker local)
PORT=${PORT:-8000}

echo "[gateway-service] Dang khoi dong uvicorn tren port ${PORT}..."
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT}" --log-level info
