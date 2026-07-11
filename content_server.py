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
from fastapi import FastAPI, BackgroundTasks, UploadFile, File
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
    source: str = ""   # "pexels" | "pixabay" | "web" | "local" | "custom"
    start: float = 0.0 # giây bắt đầu lấy trong clip nguồn (chỉ áp dụng cho video)

class Scene(BaseModel):
    query: str
    prompt: str = ""
    text: str = ""                      # câu thoại của cảnh — dùng cho Whisper align
    trigger_idx: Optional[int] = None   # chỉ số từ trigger trong text (tách khoảng trắng)
    duration: float = 6.0
    media: Optional[SceneMedia] = None  # None = tự search

class GenerateRequest(BaseModel):
    content: str
    scenes: list[Scene]             # scenes với media đã chọn
    voice: str = ""
    rate: float = 1.0
    subtitle: bool = True           # burn vietsub bám giọng đọc (Whisper word-level)


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

def _search_web_images(query: str, n: int = 12) -> list[dict]:
    """Tìm ảnh từ toàn bộ web qua DuckDuckGo (không cần API key).
    Trả về cùng format với các nguồn khác, source='web'."""
    try:
        from ddgs import DDGS
        with DDGS() as d:
            results = list(d.images(query, max_results=n))
        return [{
            'url': r.get('image', ''),
            'thumb': r.get('thumbnail') or r.get('image', ''),
            'is_video': False,
            'duration': 0,
            'source': 'web',
            'title': (r.get('title') or '')[:80],
        } for r in results if r.get('image')]
    except Exception as e:
        print(f'  ⚠ Web image search lỗi: {e}')
        return []


def _search_coverr_videos(query: str, api_key: str, n: int = 15) -> list[dict]:
    """Coverr Free Videos API — cần key riêng, hạn mức 50 req/giờ ở tier Demo.
    CHỈ gọi khi user chủ động bấm nút riêng, KHÔNG gộp vào search tự động
    (mỗi query tốn đúng 1 request bất kể page_size)."""
    import requests
    try:
        r = requests.get('https://api.coverr.co/videos', params={
            'query': query, 'page_size': n, 'urls': 'true', 'api_key': api_key,
        }, timeout=12)
        r.raise_for_status()
        hits = r.json().get('hits', [])
    except Exception as e:
        print(f'  ⚠ Coverr search lỗi: {e}')
        return []
    out = []
    for h in hits:
        mp4 = (h.get('urls') or {}).get('mp4', '')
        if mp4:
            out.append({'url': mp4, 'thumb': h.get('thumbnail', ''), 'is_video': True,
                        'duration': h.get('duration', 0), 'source': 'coverr'})
    return out


def _search_coverr_videos(query: str, api_key: str, n: int = 30) -> list[dict]:
    """Coverr Free Videos API — cần key riêng, hạn mức 50 req/giờ ở tier Demo.
    CHỈ gọi khi user chủ động bấm nút riêng, KHÔNG gộp vào search tự động
    (mỗi query tốn đúng 1 request bất kể page_size)."""
    import requests
    try:
        r = requests.get('https://api.coverr.co/videos', params={
            'query': query, 'page_size': n, 'urls': 'true', 'api_key': api_key,
        }, timeout=12)
        r.raise_for_status()
        hits = r.json().get('hits', [])
    except Exception as e:
        print(f'  ⚠ Coverr search lỗi: {e}')
        return []
    out = []
    for h in hits:
        mp4 = (h.get('urls') or {}).get('mp4', '')
        if mp4:
            out.append({'url': mp4, 'thumb': h.get('thumbnail', ''), 'is_video': True,
                        'duration': h.get('duration', 0), 'source': 'coverr'})
    return out


def _search_nasa(query: str, n_img: int = 10, n_vid: int = 6) -> tuple[list[dict], list[dict]]:
    """NASA Image and Video Library — miễn phí, KHÔNG cần API key.
    Ảnh + video vũ trụ/khoa học chất lượng cao. Trả về (videos, images)."""
    import requests
    from concurrent.futures import ThreadPoolExecutor
    try:
        r = requests.get('https://images-api.nasa.gov/search',
                         params={'q': query, 'media_type': 'image,video', 'page_size': 50},
                         timeout=12)
        items = r.json().get('collection', {}).get('items', [])
    except Exception as e:
        print(f'  ⚠ NASA search lỗi: {e}')
        return [], []

    images, vid_items = [], []
    for it in items:
        data = (it.get('data') or [{}])[0]
        links = it.get('links') or []
        thumb = next((l.get('href', '') for l in links if l.get('rel') == 'preview'), '')
        if data.get('media_type') == 'image' and thumb and len(images) < n_img:
            images.append({'url': thumb.replace('~thumb', '~large').replace('~small', '~large'),
                           'thumb': thumb, 'is_video': False, 'duration': 0, 'source': 'nasa'})
        elif data.get('media_type') == 'video' and it.get('href') and len(vid_items) < n_vid:
            vid_items.append((it['href'], thumb))

    def _resolve_mp4(args):
        href, thumb = args
        try:
            assets = requests.get(href, timeout=10).json()
            mp4 = (next((a for a in assets if a.endswith('~mobile.mp4')), None)
                   or next((a for a in assets if a.endswith('.mp4')), None))
            if mp4:
                return {'url': mp4, 'thumb': thumb, 'is_video': True, 'duration': 0, 'source': 'nasa'}
        except Exception:
            pass
        return None

    videos = []
    if vid_items:
        with ThreadPoolExecutor(max_workers=6) as ex:
            videos = [v for v in ex.map(_resolve_mp4, vid_items) if v]
    return videos, images


def _search_wikimedia_images(query: str, n: int = 10) -> list[dict]:
    """Wikimedia Commons — miễn phí, KHÔNG cần API key. Kho ảnh khoa học/thiên nhiên lớn."""
    import requests
    try:
        r = requests.get('https://commons.wikimedia.org/w/api.php', params={
            'action': 'query', 'format': 'json', 'generator': 'search',
            'gsrsearch': f'filetype:bitmap {query}', 'gsrlimit': n, 'gsrnamespace': 6,
            'prop': 'imageinfo', 'iiprop': 'url', 'iiurlwidth': 1600,
        }, headers={'User-Agent': 'ContentVideoTool/1.0'}, timeout=12)
        pages = r.json().get('query', {}).get('pages', {})
    except Exception as e:
        print(f'  ⚠ Wikimedia search lỗi: {e}')
        return []
    out = []
    for p in pages.values():
        ii = (p.get('imageinfo') or [{}])[0]
        url = ii.get('thumburl') or ii.get('url')
        if url:
            out.append({'url': url, 'thumb': url, 'is_video': False,
                        'duration': 0, 'source': 'wikimedia'})
    return out


def _run_pipeline(job_id: str, req: GenerateRequest):
    import subprocess, time
    from pathlib import Path as P
    from content_video import (
        download_file, process_video_clip, process_image_clip,
        tts_content, capcut_tts_content, build_content_video,
        whisper_words, whisper_align_scenes, make_ass_subtitles,
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
                scenes_out.append({'query': scene.query, 'text': scene.text,
                                   'trigger_idx': scene.trigger_idx,
                                   'duration': duration, 'clip_path': None})
                continue

            # Download hoặc copy local
            ext = '.mp4' if media.is_video else Path(media.url).suffix.split('?')[0] or '.jpg'
            raw = media_dir / f'raw_{i:04d}{ext}'
            try:
                if media.source == 'local' and Path(media.url).exists():
                    import shutil as _sh
                    _sh.copy2(media.url, raw)
                else:
                    download_file(media.url, raw)
            except Exception as e:
                print(f'  ⚠ Download lỗi clip {i+1} ({media.source}): {e}')
                scenes_out.append({'query': scene.query, 'text': scene.text,
                                   'trigger_idx': scene.trigger_idx,
                                   'duration': duration, 'clip_path': None})
                continue

            try:
                if media.is_video:
                    start = max(0.0, float(media.start or 0))
                    total = float(media.duration or 0)
                    if total > 0:
                        remain = total - start
                        if remain <= 0.5:  # start vượt quá cuối clip → bỏ offset
                            start, remain = 0.0, total
                    else:
                        remain = duration  # không rõ thời lượng nguồn (vd NASA) — tin start của user
                    actual = min(duration, remain, 8.0)
                    process_video_clip(raw, clip_path, actual, start=start)
                    duration = actual
                else:
                    process_image_clip(raw, clip_path, duration)
            except subprocess.CalledProcessError as e:
                print(f'  ⚠ FFmpeg lỗi clip {i+1}: {e}')
                scenes_out.append({'query': scene.query, 'text': scene.text,
                                   'trigger_idx': scene.trigger_idx,
                                   'duration': duration, 'clip_path': None})
                continue

            scenes_out.append({'query': scene.query, 'text': scene.text,
                                'trigger_idx': scene.trigger_idx,
                                'duration': duration, 'clip_path': str(clip_path)})

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

        # Whisper word-level alignment — PHẢI chạy trên TOÀN BỘ scenes_out (kể cả
        # scene không có clip), vì audio TTS đọc đủ hết content; align trên danh sách
        # đã lọc sẽ làm lệch timestamp mọi scene phía sau. Trigger word (trigger_idx)
        # cũng được xử lý trong bước này.
        scene_starts_all = None
        whisper_word_list = None
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if groq_key and any(s.get('text') for s in scenes_out):
            try:
                prog(0.70, 'Căn chỉnh thời gian bằng Whisper...')
                words = whisper_words(tts_path, groq_key)
                if words:
                    whisper_word_list = words
                    scene_starts_all = whisper_align_scenes(scenes_out, words)
                    print(f'  ✓ Whisper align: {len(words)} từ, {len(scene_starts_all)} mốc thời gian')
            except Exception as e:
                print(f'  ⚠ Whisper align lỗi, dùng fallback theo câu TTS: {e}')

        # Vietsub: chữ từ script, thời gian từ Whisper — chỉ làm được khi Whisper OK
        subtitle_path = None
        if req.subtitle and whisper_word_list:
            try:
                prog(0.75, 'Tạo vietsub...')
                ass_path = work_dir / 'vietsub.ass'
                if make_ass_subtitles(scenes_out, whisper_word_list, ass_path):
                    subtitle_path = ass_path
            except Exception as e:
                print(f'  ⚠ Tạo vietsub lỗi, bỏ qua: {e}')
        elif req.subtitle:
            print('  ⚠ Không có Whisper words — bỏ qua vietsub (cần chính xác tuyệt đối)')

        # Ghép — lọc valid + scene_starts song song, giữ đúng tương ứng vị trí
        prog(0.80, 'Ghép video...')
        valid: list[dict] = []
        scene_starts = [] if scene_starts_all else None
        for i, s in enumerate(scenes_out):
            if s.get('clip_path'):
                valid.append(s)
                if scene_starts_all is not None:
                    scene_starts.append(scene_starts_all[i])
        if not valid:
            raise RuntimeError('Không có clip nào hợp lệ')

        out = work_dir / 'content_video.mp4'
        build_content_video(valid, tts_path, out, work_dir,
                            sentence_durations=sentence_durations,
                            scene_starts=scene_starts,
                            subtitle_path=subtitle_path)

        jobs[job_id].update(status="done", progress=100,
                            message="Hoàn tất!", output=str(out))

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower() or ".jpg"
    tmp = Path(tempfile.mkdtemp(prefix="cv_upload_")) / f"upload{suffix}"
    tmp.write_bytes(await file.read())
    return {"path": str(tmp)}


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

    def _add(dst: list, items: list):
        seen = {x['url'] for x in dst}
        dst += [x for x in items if x['url'] not in seen]

    # Pixabay trước (15 mỗi loại)
    if pixabay_key:
        videos += _search_pixabay_videos(q, pixabay_key, 15)
        images += _search_pixabay_images(q, pixabay_key, 15)

    # Pexels bổ sung (15 mỗi loại)
    if pexels_key:
        _add(videos, _search_pexels_videos(q, pexels_key, 15))
        _add(images, _search_pexels_images(q, pexels_key, 15))

    # NASA — ảnh + video vũ trụ/khoa học, không cần key
    nasa_videos, nasa_images = _search_nasa(q, n_img=10, n_vid=6)
    _add(videos, nasa_videos)
    _add(images, nasa_images)

    # Wikimedia Commons — ảnh khoa học/thiên nhiên, không cần key
    _add(images, _search_wikimedia_images(q, 10))

    # Toàn bộ web (DuckDuckGo) — chỉ ảnh, xếp cuối
    _add(images, _search_web_images(q, 20))

    return {"videos": videos[:40], "images": images[:80]}


@app.post("/api/search-coverr")
async def search_coverr(req: SearchRequest):
    """Coverr Free Videos API — endpoint RIÊNG, chỉ gọi khi user bấm nút thủ công.
    KHÔNG gộp vào /api/search vì hạn mức chỉ 50 request/giờ (tier Demo)."""
    api_key = os.environ.get("COVERR_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "Chưa cấu hình COVERR_API_KEY"}, status_code=400)
    videos = _search_coverr_videos(req.query.strip(), api_key, 30)
    return {"videos": videos, "images": []}


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
