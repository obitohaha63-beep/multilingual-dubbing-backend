# render.Dockerfile - Dong goi 3 microservice vao 1 container duy nhat cho Render Free.
# Chay: content-service (8001), translate-audio-service (8002), gateway-service (8000)
# Dung supervisord de quan ly 3 tien trinh Python trong 1 container.
#
# Luu y: docker-compose.yml giu nguyen cho moi truong local.
# File nay CHI dung de build image trien khai tren Render.

FROM python:3.11-slim

# Cai supervisord (quan ly nhieu tien trinh) va dos2unix (chuan hoa line ending)
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Tao thu muc ung dung
WORKDIR /app

# Cai dependencies cua 3 service (gop vao 1 layer de toi uu build cache)
COPY content-service/requirements.txt /tmp/req-content.txt
COPY gateway-service/requirements.txt /tmp/req-gateway.txt
COPY translate-audio-service/requirements.txt /tmp/req-translate.txt

RUN pip install --no-cache-dir \
    -r /tmp/req-content.txt \
    -r /tmp/req-gateway.txt \
    -r /tmp/req-translate.txt

# Copy source code cua tung service
COPY content-service/         /app/content-service/
COPY gateway-service/         /app/gateway-service/
COPY translate-audio-service/ /app/translate-audio-service/

# Chuan hoa line ending sang Unix LF va cap quyen thuc thi cho start.sh
RUN dos2unix /app/gateway-service/start.sh \
    && chmod +x /app/gateway-service/start.sh

# Du lieu runtime tam: analytics SQLite va cache Edge TTS.
# Dia diem/thuyet minh dung DATABASE_URL Supabase, MP3 hoan tat o Supabase Storage.
RUN mkdir -p /app/data /app/audio_files

# Copy cau hinh supervisord
COPY supervisord.conf /etc/supervisor/conf.d/app.conf

# Render doc PORT tu env, gateway-service lang nghe tren $PORT
# Mac dinh 8000 khi chay local voi Docker truc tiep
ENV PORT=8000

# Bien moi truong noi bo cho giao tiep giua cac service trong cung container
ENV CONTENT_SERVICE_URL=http://127.0.0.1:8001
ENV TTS_SERVICE_URL=http://127.0.0.1:8002

# Duong dan du lieu trong container
ENV DB_PATH=/app/data/content.db
ENV AUDIO_DIR=/app/audio_files

# Cac bien moi truong mac dinh (tranh loi supervisord status 2 neu chua duoc set tren Render)
ENV WIKIMEDIA_USER_AGENT="multilingual-dubbing/1.0 (contact@example.com)"
ENV NARRATION_TTL_DAYS="7"
ENV TTS_PROVIDER_VI="google"
ENV GOOGLE_TTS_API_KEY=""
ENV TTS_TIMEOUT_SECONDS="25.0"
ENV TTS_CONFIG_VERSION="v1"
ENV TTS_VOICE_VI="vi-VN-HoaiMyNeural"
ENV TTS_VOICE_EN="en-US-JennyNeural"
ENV TTS_VOICE_JA="ja-JP-NanamiNeural"
ENV GATEWAY_TIMEOUT="6.0"
ENV GATEWAY_NARRATE_TIMEOUT="30.0"
ENV ADMIN_PATH="/admin-dashboard/"
ENV ADMIN_USERNAME="admin"
ENV ADMIN_PASSWORD=""
ENV ADMIN_SESSION_SECRET=""

# Mo cong chinh ma Render se expose
EXPOSE 8000

# Entrypoint: supervisord quan ly 3 tien trinh
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/app.conf"]
