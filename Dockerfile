FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/threads

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
COPY allYoutube/videoPhanBiet ./allYoutube/videoPhanBiet

CMD ["sh", "-c", "uvicorn threads.unified_server:app --host 0.0.0.0 --port ${PORT:-8007}"]
