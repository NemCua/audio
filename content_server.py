#!/usr/bin/env python3
"""
content_server.py — FastAPI port 8004
"""

import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    content: str

class SearchRequest(BaseModel):
    query: str

class SceneMedia(BaseModel):
    url: str
    thumb: str = ""
    is_video: bool = True
    duration: float = 6.0
    source: str = ""   # "pexels" | "pixabay" | "custom"

class Scene(BaseModel):
    query: str
    prompt: str = ""
    duration: float = 6.0
    media: Optional[SceneMedia] = None  # None = tự search

class GenerateRequest(BaseModel):
    content: str
    scenes: list[Scene]             # scenes với media đã chọn
    voice: str = ""
    rate: float = 1.0


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def _search_pexels_videos(query: str, pexels_key: str, n: int = 10) -> list[dict]:
    import requests
    results = []
    r = requests.get(
        'https://api.pexels.com/videos/search',
        headers={'Authorization': pexels_key},
        params={'query': query, 'per_page': n, 'orientation': 'landscape'},
        timeout=15,
    )
    for v in r.json().get('videos', []):
        files = sorted(
            [f for f in v['video_files'] if 640 <= f.get('width', 0) <= 1280],
            key=lambda f: f.get('width', 0), reverse=True,
        ) or sorted(
            [f for f in v['video_files'] if f.get('width', 0) > 0],
            key=lambda f: f.get('width', 0),
        )
        if files:
            thumb = v.get('image', '') or (v.get('video_pictures') or [{}])[0].get('picture', '')
            results.append({
                'url': files[0]['link'],
                'thumb': thumb,
                'duration': v['duration'],
                'is_video': True,
                'source': 'pexels',
                'id': v['id'],
            })
    return results


def _search_pexels_images(query: str, pexels_key: str, n: int = 10) -> list[dict]:
    import requests
    results = []
    r = requests.get(
        'https://api.pexels.com/v1/search',
        headers={'Authorization': pexels_key},
        params={'query': query, 'per_page': n, 'orientation': 'landscape'},
        timeout=15,
    )
    for p in r.json().get('photos', []):
        results.append({
            'url': p['src']['large2x'],
            'thumb': p['src']['medium'],
            'duration': 0,
            'is_video': False,
            'source': 'pexels',
            'id': p['id'],
        })
    return results


def _search_pixabay_videos(query: str, pixabay_key: str, n: int = 10) -> list[dict]:
    import requests
    results = []
    r = requests.get(
        'https://pixabay.com/api/videos/',
        params={'key': pixabay_key, 'q': query, 'per_page': n, 'order': 'popular'},
        timeout=15,
    )
    for v in r.json().get('hits', []):
        videos = v.get('videos', {})
        stream = (videos.get('medium') or videos.get('small') or
                  videos.get('large') or videos.get('tiny') or {})
        url = stream.get('url', '')
        thumb = stream.get('thumbnail', '')
        if url:
            results.append({
                'url': url,
                'thumb': thumb,
                'duration': v['duration'],
                'is_video': True,
                'source': 'pixabay',
                'id': v['id'],
            })
    return results


def _search_pixabay_images(query: str, pixabay_key: str, n: int = 10) -> list[dict]:
    import requests
    results = []
    r = requests.get(
        'https://pixabay.com/api/',
        params={'key': pixabay_key, 'q': query, 'per_page': n,
                'image_type': 'photo', 'orientation': 'horizontal'},
        timeout=15,
    )
    for p in r.json().get('hits', []):
        results.append({
            'url': p.get('largeImageURL', p.get('webformatURL', '')),
            'thumb': p.get('webformatURL', ''),
            'duration': 0,
            'is_video': False,
            'source': 'pixabay',
            'id': p['id'],
        })
    return results


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def _run_pipeline(job_id: str, req: GenerateRequest):
    import subprocess, time
    from pathlib import Path as P
    from content_video import (
        download_file, process_video_clip, process_image_clip,
        tts_content, capcut_tts_content, build_content_video,
        FFMPEG_BIN,
    )
    from translate_video import CAPCUT_VOICES_VI
    import asyncio

    jobs[job_id].update(status="running", progress=0, message="Đang khởi động...")

    def prog(pct, msg=""):
        jobs[job_id]["progress"] = int(pct * 100)
        jobs[job_id]["message"] = msg

    try:
        work_dir = P(tempfile.mkdtemp(prefix=f"cv_{job_id[:8]}_"))
        media_dir = work_dir / 'media'; media_dir.mkdir()
        clips_dir = work_dir / 'clips'; clips_dir.mkdir()

        pexels_key  = os.environ.get("PEXELS_API_KEY", "")
        pixabay_key = os.environ.get("PIXABAY_API_KEY", "")

        scenes_out = []
        for i, scene in enumerate(req.scenes):
            prog(0.05 + 0.45 * i / len(req.scenes), f'Xử lý cảnh {i+1}/{len(req.scenes)}: {scene.query}')

            clip_path = clips_dir / f'clip_{i:04d}.mp4'
            duration  = float(scene.duration or 6)
            media     = scene.media

            # Nếu user không chọn → tự search lấy cái đầu tiên
            if not media:
                from content_video import search_pexels_video, search_pixabay_video, search_pexels_image
                used: set = set()
                info = search_pexels_video(scene.query, pexels_key, used)
                if not info and pixabay_key:
                    info = search_pixabay_video(scene.query, pixabay_key, used)
                if info:
                    media = SceneMedia(url=info['url'], is_video=True,
                                       duration=info.get('duration', duration))
                else:
                    img = search_pexels_image(scene.query, pexels_key, used)
                    if img:
                        media = SceneMedia(url=img['url'], is_video=False, duration=duration)

            if not media:
                print(f'  ⚠ Không có media cho: {scene.query}')
                scenes_out.append({'query': scene.query, 'duration': duration, 'clip_path': None})
                continue

            # Download
            ext = '.mp4' if media.is_video else '.jpg'
            raw = media_dir / f'raw_{i:04d}{ext}'
            download_file(media.url, raw)

            try:
                if media.is_video:
                    actual = min(duration, float(media.duration or duration), 8.0)
                    process_video_clip(raw, clip_path, actual)
                    duration = actual
                else:
                    process_image_clip(raw, clip_path, duration)
            except subprocess.CalledProcessError as e:
                print(f'  ⚠ FFmpeg lỗi clip {i+1}: {e}')
                scenes_out.append({'query': scene.query, 'duration': duration, 'clip_path': None})
                continue

            scenes_out.append({'query': scene.query, 'duration': duration,
                                'clip_path': str(clip_path)})

        # TTS
        prog(0.55, 'Tạo TTS...')
        tts_path = work_dir / 'content_tts.mp3'
        capcut_info = next(
            (v for v in CAPCUT_VOICES_VI if v[1] == req.voice.strip()), None
        )
        capcut_device_id = os.environ.get("CAPCUT_DEVICE_ID", "")

        if capcut_info and capcut_device_id:
            sentence_durations = capcut_tts_content(
                req.content, tts_path,
                capcut_info[1], capcut_info[2], capcut_device_id,
                rate=str(round(req.rate, 1)),
            )
        else:
            sentence_durations = asyncio.run(tts_content(req.content, tts_path))

        # Ghép
        prog(0.80, 'Ghép video...')
        valid = [s for s in scenes_out if s.get('clip_path')]
        if not valid:
            raise RuntimeError('Không có clip nào hợp lệ')

        out = work_dir / 'content_video.mp4'
        build_content_video(valid, tts_path, out, work_dir,
                            sentence_durations=sentence_durations)

        jobs[job_id].update(status="done", progress=100,
                            message="Hoàn tất!", output=str(out))

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/preview")
async def preview(req: PreviewRequest):
    if not req.content.strip():
        return JSONResponse({"error": "Chưa nhập nội dung"}, status_code=400)
    try:
        from content_video import ai_split_scenes
        scenes = ai_split_scenes(
            req.content.strip(),
            os.environ.get("GROQ_API_KEY", ""),
            beeknoee_key=os.environ.get("BEEKNOEE_API_KEY", ""),
        )
        return {"scenes": scenes}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/search")
async def search(req: SearchRequest):
    """Tìm video + ảnh từ Pixabay (ưu tiên) + Pexels cho 1 query."""
    q           = req.query.strip()
    pexels_key  = os.environ.get("PEXELS_API_KEY", "")
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "")

    videos, images = [], []

    # Pixabay trước (10 mỗi loại)
    if pixabay_key:
        videos += _search_pixabay_videos(q, pixabay_key, 10)
        images += _search_pixabay_images(q, pixabay_key, 10)

    # Pexels bổ sung (10 mỗi loại)
    if pexels_key:
        pv = _search_pexels_videos(q, pexels_key, 10)
        pi = _search_pexels_images(q, pexels_key, 10)
        # Dedup theo URL
        existing_urls = {x['url'] for x in videos}
        videos += [x for x in pv if x['url'] not in existing_urls]
        existing_urls = {x['url'] for x in images}
        images += [x for x in pi if x['url'] not in existing_urls]

    return {"videos": videos[:20], "images": images[:20]}


@app.post("/api/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    if not req.content.strip():
        return JSONResponse({"error": "Chưa nhập nội dung"}, status_code=400)
    if not req.scenes:
        return JSONResponse({"error": "Chưa có danh sách cảnh"}, status_code=400)

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "pending", "progress": 0, "message": "Đang chờ..."}
    background_tasks.add_task(_run_pipeline, job_id, req)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job không tồn tại"}, status_code=404)
    return job


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return JSONResponse({"error": "Video chưa sẵn sàng"}, status_code=404)
    out = job.get("output")
    if not out or not Path(out).exists():
        return JSONResponse({"error": "File không tồn tại"}, status_code=404)
    return FileResponse(out, media_type="video/mp4", filename="content_video.mp4")


# Serve static
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
