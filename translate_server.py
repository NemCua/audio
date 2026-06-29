#!/usr/bin/env python3
"""
translate_server.py — FastAPI port 8005
Web UI cho tab Dịch Video (thay thế Gradio)
"""

import asyncio
import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
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

# ── JWT decode helper ────────────────────────────────────────────
def decode_user_id(token: str) -> int | None:
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/static") or path in _PUBLIC_PATHS
            or request.method == "OPTIONS"):
        return await call_next(request)
    if path.startswith("/api/"):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
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
                logo_fontsize: int = 28, logo_color: str = "#ffffff"):
    """Bước 2: TTS CapCut + render video."""
    from translate_video import (
        build_srt, build_tts_track, render_video, get_audio_duration, CAPCUT_VOICES_VI,
    )

    job = jobs[job_id]
    jobs[job_id].update(status="rendering", progress=0, message="Bắt đầu tạo TTS...")

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
        ))

        _prog(job_id, 0.85, "Render video...")
        output_path = work_dir / f"{video_path.stem}_vi.mp4"
        render_video(video_path, tts_track, srt_path, output_path,
                     bg_music=Path(bg_music_path) if bg_music_path else None,
                     bg_volume=bg_volume, tts_volume=tts_volume,
                     original_audio=None, original_volume=0.0,
                     logo=Path(logo_path) if logo_path else None,
                     logo_pos=logo_pos, logo_size=logo_size,
                     watermark=logo_text)

        jobs[job_id].update(status="done", progress=100,
                            message="Hoàn tất!", output=str(output_path))

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))
        _maybe_refund(job_id)


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
async def status(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    return {k: v for k, v in job.items() if k not in ("vi_cues", "zh_cues")}


@app.get("/api/cues/{job_id}")
async def get_cues(job_id: str, request: Request):
    """Trả về bảng phụ đề để hiển thị/edit."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    vi_cues = job.get("vi_cues", [])
    zh_cues = job.get("zh_cues", [])
    zh_map  = {c["idx"]: c["text"] for c in zh_cues}
    rows    = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
                "zh": zh_map.get(c["idx"], ""), "vi": c.get("text", "")}
               for c in vi_cues]
    return {"rows": rows}


@app.post("/api/save-cues/{job_id}")
async def save_cues(job_id: str, body: dict, request: Request):
    """Lưu bản dịch đã edit vào memory."""
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error", "rendering"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    rows    = body.get("rows", [])
    vi_cues = job.get("vi_cues", [])
    by_idx  = {r["idx"]: r["vi"] for r in rows}
    updated = [{**c, "text": by_idx.get(c["idx"], c.get("text", ""))} for c in vi_cues]
    job["vi_cues"] = updated

    from translate_video import build_srt
    srt_path = Path(job["srt_path"])
    srt_path.write_text(build_srt(updated), encoding="utf-8")
    return {"ok": True}


@app.post("/api/render/{job_id}")
async def render(
    job_id:       str,
    request:      Request,
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
    bg_music:     Optional[UploadFile] = File(None),
    logo:         Optional[UploadFile] = File(None),
):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua san sang render"}, status_code=400)

    work_dir = Path(job["work_dir"])

    bg_music_path = None
    if bg_music and bg_music.filename:
        suffix = Path(bg_music.filename).suffix or ".mp3"
        p = work_dir / f"bg_music{suffix}"
        p.write_bytes(await bg_music.read())
        bg_music_path = str(p)

    logo_path = None
    if logo_tab == "img" and logo and logo.filename:
        suffix = Path(logo.filename).suffix or ".png"
        p = work_dir / f"logo{suffix}"
        p.write_bytes(await logo.read())
        logo_path = str(p)

    background_tasks.add_task(
        _run_render, job_id, voice, bg_volume, tts_volume,
        "1.0", speed_ratio, bg_music_path,
        logo_path, logo_pos, logo_size,
        logo_tab, logo_text.strip(), logo_fontsize, logo_color,
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
    bg_music:     Optional[UploadFile] = File(None),
    logo:         Optional[UploadFile] = File(None),
):
    return await render(job_id, request, background_tasks, voice, bg_volume, tts_volume,
                        speed_ratio, logo_pos, logo_size, logo_tab, logo_text,
                        logo_fontsize, logo_color, bg_music, logo)


@app.get("/api/download/{job_id}")
async def download(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    if job.get("status") != "done":
        return JSONResponse({"error": "Video chua san sang"}, status_code=404)
    out = job.get("output")
    if not out or not Path(out).exists():
        return JSONResponse({"error": "File khong ton tai"}, status_code=404)
    fname = job.get("filename", "video.mp4")
    stem  = Path(fname).stem
    return FileResponse(out, media_type="video/mp4", filename=f"{stem}_vi.mp4")


@app.get("/api/download-srt/{job_id}")
async def download_srt(job_id: str, request: Request):
    job, err = _get_job(job_id, request.state.user_id)
    if err: return err
    srt = job.get("srt_path")
    if not srt or not Path(srt).exists():
        return JSONResponse({"error": "Chua co SRT"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(srt, media_type="text/plain", filename=f"{fname}_vi.srt")


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


# Serve static — phải đặt cuối
static_dir = Path(__file__).parent / "static_translate"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
