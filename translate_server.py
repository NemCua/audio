#!/usr/bin/env python3
"""
translate_server.py — FastAPI port 8005
Web UI cho tab Dịch Video (thay thế Gradio)
"""

import asyncio
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import httpx
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, Request
from starlette import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel

load_dotenv()

AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "")
DATABASE_URL    = os.getenv("DATABASE_URL", "")
AUTH_ALGORITHM  = "HS256"
FFMPEG_BIN      = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN     = str(Path(FFMPEG_BIN).parent / "ffprobe")

PRICE_PER_MIN   = 85    # đồng / phút
FREE_THRESHOLD  = 3 * 60  # ≤ 3 phút → miễn phí

AUDIO_DIR = Path(__file__).parent / "audio"
AUDIO_DIR.mkdir(exist_ok=True)
EXPORT_DIR = Path(__file__).parent / "exports"
EXPORT_DIR.mkdir(exist_ok=True)
BEEKNOEE_TITLE_MODEL = "glm-4.5-flash"

# Routes that don't require auth
_PUBLIC_PATHS = {"/", "/auth/login", "/auth/register"}

# ── DB helpers ───────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_get_balance(user_id: int) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
    return row["balance"] if row else 0

def db_deduct(user_id: int, amount: int, note: str) -> bool:
    """Trừ tiền, trả False nếu không đủ số dư."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET balance = balance - %s "
                "WHERE id = %s AND balance >= %s RETURNING balance",
                (amount, user_id, amount)
            )
            ok = cur.fetchone() is not None
            if ok:
                cur.execute(
                    "INSERT INTO topup_txns(user_id,amount,note,status) VALUES(%s,%s,%s,'completed')",
                    (user_id, -amount, note)
                )
    return ok

def db_refund(user_id: int, amount: int, note: str):
    """Hoàn tiền khi job lỗi."""
    if amount <= 0:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s", (amount, user_id))
            cur.execute(
                "INSERT INTO topup_txns(user_id,amount,note,status) VALUES(%s,%s,%s,'refunded')",
                (user_id, amount, note)
            )

# ── Billing helpers ──────────────────────────────────────────────
def get_video_duration(path: str | Path) -> float:
    """Trả về duration (giây) dùng ffprobe."""
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0

def calc_cost(duration_secs: float) -> int:
    """Tính tiền VND. Video ≤ 5 phút miễn phí."""
    if duration_secs <= FREE_THRESHOLD:
        return 0
    minutes = math.ceil(duration_secs / 60)
    return minutes * PRICE_PER_MIN

# ── Tải video từ link (Douyin/TikTok/...) qua yt-dlp ────────────────
# Chỉ cho phép domain video công khai đã biết — chặn SSRF (không cho
# người dùng đưa URL trỏ vào localhost/IP nội bộ/file:// qua yt-dlp).
_ALLOWED_URL_HOSTS = (
    "douyin.com", "iesdouyin.com", "tiktok.com", "www.tiktok.com",
)

def _is_allowed_video_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_URL_HOSTS)


def download_video_from_url(url: str, dst_dir: str) -> str:
    """Tải video (Douyin/TikTok) bằng yt-dlp, trả về đường dẫn file mp4 đã tải.
    Raise RuntimeError nếu URL không hợp lệ hoặc tải thất bại."""
    if not _is_allowed_video_url(url):
        raise RuntimeError("Link không được hỗ trợ — chỉ nhận link Douyin hoặc TikTok")

    out_tmpl = str(Path(dst_dir) / "%(id)s.%(ext)s")
    r = subprocess.run(
        ["yt-dlp", "--no-playlist", "-f", "mp4/best",
         "--cookies-from-browser", "brave",
         "-o", out_tmpl, "--print", "after_move:filepath", url],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Tải video thất bại: {r.stderr.strip()[-300:]}")

    filepath = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if not filepath or not Path(filepath).exists():
        raise RuntimeError("yt-dlp không trả về file hợp lệ")
    return filepath

# ── JWT decode helper ────────────────────────────────────────────
def decode_user_id(token: str) -> int | None:
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


def decode_media_user_id(token: str, job_id: str) -> int | None:
    """Decode a short-lived token used by the native <video> element.

    A video element cannot attach the Authorization header, so media playback
    gets a narrowly scoped token instead of exposing the normal login token in
    the URL.
    """
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        if payload.get("purpose") != "media" or payload.get("job_id") != job_id:
            return None
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def ngrok_skip_warning(request: Request, call_next):
    response = await call_next(request)
    response.headers["ngrok-skip-browser-warning"] = "1"
    return response

@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/static") or path in _PUBLIC_PATHS
            or request.method == "OPTIONS"):
        return await call_next(request)
    if path.startswith("/api/"):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        # Native media requests cannot send our Authorization header. Accept a
        # short-lived, job-scoped token only for the video download endpoint.
        media_prefix = "/api/download/"
        if path.startswith(media_prefix) and request.query_params.get("media_token"):
            job_id = path[len(media_prefix):]
            uid = decode_media_user_id(request.query_params["media_token"], job_id)
            if uid is None:
                return JSONResponse(status_code=http_status.HTTP_401_UNAUTHORIZED,
                                    content={"detail": "Media token khong hop le"})
            request.state.user_id = uid
            return await call_next(request)
        if not token or not AUTH_SECRET_KEY:
            return JSONResponse(status_code=http_status.HTTP_401_UNAUTHORIZED,
                                content={"detail": "Chua dang nhap"})
        uid = decode_user_id(token)
        if uid is None:
            return JSONResponse(status_code=http_status.HTTP_401_UNAUTHORIZED,
                                content={"detail": "Token khong hop le"})
        request.state.user_id = uid
    return await call_next(request)

jobs: dict[str, dict] = {}   # job_id -> {status, progress, message, output, ...}

# Cờ: user đã bật xoay VPN chưa. Chỉ khi bật, pipeline mới tự đổi IP lúc CapCut chặn.
_vpn_auto_rotate = {"on": False}


def _vpn_rotate_cb():
    """Callback truyền vào build_tts_track: đổi IP khi CapCut chặn.
    Chỉ hoạt động nếu user đã bật VPN từ web (tránh tự ý bật VPN)."""
    if not _vpn_auto_rotate["on"] or _vpn is None:
        return
    _vpn.ensure_list()
    _vpn.rotate()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TranslateRequest(BaseModel):
    job_id: str
    voice: str = "BV421_vivn_streaming"
    bg_volume: float = 0.3
    tts_volume: float = 1.8
    groq_key: str = ""          # user tự nhập


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _prog(job_id: str, pct: float, msg: str):
    jobs[job_id]["progress"] = int(pct * 100)
    jobs[job_id]["message"]  = msg

def _log(job_id: str, line: str):
    buf = jobs[job_id].setdefault("log", [])
    buf.append(line)
    if len(buf) > 2000:
        buf.pop(0)

class _LogCapture:
    """Redirect stdout/stderr của thread hiện tại vào job log."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        self._orig_write = None

    def __enter__(self):
        orig_stdout = sys.stdout
        job_id = self.job_id
        class _W:
            def write(self, s):
                if s.strip():
                    _log(job_id, s.rstrip())
                orig_stdout.write(s)
            def flush(self): orig_stdout.flush()
            def __getattr__(self, a): return getattr(orig_stdout, a)
        self._orig = orig_stdout
        sys.stdout = _W()
        return self

    def __exit__(self, *_):
        sys.stdout = self._orig


def _run_translate_pipeline(job_id: str, video_path: str, req: TranslateRequest):
    """Bước 1: STT + Dịch — dừng lại để người dùng xem/sửa phụ đề."""
    from translate_video import (
        extract_audio_for_stt, stt_groq, translate_srt, build_srt,
    )

    jobs[job_id].update(status="running", progress=0, message="Đang khởi động...")

    groq_key     = req.groq_key.strip() or os.environ.get("GROQ_API_KEY", "")
    beeknoee_key = os.environ.get("BEEKNOEE_API_KEY", "")

    if not groq_key:
        jobs[job_id].update(status="error", message="Chưa có Groq API key")
        _maybe_refund(job_id)
        return

    try:
        work_dir = Path(tempfile.mkdtemp(prefix=f"tr_{job_id[:8]}_"))
        src      = Path(video_path)
        video_copy = work_dir / ("video" + src.suffix)
        shutil.copy2(src, video_copy)

        jobs[job_id]["work_dir"]   = str(work_dir)
        jobs[job_id]["video_path"] = str(video_copy)
        jobs[job_id]["voice"]      = req.voice
        jobs[job_id]["bg_volume"]  = req.bg_volume
        jobs[job_id]["tts_volume"] = req.tts_volume

        # 1. Tách audio STT
        _prog(job_id, 0.1, "Tách audio...")
        audio_stt = work_dir / "audio_stt.mp3"
        extract_audio_for_stt(video_copy, audio_stt)

        # 2. STT
        _prog(job_id, 0.3, "Nhận dạng giọng nói (Whisper)...")
        zh_cues = stt_groq(audio_stt, groq_key)
        jobs[job_id]["zh_cues"] = zh_cues

        # 3. Dịch
        _prog(job_id, 0.5, f"Dịch {len(zh_cues)} đoạn...")

        def on_chunk(done, total, _partial):
            pct = 0.5 + (done / total) * 0.45
            _prog(job_id, pct, f"Dịch chunk {done}/{total}...")

        vi_cues = translate_srt(zh_cues, groq_key, beeknoee_key=beeknoee_key, chunk_cb=on_chunk)
        jobs[job_id]["vi_cues"] = vi_cues

        srt_path = work_dir / "captions_vi.srt"
        srt_path.write_text(build_srt(vi_cues), encoding="utf-8")
        jobs[job_id]["srt_path"] = str(srt_path)

        jobs[job_id].update(status="translated", progress=100,
                            message=f"Dịch xong {len(vi_cues)} đoạn. Xem và nhấn Tạo video.")

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))
        _maybe_refund(job_id)


def _run_render(job_id: str, voice: str, bg_volume: float, tts_volume: float,
                capcut_rate: str = "1.0", speed_ratio: float = 1.0,
                bg_music_path: str | None = None,
                logo_path: str | None = None,
                logo_pos: str = "topright", logo_size: int = 100,
                logo_tab: str = "none", logo_text: str = "",
                logo_fontsize: int = 28, logo_color: str = "#ffffff",
                keep_original: bool = False, original_volume: float = 0.3,
                strip_background_music: bool = False,
                encode_preset: str = "ultrafast", thumbnail_path: str | None = None):
    """Bước 2: TTS CapCut + render video."""
    from translate_video import (
        build_srt, build_tts_track, render_video, get_audio_duration, CAPCUT_VOICES_VI,
        build_light_video_enhance_filter,
    )

    job = jobs[job_id]
    jobs[job_id].update(status="rendering", progress=0, message="Bắt đầu tạo TTS...", log=[])

    cap = _LogCapture(job_id)
    cap.__enter__()
    try:
        work_dir   = Path(job["work_dir"])
        video_path = Path(job["video_path"])
        vi_cues    = job["vi_cues"]
        srt_path   = work_dir / "captions_vi.srt"
        srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

        _prog(job_id, 0.1, "Tạo TTS (CapCut)...")
        capcut_info        = next((v for v in CAPCUT_VOICES_VI if v[1] == voice), None)
        capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", "") if capcut_info else None
        capcut_voice_type  = capcut_info[1] if capcut_info else None
        capcut_resource_id = capcut_info[2] if capcut_info else None

        def _tts_progress(done: int, total: int):
            pct = 0.1 + 0.7 * (done / total) if total else 0.1
            _prog(job_id, pct, f"TTS {done}/{total} đoạn...")

        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(
            vi_cues, work_dir, video_dur,
            beeknoee_key=None,
            beeknoee_tts_model=None,
            beeknoee_tts_voice=None,
            capcut_device_id=capcut_device_id,
            capcut_voice_type=capcut_voice_type,
            capcut_resource_id=capcut_resource_id,
            capcut_delay=1.5,
            capcut_rate=capcut_rate,
            speed_ratio=speed_ratio,
            progress_cb=_tts_progress,
            vpn_rotate_cb=_vpn_rotate_cb,
        ))

        _prog(job_id, 0.85, "Render video...")
        output_path = work_dir / f"{video_path.stem}_vi.mp4"
        orig_audio_path = None
        if keep_original:
            if strip_background_music:
                jobs[job_id]["message"] = "Đang tách giọng để bỏ nhạc nền..."
                _prog(job_id, 0.82, "Đang tách giọng để bỏ nhạc nền...")
                from translate_video import extract_vocals_for_mix
                orig_audio_path = extract_vocals_for_mix(video_path, work_dir)
            else:
                orig_audio_path = work_dir / "original_audio.wav"
                from translate_video import extract_audio_for_stt
                extract_audio_for_stt(video_path, orig_audio_path)
        render_video(video_path, tts_track, srt_path, output_path,
                     bg_music=Path(bg_music_path) if bg_music_path else None,
                     bg_volume=bg_volume, tts_volume=tts_volume,
                     original_audio=orig_audio_path, original_volume=original_volume,
                     logo=Path(logo_path) if logo_path else None,
                     logo_pos=logo_pos, logo_size=logo_size,
                     watermark=logo_text,
                     encode_preset=encode_preset,
                     thumbnail=Path(thumbnail_path) if thumbnail_path else None)

        # Keep a durable copy outside the temporary job directory. Temporary
        # folders may be cleaned by macOS or maintenance, and a successful
        # translation must never disappear before the user downloads it.
        durable_name = f"{video_path.stem}_vi_{job_id[:8]}.mp4"
        durable_output = EXPORT_DIR / durable_name
        shutil.copy2(output_path, durable_output)

        jobs[job_id].update(status="done", progress=100,
                            message="Hoàn tất!", output=str(durable_output),
                            temp_output=str(output_path))

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))
        _maybe_refund(job_id)
    finally:
        cap.__exit__(None, None, None)


def _maybe_refund(job_id: str):
    """Hoàn tiền nếu job bị lỗi và đã trừ tiền."""
    job = jobs.get(job_id, {})
    user_id = job.get("user_id")
    cost    = job.get("cost", 0)
    if user_id and cost > 0 and not job.get("refunded"):
        job["refunded"] = True
        db_refund(user_id, cost, f"Hoan tien job {job_id[:8]} that bai")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/bg-music-list")
async def bg_music_list(request: Request):
    """Liệt kê nhạc nền có sẵn trong thư mục audio/ để chọn + nghe thử."""
    exts = (".mp3", ".wav", ".m4a", ".aac", ".ogg")
    files = sorted(p.name for p in AUDIO_DIR.iterdir() if p.suffix.lower() in exts and p.is_file())
    return {"files": files}


@app.get("/api/bg-music/{filename}")
async def bg_music_file(filename: str, request: Request):
    """Serve 1 file nhạc trong audio/ — dùng để nghe thử hoặc làm nhạc nền khi render."""
    name = Path(filename).name  # chặn path traversal (../..)
    path = AUDIO_DIR / name
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "Không tìm thấy file"}, status_code=404)
    return FileResponse(path)


@app.post("/api/video-cost")
async def video_cost(request: Request, file: UploadFile = File(...)):
    """Tính giá video trước khi upload thật. Trả về duration + cost + balance."""
    user_id = request.state.user_id
    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()
    try:
        duration = get_video_duration(tmp.name)
        cost     = calc_cost(duration)
        balance  = db_get_balance(user_id)
        return {
            "duration_secs": round(duration, 1),
            "duration_min":  round(duration / 60, 1),
            "cost":          cost,
            "balance":       balance,
            "can_afford":    balance >= cost,
            "free":          cost == 0,
        }
    finally:
        os.unlink(tmp.name)


@app.post("/api/upload")
async def upload_video(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    voice: str = Form("BV421_vivn_streaming"),
    bg_volume: float = Form(0.3),
    tts_volume: float = Form(1.8),
    groq_key: str = Form(""),
):
    user_id = request.state.user_id
    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    duration = get_video_duration(tmp.name)
    cost     = calc_cost(duration)

    if cost > 0:
        ok = db_deduct(user_id, cost, f"Dich video: {file.filename} ({round(duration/60,1)} phut)")
        if not ok:
            os.unlink(tmp.name)
            balance = db_get_balance(user_id)
            return JSONResponse(
                {"error": f"So du khong du. Can {cost:,}d, hien co {balance:,}d"},
                status_code=402
            )

    job_id = str(uuid.uuid4())
    req    = TranslateRequest(job_id=job_id, voice=voice,
                              bg_volume=bg_volume, tts_volume=tts_volume,
                              groq_key=groq_key)
    jobs[job_id] = {
        "status": "pending", "progress": 0, "message": "Dang cho...",
        "filename": file.filename,
        "user_id": user_id, "cost": cost, "duration_secs": round(duration, 1),
    }
    background_tasks.add_task(_run_translate_pipeline, job_id, tmp.name, req)
    return {"job_id": job_id, "cost": cost, "duration_secs": round(duration, 1)}


@app.post("/api/upload-from-url")
async def upload_video_from_url(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    voice: str = Form("BV421_vivn_streaming"),
    bg_volume: float = Form(0.3),
    tts_volume: float = Form(1.8),
    groq_key: str = Form(""),
):
    """Nhận link Douyin/TikTok, tự tải về bằng yt-dlp rồi chạy pipeline dịch
    y hệt /api/upload — người dùng không cần tải file thủ công."""
    user_id = request.state.user_id
    url = url.strip()

    tmp_dir = tempfile.mkdtemp(prefix="dlvid_")
    try:
        filepath = await asyncio.get_running_loop().run_in_executor(
            None, download_video_from_url, url, tmp_dir)
    except RuntimeError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return JSONResponse({"error": str(e)}, status_code=400)

    filename = Path(filepath).name
    duration = get_video_duration(filepath)
    cost     = calc_cost(duration)

    if cost > 0:
        ok = db_deduct(user_id, cost, f"Dich video (link): {filename} ({round(duration/60,1)} phut)")
        if not ok:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            balance = db_get_balance(user_id)
            return JSONResponse(
                {"error": f"So du khong du. Can {cost:,}d, hien co {balance:,}d"},
                status_code=402
            )

    job_id = str(uuid.uuid4())
    req    = TranslateRequest(job_id=job_id, voice=voice,
                              bg_volume=bg_volume, tts_volume=tts_volume,
                              groq_key=groq_key)
    jobs[job_id] = {
        "status": "pending", "progress": 0, "message": "Dang cho...",
        "filename": filename,
        "user_id": user_id, "cost": cost, "duration_secs": round(duration, 1),
    }
    background_tasks.add_task(_run_translate_pipeline, job_id, filepath, req)
    return {"job_id": job_id, "cost": cost, "duration_secs": round(duration, 1), "filename": filename}


@app.post("/api/upload-with-srt")
async def upload_with_srt(
    request:   Request,
    file:      UploadFile = File(...),
    srt:       UploadFile = File(...),
    voice:     str   = Form("BV421_vivn_streaming"),
    bg_volume: float = Form(0.3),
    tts_volume:float = Form(1.8),
):
    """Upload video + SRT sẵn có, bỏ qua STT + dịch AI."""
    from translate_video import parse_srt, build_srt

    user_id = request.state.user_id
    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    duration = get_video_duration(tmp.name)
    cost     = calc_cost(duration)

    if cost > 0:
        ok = db_deduct(user_id, cost, f"Dich video (SRT): {file.filename} ({round(duration/60,1)} phut)")
        if not ok:
            os.unlink(tmp.name)
            balance = db_get_balance(user_id)
            return JSONResponse(
                {"error": f"So du khong du. Can {cost:,}d, hien co {balance:,}d"},
                status_code=402
            )

    srt_content = (await srt.read()).decode("utf-8", errors="replace")
    try:
        vi_cues = parse_srt(srt_content)
    except Exception as e:
        if cost > 0: db_refund(user_id, cost, "Hoan tien SRT khong hop le")
        os.unlink(tmp.name)
        return JSONResponse({"error": f"SRT khong hop le: {e}"}, status_code=400)
    if not vi_cues:
        if cost > 0: db_refund(user_id, cost, "Hoan tien SRT rong")
        os.unlink(tmp.name)
        return JSONResponse({"error": "SRT rong hoac sai dinh dang"}, status_code=400)

    job_id     = str(uuid.uuid4())
    work_dir   = Path(tempfile.mkdtemp(prefix=f"tr_{job_id[:8]}_"))
    video_copy = work_dir / ("video" + suffix)
    shutil.copy2(tmp.name, video_copy)
    os.unlink(tmp.name)

    srt_path = work_dir / "captions_vi.srt"
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

    jobs[job_id] = {
        "status":       "translated",
        "progress":     100,
        "message":      f"Da nap SRT ({len(vi_cues)} doan)",
        "filename":     file.filename,
        "work_dir":     str(work_dir),
        "video_path":   str(video_copy),
        "srt_path":     str(srt_path),
        "vi_cues":      vi_cues,
        "zh_cues":      [],
        "voice":        voice,
        "bg_volume":    bg_volume,
        "tts_volume":   tts_volume,
        "user_id":      user_id,
        "cost":         cost,
        "duration_secs": round(duration, 1),
    }

    rows = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
             "zh": "", "vi": c.get("text", "")} for c in vi_cues]
    return {"job_id": job_id, "rows": rows, "cost": cost, "duration_secs": round(duration, 1)}


@app.get("/api/me/balance")
async def my_balance(request: Request):
    balance = db_get_balance(request.state.user_id)
    return {"balance": balance}


def _get_job(job_id: str, user_id: int):
    """Lấy job, trả None nếu không tồn tại hoặc không phải của user này."""
    job = jobs.get(job_id)
    if not job:
        return None, JSONResponse({"error": "Khong tim thay"}, status_code=404)
    if job.get("user_id") != user_id:
        return None, JSONResponse({"error": "Khong co quyen"}, status_code=403)
    return job, None


@app.get("/api/status/{job_id}")
async def status(job_id: str, request: Request, log_from: int = 0):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    result = {k: v for k, v in job.items() if k not in ("vi_cues", "zh_cues", "log")}
    all_log = job.get("log", [])
    result["log"] = all_log[log_from:]
    result["log_total"] = len(all_log)
    return result


@app.get("/api/cues/{job_id}")
async def get_cues(job_id: str, request: Request):
    """Trả về bảng phụ đề để hiển thị/edit."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    return {"rows": _cues_rows(job.get("vi_cues", []), job.get("zh_cues", []))}


@app.post("/api/save-cues/{job_id}")
async def save_cues(job_id: str, body: dict, request: Request):
    """Lưu bản dịch đã edit. Xây LẠI TOÀN BỘ vi_cues từ rows gửi lên (không
    map theo vị trí của bản cũ) — bắt buộc vì client có thể tách 1 dòng dài
    thành nhiều dòng (mỗi dòng có start/end mới) hoặc gộp/xoá dòng, nên số
    lượng và timing đều có thể khác hẳn vi_cues cũ. Map theo vị trí cũ sẽ làm
    mất các dòng mới thêm và giữ nguyên timing cũ (sai) — đúng lỗi đã gặp."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error", "rendering"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    from translate_video import build_srt, srt_time_to_sec

    rows = body.get("rows", [])
    if not rows:
        return JSONResponse({"error": "Danh sach rows rong"}, status_code=400)

    updated = []
    previous_end = None
    for i, r in enumerate(rows):
        start = str(r.get("start", "")).strip()
        end   = str(r.get("end", "")).strip()
        text  = str(r.get("vi", "")).strip()
        if not start or not end:
            return JSONResponse({"error": f"Dong {i+1} thieu start/end"}, status_code=400)
        time_pattern = r"^\d{2,}:([0-5]\d):([0-5]\d),(\d{3})$"
        if not re.fullmatch(time_pattern, start) or not re.fullmatch(time_pattern, end):
            return JSONResponse({"error": f"Dong {i+1} co timestamp sai dinh dang HH:MM:SS,mmm"}, status_code=400)
        try:
            start_sec = srt_time_to_sec(start)
            end_sec = srt_time_to_sec(end)
        except Exception:
            return JSONResponse({"error": f"Dong {i+1} co timestamp sai dinh dang HH:MM:SS,mmm"}, status_code=400)
        if start_sec < 0 or end_sec <= start_sec:
            return JSONResponse({"error": f"Dong {i+1}: thoi gian ket thuc phai lon hon bat dau"}, status_code=400)
        if previous_end is not None and start_sec < previous_end:
            return JSONResponse({"error": f"Dong {i+1} bi chong thoi gian voi dong truoc"}, status_code=400)
        updated.append({
            "idx": i + 1,
            "start": start,
            "end": end,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": text,
        })
        previous_end = end_sec
    job["vi_cues"] = updated

    srt_path = Path(job["srt_path"])
    srt_path.write_text(build_srt(updated), encoding="utf-8")
    return {"ok": True, "count": len(updated)}


@app.post("/api/render/{job_id}")
async def render(
    job_id:           str,
    request:          Request,
    background_tasks: BackgroundTasks,
    voice:            str   = Form("BV421_vivn_streaming"),
    bg_volume:        float = Form(0.3),
    tts_volume:       float = Form(1.8),
    speed_ratio:      float = Form(1.0),
    logo_pos:         str   = Form("topright"),
    logo_size:        int   = Form(100),
    logo_tab:         str   = Form("none"),
    logo_text:        str   = Form(""),
    logo_fontsize:    int   = Form(28),
    logo_color:       str   = Form("#ffffff"),
    keep_original:    bool  = Form(False),
    original_volume:  float = Form(0.3),
    strip_background_music: bool = Form(False),
    encode_preset:    str   = Form("ultrafast"),
    bg_music_name:    str   = Form(""),
    bg_music:         Optional[UploadFile] = File(None),
    logo:             Optional[UploadFile] = File(None),
    thumbnail:        Optional[UploadFile] = File(None),
):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua san sang render"}, status_code=400)

    # Full render must have a translation that covers the full source. A
    # preview JSON (for example, only the first 3 minutes of a 60-minute
    # video) used to be accepted here, producing an hour-long file with
    # almost no translated speech or subtitles while still saying "done".
    cues = job.get("vi_cues") or []
    video_duration = float(job.get("duration_secs") or 0)
    if video_duration <= 0 and job.get("video_path"):
        video_duration = get_video_duration(job["video_path"])
    translated_cues = [c for c in cues if str(c.get("text", "")).strip()]
    translated_end = max((float(c.get("end_sec") or 0) for c in translated_cues), default=0.0)
    coverage = min(translated_end / video_duration, 1.0) if video_duration > 0 else 0.0
    nonempty_ratio = len(translated_cues) / len(cues) if cues else 0.0
    if not cues or coverage < 0.90 or nonempty_ratio < 0.80:
        return JSONResponse({
            "error": (
                f"Bản dịch chưa đủ để render toàn bộ: video dài {video_duration/60:.1f} phút, "
                f"phụ đề có nội dung mới tới {translated_end/60:.1f} phút "
                f"({coverage*100:.0f}%, {len(translated_cues)}/{len(cues)} dòng có bản dịch). "
                "Hãy nạp JSON/SRT đầy đủ hoặc chỉ dùng nút Preview."
            )
        }, status_code=400)

    work_dir = Path(job["work_dir"])

    bg_music_path = None
    if bg_music and bg_music.filename:
        suffix = Path(bg_music.filename).suffix or ".mp3"
        p = work_dir / f"bg_music{suffix}"
        p.write_bytes(await bg_music.read())
        bg_music_path = str(p)
    elif bg_music_name.strip():
        # Chọn từ thư viện nhạc có sẵn trong audio/ (không upload)
        candidate = AUDIO_DIR / Path(bg_music_name.strip()).name
        if candidate.exists():
            bg_music_path = str(candidate)

    logo_path = None
    if logo_tab == "img" and logo and logo.filename:
        suffix = Path(logo.filename).suffix or ".png"
        p = work_dir / f"logo{suffix}"
        p.write_bytes(await logo.read())
        logo_path = str(p)

    thumbnail_path = None
    if thumbnail and thumbnail.filename:
        suffix = Path(thumbnail.filename).suffix or ".jpg"
        p = work_dir / f"thumbnail{suffix}"
        p.write_bytes(await thumbnail.read())
        thumbnail_path = str(p)

    background_tasks.add_task(
        _run_render, job_id, voice, bg_volume, tts_volume,
        "1.0", speed_ratio, bg_music_path,
        logo_path, logo_pos, logo_size,
        logo_tab, logo_text.strip(), logo_fontsize, logo_color,
        keep_original, original_volume, strip_background_music,
        encode_preset, thumbnail_path,
    )
    return {"ok": True}


@app.post("/api/rerender/{job_id}")
async def rerender(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    voice:        str   = Form("BV421_vivn_streaming"),
    bg_volume:    float = Form(0.3),
    tts_volume:   float = Form(1.8),
    speed_ratio:  float = Form(1.0),
    logo_pos:     str   = Form("topright"),
    logo_size:    int   = Form(100),
    logo_tab:     str   = Form("none"),
    logo_text:    str   = Form(""),
    logo_fontsize:int   = Form(28),
    logo_color:   str   = Form("#ffffff"),
    bg_music_name: str  = Form(""),
    bg_music:     Optional[UploadFile] = File(None),
    logo:         Optional[UploadFile] = File(None),
):
    return await render(job_id, request, background_tasks, voice=voice,
                        bg_volume=bg_volume, tts_volume=tts_volume, speed_ratio=speed_ratio,
                        logo_pos=logo_pos, logo_size=logo_size, logo_tab=logo_tab,
                        logo_text=logo_text, logo_fontsize=logo_fontsize, logo_color=logo_color,
                        bg_music_name=bg_music_name, bg_music=bg_music, logo=logo)


PREVIEW_SECS = 180  # 3 phút

@app.post("/api/preview/{job_id}")
async def preview(
    job_id:           str,
    request:          Request,
    background_tasks: BackgroundTasks,
    voice:            str   = Form("BV421_vivn_streaming"),
    bg_volume:        float = Form(0.3),
    tts_volume:       float = Form(1.8),
    speed_ratio:      float = Form(1.0),
    logo_text:        str   = Form(""),
    keep_original:    bool  = Form(False),
    original_volume:  float = Form(0.3),
    strip_background_music: bool = Form(False),
    preview_secs:     int   = Form(180),
    bg_music_name:    str   = Form(""),
    thumbnail:        Optional[UploadFile] = File(None),
    bg_music:         Optional[UploadFile] = File(None),
):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua san sang"}, status_code=400)

    work_dir   = Path(job["work_dir"])
    video_path = Path(job["video_path"])
    vi_cues    = job["vi_cues"]

    # Cắt cues trong thời lượng preview
    preview_cues = [c for c in vi_cues if c.get("start_sec", 0) < preview_secs]
    if not preview_cues:
        return JSONResponse({"error": "Không có phụ đề trong 3 phút đầu"}, status_code=400)

    # Tạo subdir riêng cho preview
    prev_dir = work_dir / "preview"
    prev_dir.mkdir(exist_ok=True)

    # Cắt video 3p đầu bằng FFmpeg (nhanh, không re-encode)
    prev_video = prev_dir / "video_preview.mp4"
    subprocess.run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-t", str(preview_secs), "-c", "copy", str(prev_video)
    ], capture_output=True)

    # Tạo preview job tạm trong jobs dict (dùng chung job_id + suffix)
    prev_job_id = f"{job_id}_preview"
    jobs[prev_job_id] = {
        **{k: v for k, v in job.items() if k not in ("log",)},
        "work_dir":   str(prev_dir),
        "video_path": str(prev_video),
        "vi_cues":    preview_cues,
        "status":     "translated",
        "is_preview": True,
    }

    thumbnail_path = None
    if thumbnail and thumbnail.filename:
        suffix = Path(thumbnail.filename).suffix or ".jpg"
        p = prev_dir / f"thumbnail{suffix}"
        p.write_bytes(await thumbnail.read())
        thumbnail_path = str(p)

    bg_music_path = None
    if bg_music and bg_music.filename:
        suffix = Path(bg_music.filename).suffix or ".mp3"
        p = prev_dir / f"bg_music{suffix}"
        p.write_bytes(await bg_music.read())
        bg_music_path = str(p)
    elif bg_music_name.strip():
        candidate = AUDIO_DIR / Path(bg_music_name.strip()).name
        if candidate.exists():
            bg_music_path = str(candidate)

    background_tasks.add_task(
        _run_render, prev_job_id, voice, bg_volume, tts_volume,
        "1.0", speed_ratio, bg_music_path,
        None, "topright", 100,
        "text", logo_text.strip(), 28, "#ffffff",
        keep_original, original_volume, strip_background_music,
        "ultrafast", thumbnail_path,
    )
    return {"ok": True, "preview_job_id": prev_job_id}


def _call_beeknoee_titles(beeknoee_key: str, transcript_head: str) -> dict:
    """Gọi Beeknoee (model glm-4.5-flash, giá rẻ) sinh title TikTok + title/description
    YouTube từ vài dòng phụ đề đầu. Trả JSON strict để dễ parse."""
    from openai import OpenAI
    from translate_video import BEEKNOEE_BASE_URL

    client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
    system = (
        "Bạn là chuyên gia đặt tiêu đề video ngắn tiếng Việt cho TikTok và YouTube. "
        "Chỉ trả về JSON hợp lệ, không thêm chữ nào khác, không markdown code fence."
    )
    user = f"""Dựa vào đoạn mở đầu phụ đề video sau (tiếng Việt), hãy tạo BẰNG TIẾNG VIỆT:

1. "tiktok_title": tiêu đề TikTok TIẾNG VIỆT lấy ý từ 4-5 dòng phụ đề đầu (có thể viết lại cho hấp dẫn hơn, giữ đúng nội dung), theo sau là ĐÚNG 5 hashtag cách nhau bởi dấu cách. Bắt buộc phải có #AI #videoAI #science. Nếu trong đoạn phụ đề có tên một sinh vật (con vật, loài...) cụ thể thì thêm 1 hashtag tên sinh vật đó (viết liền không dấu cách, có thể tiếng Anh, ví dụ #bluewhale). Hashtag còn lại tự nghĩ sao cho hợp nội dung. Tổng cộng đúng 5 hashtag.
2. "youtube_title": tiêu đề YouTube TIẾNG VIỆT, KHÔNG có hashtag, PHẢI đúng tối đa 95 ký tự kể cả khoảng trắng (đếm kỹ, không được vượt quá).
3. "youtube_description": mô tả YouTube TIẾNG VIỆT, không giới hạn độ dài, có thể nhiều dòng, kết thúc bằng ĐÚNG 10-12 hashtag liên quan tới nội dung (đa dạng: chủ đề chính, chủ đề phụ, sinh vật/vật thể nhắc tới, thể loại video, #AI #videoAI #science, các từ khoá thịnh hành liên quan), cách nhau bởi dấu cách.

QUAN TRỌNG: tiktok_title và youtube_title và youtube_description PHẢI viết bằng tiếng Việt (chỉ hashtag được phép tiếng Anh nếu cần).

Phụ đề đầu video:
\"\"\"
{transcript_head}
\"\"\"

Trả về đúng format JSON:
{{"tiktok_title": "...", "youtube_title": "...", "youtube_description": "..."}}"""

    resp = client.chat.completions.create(
        model=BEEKNOEE_TITLE_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.7,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)

    yt_title = (data.get("youtube_title") or "").strip()
    if len(yt_title) > 95:
        yt_title = yt_title[:95].rstrip()
    return {
        "tiktok_title":        (data.get("tiktok_title") or "").strip(),
        "youtube_title":       yt_title,
        "youtube_description": (data.get("youtube_description") or "").strip(),
    }


@app.post("/api/generate-titles/{job_id}")
async def generate_titles(job_id: str, request: Request):
    """Sinh title TikTok + title/description YouTube từ phụ đề đã dịch, dùng
    Beeknoee model glm-4.5-flash (rẻ). Chạy sau khi đã có vi_cues (dịch xong)."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err

    vi_cues = job.get("vi_cues") or []
    if not vi_cues:
        return JSONResponse({"error": "Chưa có phụ đề để tạo title"}, status_code=400)

    beeknoee_key = os.environ.get("BEEKNOEE_API_KEY", "")
    if not beeknoee_key:
        return JSONResponse({"error": "Chưa cấu hình BEEKNOEE_API_KEY"}, status_code=400)

    head_lines = [c.get("text", "").strip() for c in vi_cues[:5] if c.get("text", "").strip()]
    transcript_head = "\n".join(head_lines)
    if not transcript_head:
        return JSONResponse({"error": "Phụ đề đầu rỗng"}, status_code=400)

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, _call_beeknoee_titles, beeknoee_key, transcript_head)
    except Exception as e:
        return JSONResponse({"error": f"Lỗi tạo title: {e}"}, status_code=500)

    jobs[job_id]["titles"] = result
    return result


@app.get("/api/download/{job_id}")
async def download(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    out = job.get("output")
    if not out or not Path(out).exists():
        return JSONResponse({"error": "Video chua san sang"}, status_code=404)
    fname = job.get("filename", "video.mp4")
    stem  = Path(fname).stem
    return FileResponse(out, media_type="video/mp4", filename=f"{stem}_vi.mp4")


@app.get("/api/media-token/{job_id}")
async def media_token(job_id: str, request: Request):
    """Issue a short-lived token so the browser can stream/seek the result."""
    from datetime import datetime, timedelta, timezone

    job, err = _get_job(job_id, request.state.user_id)
    if err:
        return err
    if not job.get("output") or not Path(job["output"]).exists():
        return JSONResponse({"error": "Video chua san sang"}, status_code=404)
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "sub": str(request.state.user_id),
        "purpose": "media",
        "job_id": job_id,
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)
    return {"media_token": token}


@app.get("/api/frame/{job_id}")
async def get_frame(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    video_path = job.get("video_path")
    if not video_path or not Path(video_path).exists():
        return JSONResponse({"error": "Chua co video"}, status_code=404)
    work_dir = Path(job["work_dir"])
    frame_path = work_dir / "preview_frame.jpg"
    subprocess.run([
        FFMPEG_BIN, "-y", "-ss", "10", "-i", str(video_path),
        "-vframes", "1", "-q:v", "2", str(frame_path)
    ], capture_output=True)
    if not frame_path.exists():
        return JSONResponse({"error": "Khong lay duoc frame"}, status_code=500)
    return FileResponse(frame_path, media_type="image/jpeg")


@app.get("/api/download-srt/{job_id}")
async def download_srt(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    srt = job.get("srt_path")
    if not srt or not Path(srt).exists():
        return JSONResponse({"error": "Chua co SRT"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(srt, media_type="text/plain", filename=f"{fname}_vi.srt")


# ── JSON cues: nạp / xuất ───────────────────────────────────────────────────

def _parse_cues_json(content: bytes) -> tuple[list[dict], list[dict]]:
    """Parse file JSON bản dịch (format export từ app: [{idx,start,end,start_sec,end_sec,zh,vi}]).
    Trả về (vi_cues, zh_cues). Chấp nhận cả {"cues": [...]} và key "text" thay "vi"."""
    from translate_video import srt_time_to_sec, sec_to_srt_time
    data = json.loads(content.decode("utf-8-sig", errors="replace"))
    if isinstance(data, dict):
        raw = data.get("cues") or data.get("vi_cues") or []
    else:
        raw = data
    if not isinstance(raw, list):
        raise ValueError("JSON phải là mảng các cue")
    vi_cues, zh_cues = [], []
    for i, c in enumerate(raw, 1):
        if not isinstance(c, dict):
            continue
        text = c.get("vi") if c.get("vi") is not None else c.get("text", "")
        start, end = c.get("start"), c.get("end")
        ss, es = c.get("start_sec"), c.get("end_sec")
        if ss is None and start: ss = srt_time_to_sec(str(start))
        if es is None and end:   es = srt_time_to_sec(str(end))
        if ss is None or es is None:
            continue
        ss, es = float(ss), float(es)
        if not start: start = sec_to_srt_time(ss)
        if not end:   end   = sec_to_srt_time(es)
        cue = {"idx": c.get("idx", i), "start": start, "end": end,
               "start_sec": ss, "end_sec": es, "text": str(text or "")}
        if c.get("zh"):
            cue["zh"] = str(c["zh"])
            zh_cues.append({"idx": cue["idx"], "text": cue["zh"]})
        vi_cues.append(cue)
    vi_cues.sort(key=lambda c: c["start_sec"])
    return vi_cues, zh_cues


def _cues_rows(vi_cues: list[dict], zh_cues: list[dict] | None = None) -> list[dict]:
    """Rows cho bảng edit — vị trí (pos) là khóa chính, idx chỉ để hiển thị."""
    zh_map = {c["idx"]: c["text"] for c in (zh_cues or [])}
    return [{"pos": i, "idx": c["idx"], "start": c["start"], "end": c["end"],
             "zh": c.get("zh") or zh_map.get(c["idx"], ""), "vi": c.get("text", "")}
            for i, c in enumerate(vi_cues)]


@app.post("/api/upload-with-json")
async def upload_with_json(
    request:   Request,
    file:      UploadFile = File(...),
    cues_json: UploadFile = File(...),
    voice:     str   = Form("BV421_vivn_streaming"),
    bg_volume: float = Form(0.3),
    tts_volume:float = Form(1.8),
):
    """Upload video + JSON bản dịch, bỏ qua STT + dịch AI."""
    from translate_video import build_srt

    user_id = request.state.user_id
    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    duration = get_video_duration(tmp.name)
    cost     = calc_cost(duration)

    if cost > 0:
        ok = db_deduct(user_id, cost, f"Dich video (JSON): {file.filename} ({round(duration/60,1)} phut)")
        if not ok:
            os.unlink(tmp.name)
            balance = db_get_balance(user_id)
            return JSONResponse(
                {"error": f"So du khong du. Can {cost:,}d, hien co {balance:,}d"},
                status_code=402
            )

    try:
        vi_cues, zh_cues = _parse_cues_json(await cues_json.read())
    except Exception as e:
        if cost > 0: db_refund(user_id, cost, "Hoan tien JSON khong hop le")
        os.unlink(tmp.name)
        return JSONResponse({"error": f"JSON khong hop le: {e}"}, status_code=400)
    if not vi_cues:
        if cost > 0: db_refund(user_id, cost, "Hoan tien JSON rong")
        os.unlink(tmp.name)
        return JSONResponse({"error": "JSON rong hoac sai dinh dang"}, status_code=400)

    job_id     = str(uuid.uuid4())
    work_dir   = Path(tempfile.mkdtemp(prefix=f"tr_{job_id[:8]}_"))
    video_copy = work_dir / ("video" + suffix)
    shutil.copy2(tmp.name, video_copy)
    os.unlink(tmp.name)

    srt_path = work_dir / "captions_vi.srt"
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

    jobs[job_id] = {
        "status":       "translated",
        "progress":     100,
        "message":      f"Da nap JSON ({len(vi_cues)} doan)",
        "filename":     file.filename,
        "work_dir":     str(work_dir),
        "video_path":   str(video_copy),
        "srt_path":     str(srt_path),
        "vi_cues":      vi_cues,
        "zh_cues":      zh_cues,
        "voice":        voice,
        "bg_volume":    bg_volume,
        "tts_volume":   tts_volume,
        "user_id":      user_id,
        "cost":         cost,
        "duration_secs": round(duration, 1),
    }
    return {"job_id": job_id, "rows": _cues_rows(vi_cues, zh_cues),
            "cost": cost, "duration_secs": round(duration, 1)}


@app.post("/api/load-json/{job_id}")
async def load_json_cues(job_id: str, request: Request, file: UploadFile = File(...)):
    """Nạp file JSON bản dịch vào job hiện tại, ghi đè bản dịch."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    try:
        vi_cues, zh_cues = _parse_cues_json(await file.read())
    except Exception as e:
        return JSONResponse({"error": f"JSON không hợp lệ: {e}"}, status_code=400)
    if not vi_cues:
        return JSONResponse({"error": "JSON rỗng hoặc sai định dạng"}, status_code=400)

    job["vi_cues"] = vi_cues
    job["zh_cues"] = zh_cues
    from translate_video import build_srt
    if job.get("srt_path"):
        Path(job["srt_path"]).write_text(build_srt(vi_cues), encoding="utf-8")
    job.pop("chunks", None)  # bản dịch đổi → chunk cũ không còn khớp
    return {"ok": True, "rows": _cues_rows(vi_cues, zh_cues)}


@app.get("/api/export-json/{job_id}")
async def export_json_cues(job_id: str, request: Request):
    """Tải bản dịch hiện tại dạng JSON (format giống app Gradio export)."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    vi_cues = job.get("vi_cues", [])
    if not vi_cues:
        return JSONResponse({"error": "Chua co ban dich"}, status_code=404)
    zh_map = {c["idx"]: c["text"] for c in job.get("zh_cues", [])}
    export = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
               "start_sec": c["start_sec"], "end_sec": c["end_sec"],
               "zh": c.get("zh") or zh_map.get(c["idx"], ""),
               "vi": c.get("text", "")} for c in vi_cues]
    out = Path(job["work_dir"]) / "vi_cues_export.json"
    out.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
    stem = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(out, media_type="application/json", filename=f"{stem}_vi_cues.json")


# ── Chunk render ─────────────────────────────────────────────────────────────

def _keyframe_near(video: str | Path, target: float) -> float:
    """Tìm keyframe gần target nhất (±30s) để cắt -c copy không lệch phụ đề."""
    if target <= 0:
        return 0.0
    r = subprocess.run([
        FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
        "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
        "-of", "csv=p=0",
        "-read_intervals", f"{max(0.0, target - 30)}%{target + 30}",
        str(video),
    ], capture_output=True, text=True)
    times = []
    for tok in r.stdout.split():
        tok = tok.strip().rstrip(",")
        try:
            times.append(float(tok))
        except ValueError:
            pass
    if not times:
        return target
    return min(times, key=lambda t: abs(t - target))


@app.post("/api/chunks/plan/{job_id}")
def chunks_plan(job_id: str, request: Request, body: dict):
    """Chia video + phụ đề thành các chunk N phút (mốc cắt bám theo keyframe)."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if not job.get("vi_cues"):
        return JSONResponse({"error": "Chưa có phụ đề"}, status_code=400)
    if not job.get("video_path") or not Path(job["video_path"]).exists():
        return JSONResponse({"error": "Chưa có video"}, status_code=400)
    if job.get("chunk_rendering"):
        return JSONResponse({"error": "Đang render chunk, không chia lại được"}, status_code=400)

    minutes   = float(body.get("chunk_minutes", 20))
    video_dur = get_video_duration(job["video_path"])
    step      = minutes * 60

    bounds = [0.0]
    t = step
    while t <= video_dur - 30:  # chunk cuối tối thiểu 30s
        kf = _keyframe_near(job["video_path"], t)
        if kf - bounds[-1] >= 30 and video_dur - kf >= 30:
            bounds.append(round(kf, 3))
        t += step
    bounds.append(round(video_dur, 3))

    chunks = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        n = sum(1 for c in job["vi_cues"] if c["start_sec"] < e and c["end_sec"] > s)
        chunks.append({"idx": i, "start_sec": s, "end_sec": e, "n_cues": n,
                       "status": "pending", "output": None, "error": None, "progress": 0})
    job["chunks"] = chunks
    job["chunk_minutes"] = minutes
    return {"chunks": chunks, "video_duration": round(video_dur, 1)}


def _run_chunk_render(job_id: str, indices: list[int], voice: str,
                      bg_volume: float, tts_volume: float, speed_ratio: float,
                      logo_text: str, keep_original: bool, original_volume: float,
                      strip_background_music: bool,
                      encode_preset: str, bg_music_path: str | None):
    """Render tuần tự các chunk: cắt video → TTS → FFmpeg render ngay từng chunk."""
    from translate_video import (
        build_srt, build_tts_track, render_video, sec_to_srt_time, CAPCUT_VOICES_VI,
    )
    job = jobs[job_id]
    if job.get("chunk_rendering"):
        return
    job["chunk_rendering"] = True
    job.setdefault("log", [])

    cap = _LogCapture(job_id)
    cap.__enter__()
    try:
        chunks     = job["chunks"]
        work_dir   = Path(job["work_dir"])
        video_path = Path(job["video_path"])
        chunk_dir  = work_dir / "chunks"
        chunk_dir.mkdir(exist_ok=True)

        capcut_info        = next((v for v in CAPCUT_VOICES_VI if v[1] == voice), None)
        capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", "") if capcut_info else None
        capcut_voice_type  = capcut_info[1] if capcut_info else None
        capcut_resource_id = capcut_info[2] if capcut_info else None

        for ci in indices:
            if ci < 0 or ci >= len(chunks):
                continue
            ch = chunks[ci]
            ch.update(status="running", error=None, progress=0)
            job["message"] = f"Chunk {ci+1}: cắt video..."
            print(f"=== Chunk {ci+1}/{len(chunks)} [{ch['start_sec']:.0f}s–{ch['end_sec']:.0f}s] ===")
            try:
                s, e = ch["start_sec"], ch["end_sec"]
                dur  = e - s

                # 1. Cắt video (-c copy, mốc đã bám keyframe nên không lệch)
                src = chunk_dir / f"chunk_{ci:03d}_src.mp4"
                if not src.exists() or abs(get_video_duration(src) - dur) > 2.0:
                    r = subprocess.run([
                        FFMPEG_BIN, "-y", "-ss", str(s), "-i", str(video_path),
                        "-t", str(dur), "-c", "copy", "-avoid_negative_ts", "make_zero",
                        str(src),
                    ], capture_output=True)
                    if r.returncode != 0:
                        raise RuntimeError("Cắt video lỗi: " + r.stderr.decode(errors="replace")[-300:])
                ch["progress"] = 5

                # 2. Dời phụ đề về mốc 0 của chunk
                cues = []
                for c in job["vi_cues"]:
                    if c["start_sec"] < e and c["end_sec"] > s:
                        ns = max(0.0, c["start_sec"] - s)
                        ne = min(dur, c["end_sec"] - s)
                        if ne - ns < 0.05:
                            continue
                        cues.append({**c, "start_sec": ns, "end_sec": ne,
                                     "start": sec_to_srt_time(ns), "end": sec_to_srt_time(ne)})

                srt_path = chunk_dir / f"chunk_{ci:03d}.srt"
                srt_path.write_text(build_srt(cues) if cues else "", encoding="utf-8")

                cwork = chunk_dir / f"chunk_{ci:03d}_work"
                cwork.mkdir(exist_ok=True)
                out = chunk_dir / f"chunk_{ci:03d}_out.mp4"

                # 3. TTS → 4. render FFmpeg ngay
                orig_audio = None
                if keep_original:
                    if strip_background_music:
                        job["message"] = f"Chunk {ci+1}: tách giọng bỏ nhạc..."
                        from translate_video import extract_vocals_for_mix
                        orig_audio = extract_vocals_for_mix(src, cwork)
                    else:
                        from translate_video import extract_audio_for_stt
                        orig_audio = cwork / "original_audio.wav"
                        if not orig_audio.exists():
                            extract_audio_for_stt(src, orig_audio)

                if cues:
                    def _tp(done, total, _ch=ch, _ci=ci):
                        _ch["progress"] = 5 + int(70 * done / total) if total else 5
                        job["message"] = f"Chunk {_ci+1}: TTS {done}/{total} đoạn"

                    tts_track = asyncio.run(build_tts_track(
                        cues, cwork, dur,
                        capcut_device_id=capcut_device_id,
                        capcut_voice_type=capcut_voice_type,
                        capcut_resource_id=capcut_resource_id,
                        capcut_delay=1.5, capcut_rate="1.0",
                        speed_ratio=speed_ratio,
                        progress_cb=_tp,
                        vpn_rotate_cb=_vpn_rotate_cb,
                    ))
                    ch["progress"] = 80
                    job["message"] = f"Chunk {ci+1}: render FFmpeg..."
                    render_video(src, tts_track, srt_path, out,
                                 bg_music=Path(bg_music_path) if bg_music_path else None,
                                 bg_volume=bg_volume, tts_volume=tts_volume,
                                 original_audio=orig_audio, original_volume=original_volume,
                                 watermark=logo_text, encode_preset=encode_preset)
                else:
                    # Không có phụ đề trong chunk → re-encode cùng codec để concat được
                    job["message"] = f"Chunk {ci+1}: không có phụ đề, encode lại..."
                    r = subprocess.run([
                        FFMPEG_BIN, "-y", "-i", str(src),
                        "-vf", build_light_video_enhance_filter(""),
                        "-c:v", "libx264", "-preset", encode_preset, "-crf", "21",
                        "-profile:v", "high", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", str(out),
                    ], capture_output=True)
                    if r.returncode != 0:
                        raise RuntimeError("Encode lỗi: " + r.stderr.decode(errors="replace")[-300:])

                ch.update(status="done", output=str(out), progress=100)
                print(f"=== Chunk {ci+1} XONG → {out.name} ===")
            except Exception as ex:
                import traceback; traceback.print_exc()
                ch.update(status="error", error=str(ex), progress=0)

        done = sum(1 for c in chunks if c["status"] == "done")
        job["message"] = f"Chunk: {done}/{len(chunks)} hoàn thành"
    finally:
        job["chunk_rendering"] = False
        cap.__exit__(None, None, None)


@app.post("/api/chunks/render/{job_id}")
async def chunks_render(
    job_id:           str,
    request:          Request,
    background_tasks: BackgroundTasks,
    chunk_idx:        int   = Form(-1),   # -1 = tất cả chunk chưa xong
    chunk_list:       str   = Form(""),   # "3,5,6,7" (1-based) — ưu tiên hơn chunk_idx
    voice:            str   = Form("BV421_vivn_streaming"),
    bg_volume:        float = Form(0.3),
    tts_volume:       float = Form(1.8),
    speed_ratio:      float = Form(1.0),
    logo_text:        str   = Form(""),
    keep_original:    bool  = Form(False),
    original_volume:  float = Form(0.3),
    strip_background_music: bool = Form(False),
    encode_preset:    str   = Form("ultrafast"),
    bg_music:         Optional[UploadFile] = File(None),
):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    chunks = job.get("chunks")
    if not chunks:
        return JSONResponse({"error": "Chưa chia chunk"}, status_code=400)
    if job.get("chunk_rendering"):
        return JSONResponse({"error": "Đang render chunk khác, chờ xong đã"}, status_code=409)

    if chunk_list.strip():
        # Render theo danh sách chọn, vd "3,5,6,7" (số thứ tự 1-based)
        import re as _re
        toks = [t for t in _re.split(r"[,;\s]+", chunk_list.strip()) if t]
        indices = []
        for t in toks:
            try:
                n = int(t)
            except ValueError:
                return JSONResponse({"error": f"'{t}' không phải số"}, status_code=400)
            i = n - 1
            if i < 0 or i >= len(chunks):
                return JSONResponse({"error": f"Chunk {n} không tồn tại (có 1–{len(chunks)})"},
                                    status_code=400)
            if i not in indices:
                indices.append(i)
        if not indices:
            return JSONResponse({"error": "Danh sách chunk rỗng"}, status_code=400)
        for i in indices:
            chunks[i].update(status="pending", output=None, error=None, progress=0)
    elif chunk_idx >= 0:
        if chunk_idx >= len(chunks):
            return JSONResponse({"error": "Chunk không tồn tại"}, status_code=404)
        # Làm lại chunk: reset trạng thái
        chunks[chunk_idx].update(status="pending", output=None, error=None, progress=0)
        indices = [chunk_idx]
    else:
        indices = [c["idx"] for c in chunks if c["status"] != "done"]
        if not indices:
            return JSONResponse({"error": "Tất cả chunk đã xong"}, status_code=400)

    bg_music_path = job.get("chunk_bg_music")
    if bg_music and bg_music.filename:
        suffix = Path(bg_music.filename).suffix or ".mp3"
        p = Path(job["work_dir"]) / f"chunk_bg_music{suffix}"
        p.write_bytes(await bg_music.read())
        bg_music_path = str(p)
        job["chunk_bg_music"] = bg_music_path

    background_tasks.add_task(
        _run_chunk_render, job_id, indices, voice,
        bg_volume, tts_volume, speed_ratio,
        logo_text.strip(), keep_original, original_volume,
        strip_background_music,
        encode_preset, bg_music_path,
    )
    return {"ok": True, "indices": indices}


@app.get("/api/chunks/status/{job_id}")
async def chunks_status(job_id: str, request: Request, log_from: int = 0):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    all_log = job.get("log", [])
    return {
        "chunks":    job.get("chunks", []),
        "rendering": bool(job.get("chunk_rendering")),
        "message":   job.get("message", ""),
        "log":       all_log[log_from:],
        "log_total": len(all_log),
    }


@app.get("/api/chunks/video/{job_id}/{chunk_idx}")
async def chunks_video(job_id: str, chunk_idx: int, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    chunks = job.get("chunks", [])
    if chunk_idx < 0 or chunk_idx >= len(chunks):
        return JSONResponse({"error": "Chunk không tồn tại"}, status_code=404)
    out = chunks[chunk_idx].get("output")
    if not out or not Path(out).exists():
        return JSONResponse({"error": "Chunk chưa render xong"}, status_code=404)
    return FileResponse(out, media_type="video/mp4", filename=f"chunk_{chunk_idx+1:02d}.mp4")


@app.post("/api/chunks/upload/{job_id}/{chunk_idx}")
async def chunks_upload(job_id: str, chunk_idx: int, request: Request,
                        file: UploadFile = File(...)):
    """Upload video chunk đã render (tải về từ trước) vào đúng vị trí chunk_idx."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    chunks = job.get("chunks", [])
    if chunk_idx < 0 or chunk_idx >= len(chunks):
        return JSONResponse({"error": "Chunk không tồn tại"}, status_code=404)
    ch = chunks[chunk_idx]
    if ch.get("status") == "running":
        return JSONResponse({"error": "Chunk đang render, không nạp đè được"}, status_code=409)

    chunk_dir = Path(job["work_dir"]) / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    out = chunk_dir / f"chunk_{chunk_idx:03d}_out.mp4"
    out.write_bytes(await file.read())

    # Kiểm tra duration có khớp khung chunk không (chỉ cảnh báo, vẫn chấp nhận)
    dur_expect = ch["end_sec"] - ch["start_sec"]
    dur_actual = get_video_duration(out)
    warning = None
    if dur_actual <= 0:
        out.unlink(missing_ok=True)
        return JSONResponse({"error": "File không phải video hợp lệ"}, status_code=400)
    if abs(dur_actual - dur_expect) > 5.0:
        warning = (f"Thời lượng lệch: chunk cần {dur_expect:.0f}s, "
                   f"file nạp {dur_actual:.0f}s — kiểm tra đúng vị trí chưa")

    ch.update(status="done", output=str(out), error=None, progress=100)
    return {"ok": True, "chunk": ch, "warning": warning}


@app.get("/api/chunks/export/{job_id}")
async def chunks_export(job_id: str, request: Request):
    """Xuất JSON trạng thái chunk — để nạp lại nếu server restart giữa chừng."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    chunks = job.get("chunks")
    if not chunks:
        return JSONResponse({"error": "Chưa chia chunk"}, status_code=400)
    state = {
        "type":          "chunk_state",
        "filename":      job.get("filename", ""),
        "chunk_minutes": job.get("chunk_minutes", 20),
        "chunks": [{k: c.get(k) for k in
                    ("idx", "start_sec", "end_sec", "n_cues", "status", "output", "error")}
                   for c in chunks],
    }
    out = Path(job["work_dir"]) / "chunk_state.json"
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    stem = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(out, media_type="application/json", filename=f"{stem}_chunks.json")


@app.post("/api/chunks/import/{job_id}")
async def chunks_import(job_id: str, request: Request, file: UploadFile = File(...)):
    """Nạp lại JSON trạng thái chunk. Chunk 'done' có file còn trên đĩa thì giữ, mất file thì reset."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("chunk_rendering"):
        return JSONResponse({"error": "Đang render, không nạp được"}, status_code=409)
    try:
        state = json.loads((await file.read()).decode("utf-8-sig"))
        raw = state["chunks"]
        assert isinstance(raw, list) and raw
    except Exception as e:
        return JSONResponse({"error": f"JSON trạng thái không hợp lệ: {e}"}, status_code=400)

    chunks, restored, reset = [], 0, 0
    for i, c in enumerate(raw):
        out = c.get("output")
        ok  = c.get("status") == "done" and out and Path(out).exists()
        if c.get("status") == "done" and not ok:
            reset += 1
        if ok:
            restored += 1
        chunks.append({
            "idx": i,
            "start_sec": float(c["start_sec"]), "end_sec": float(c["end_sec"]),
            "n_cues": c.get("n_cues", 0),
            "status": "done" if ok else "pending",
            "output": out if ok else None,
            "error": None, "progress": 100 if ok else 0,
        })
    job["chunks"] = chunks
    job["chunk_minutes"] = state.get("chunk_minutes", 20)
    return {"ok": True, "chunks": chunks, "restored": restored, "reset": reset}


def _chunk_ok(c: dict) -> bool:
    return c["status"] == "done" and c.get("output") and Path(c["output"]).exists()


@app.post("/api/chunks/merge/{job_id}")
def chunks_merge(job_id: str, request: Request):
    """Ghép các chunk ĐÃ XONG LIÊN TIẾP từ đầu (không cần đợi đủ hết).
    Dừng lại ở chunk đầu tiên chưa xong, không bỏ qua khoảng trống ở giữa."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    chunks = job.get("chunks", [])
    if not chunks:
        return JSONResponse({"error": "Chưa chia chunk"}, status_code=400)
    if not _chunk_ok(chunks[0]):
        return JSONResponse({"error": "Chunk 1 chưa xong, chưa ghép được"}, status_code=400)

    ready = []
    for c in chunks:
        if not _chunk_ok(c):
            break
        ready.append(c)

    work_dir  = Path(job["work_dir"])
    list_file = work_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{c['output']}'" for c in ready), encoding="utf-8")
    merged = work_dir / "merged_final.mp4"
    r = subprocess.run([
        FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(merged),
    ], capture_output=True)
    if r.returncode != 0:
        return JSONResponse(
            {"error": "Ghép lỗi: " + r.stderr.decode(errors="replace")[-300:]}, status_code=500)

    partial = len(ready) < len(chunks)
    if not partial:
        job.update(status="done", progress=100, output=str(merged),
                   message=f"Đã ghép đủ {len(chunks)} chunk")
    else:
        # Chưa đủ toàn bộ — không đánh dấu job "done", chỉ lưu output tạm để tải về
        job["output"] = str(merged)
        job["message"] = f"Đã ghép {len(ready)}/{len(chunks)} chunk đầu (còn thiếu phần sau)"
    return {
        "ok": True, "output": str(merged),
        "merged_count": len(ready), "total_count": len(chunks),
        "partial": partial,
    }


@app.post("/api/load-srt/{job_id}")
async def load_srt(job_id: str, request: Request, file: UploadFile = File(...)):
    """Nạp file SRT bên ngoài vào job, ghi đè bản dịch hiện tại."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    from translate_video import parse_srt
    content = (await file.read()).decode("utf-8", errors="replace")

    try:
        new_cues = parse_srt(content)
    except Exception as e:
        return JSONResponse({"error": f"SRT không hợp lệ: {e}"}, status_code=400)

    if not new_cues:
        return JSONResponse({"error": "SRT rỗng hoặc sai định dạng"}, status_code=400)

    job["vi_cues"] = new_cues
    srt_path = Path(job["srt_path"])
    srt_path.write_text(content, encoding="utf-8")

    zh_cues = job.get("zh_cues", [])
    zh_map  = {c["idx"]: c["text"] for c in zh_cues}
    rows    = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
                "zh": zh_map.get(c["idx"], ""), "vi": c.get("text", "")}
               for c in new_cues]
    return {"ok": True, "rows": rows}


# ── VPN Gate: xoay IP khi CapCut chặn ───────────────────────────────────────
try:
    from vpn.vpn_manager import vpn as _vpn
except Exception:
    _vpn = None


@app.get("/api/vpn/status")
async def vpn_status(request: Request):
    if _vpn is None:
        return {"available": False}
    st = _vpn.status()
    st["available"] = True
    st["current_ip"] = _vpn.current_ip()
    return st


@app.post("/api/vpn/connect")
def vpn_connect(request: Request):
    if _vpn is None:
        return JSONResponse({"error": "vpn_manager chưa sẵn sàng"}, status_code=500)
    try:
        _vpn.ensure_list()
        st = _vpn.rotate()
        _vpn_auto_rotate["on"] = True   # bật tự xoay IP khi CapCut chặn
        st["current_ip"] = _vpn.current_ip()
        st["auto_rotate"] = True
        return {"ok": True, **st}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/vpn/rotate")
def vpn_rotate(request: Request):
    if _vpn is None:
        return JSONResponse({"error": "vpn_manager chưa sẵn sàng"}, status_code=500)
    try:
        _vpn.ensure_list()
        st = _vpn.rotate()
        st["current_ip"] = _vpn.current_ip()
        return {"ok": True, **st}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/vpn/disconnect")
def vpn_disconnect(request: Request):
    if _vpn is None:
        return JSONResponse({"error": "vpn_manager chưa sẵn sàng"}, status_code=500)
    _vpn_auto_rotate["on"] = False
    _vpn.disconnect()
    return {"ok": True, "current_ip": _vpn.current_ip()}


# ── Clean: dừng mọi tiến trình + khởi động lại server ───────────────────────

@app.post("/api/clean")
async def clean_restart(request: Request):
    """Dừng hết CapCut/FFmpeg/dịch đang chạy và khởi động lại server.
    Mọi job trong RAM sẽ mất — cần nạp lại video + JSON sau đó."""
    import time as _time

    def _restart():
        _time.sleep(0.5)  # để response kịp trả về
        # Kill mọi tiến trình con (ffmpeg, ffprobe...) của server này
        subprocess.run(["pkill", "-9", "-P", str(os.getpid())], capture_output=True)
        _time.sleep(0.3)
        # Không tắt VPN ở đây — user có thể muốn giữ VPN qua lần restart.
        # (Muốn tắt VPN thì dùng nút "Tắt VPN" riêng.)
        # Thay thế process hiện tại bằng server mới (threads CapCut/render chết theo)
        os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).resolve())])

    threading.Thread(target=_restart, daemon=True).start()
    return {"ok": True, "message": "Đang dừng mọi tiến trình và khởi động lại..."}


@app.post("/api/clean-old-jobs")
async def clean_old_jobs(request: Request):
    """Xóa thư mục temp của các job KHÔNG còn tồn tại trong RAM (job cũ, mồ côi
    sau khi server từng restart). Không đụng job đang chạy hiện tại."""
    # job_id đầy đủ -> work_dir hiện có trong RAM, dùng để tính prefix cần giữ
    alive_prefixes = set()
    for jid, job in jobs.items():
        wd = job.get("work_dir")
        if wd:
            alive_prefixes.add(Path(wd).name)

    tmp_root = Path(tempfile.gettempdir())
    freed_bytes = 0
    removed = []
    skipped_running = []

    for d in tmp_root.glob("tr_*"):
        if not d.is_dir():
            continue
        if d.name in alive_prefixes:
            skipped_running.append(d.name)
            continue
        try:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except Exception:
            size = 0
        try:
            shutil.rmtree(d, ignore_errors=True)
            freed_bytes += size
            removed.append(d.name)
        except Exception:
            pass

    return {
        "ok": True,
        "removed_count": len(removed),
        "freed_mb": round(freed_bytes / 1024 / 1024, 1),
        "skipped_running": skipped_running,
    }


# ── Proxy /auth/* → auth_server :8006 ───────────────────────────────────────
AUTH_ORIGIN = "http://127.0.0.1:8006"

@app.api_route("/auth/{path:path}", methods=["GET","POST","PUT","DELETE","OPTIONS"])
async def auth_proxy(path: str, request: Request):
    url = f"{AUTH_ORIGIN}/auth/{path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}
    body = await request.body()
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method, url=url,
            headers=headers, content=body,
            params=dict(request.query_params),
        )
    return JSONResponse(status_code=resp.status_code,
                        content=resp.json() if resp.content else {},
                        headers=dict(resp.headers))


# Serve static — phải đặt cuối
static_dir = Path(__file__).parent / "static_translate"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
