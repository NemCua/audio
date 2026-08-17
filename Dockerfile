FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/threads \
    PORT=8007 \
    TTS_AUDIO_DIR=/data/tts_audio

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-render.txt ./
RUN pip install --no-cache-dir -r requirements-render.txt

# Chỉ đóng gói các module mà API desktop thực sự import. Repo còn chứa nhiều
# pipeline dịch/render nặng; chúng không được đưa vào image production.
COPY translate_video.py ./translate_video.py
COPY capcut-tts-api-main ./capcut-tts-api-main
COPY threads/account_api.py threads/tts_server.py threads/unified_server.py ./threads/

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/tts_audio \
    && chown -R appuser:appuser /app /data

USER appuser
EXPOSE 8007

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8007/health', timeout=4)" || exit 1

CMD ["uvicorn", "threads.unified_server:app", "--host", "0.0.0.0", "--port", "8007", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
