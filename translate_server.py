#!/usr/bin/env python3
"""
translate_server.py — FastAPI port 8005
Web UI cho tab Dịch Video (thay thế Gradio)
"""

import sys, io, os
os.environ.setdefault("PYTHONUTF8", "1")
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(_stream, "buffer"):
        setattr(sys, _s, io.TextIOWrapper(
            _stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
        ))

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Khi frozen (PyInstaller), load .env cạnh exe; khi dev, load từ thư mục script
_env_base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
load_dotenv(_env_base / ".env")

RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
FFMPEG_BIN      = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN     = str(Path(FFMPEG_BIN).parent / "ffprobe")

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

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def ngrok_skip_warning(request: Request, call_next):
    response = await call_next(request)
    response.headers["ngrok-skip-browser-warning"] = "1"
    return response

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

def _job_log(job_id: str, msg: str):
    if "logs" not in jobs[job_id]:
        jobs[job_id]["logs"] = []
    jobs[job_id]["logs"].append(msg)
    if len(jobs[job_id]["logs"]) > 200:
        jobs[job_id]["logs"] = jobs[job_id]["logs"][-200:]


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
        build_srt, build_tts_track, render_video, get_audio_duration,
        CAPCUT_DEVICE_ID, CAPCUT_VOICES_VI,
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
        jobs[job_id]["logs"] = []
        capcut_info        = next((v for v in CAPCUT_VOICES_VI if v[1] == voice), None)
        capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", CAPCUT_DEVICE_ID) if capcut_info else None
        capcut_voice_type  = capcut_info[1] if capcut_info else None
        capcut_resource_id = capcut_info[2] if capcut_info else None

        def _tts_progress(done: int, total: int):
            pct = 0.1 + 0.7 * (done / total) if total else 0.1
            _prog(job_id, pct, f"TTS {done}/{total} đoạn...")

        def _tts_log(msg: str):
            _job_log(job_id, msg)

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
            log_cb=_tts_log,
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
        return


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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
    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    try:
        duration = get_video_duration(tmp.name)
        job_id = str(uuid.uuid4())
        req    = TranslateRequest(job_id=job_id, voice=voice,
                                  bg_volume=bg_volume, tts_volume=tts_volume,
                                  groq_key=groq_key)
        jobs[job_id] = {
            "status": "pending", "progress": 0, "message": "Dang cho...",
            "filename": file.filename,
            "duration_secs": round(duration, 1),
        }
        background_tasks.add_task(_run_translate_pipeline, job_id, tmp.name, req)
        return {"job_id": job_id, "duration_secs": round(duration, 1)}
    except Exception as e:
        import traceback; traceback.print_exc()
        try: os.unlink(tmp.name)
        except Exception: pass
        return JSONResponse({"error": f"Loi server: {e}"}, status_code=500)


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

    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    duration = get_video_duration(tmp.name)

    srt_content = (await srt.read()).decode("utf-8", errors="replace")
    try:
        vi_cues = parse_srt(srt_content)
    except Exception as e:
        os.unlink(tmp.name)
        return JSONResponse({"error": f"SRT khong hop le: {e}"}, status_code=400)
    if not vi_cues:
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
        "duration_secs": round(duration, 1),
    }

    rows = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
             "zh": "", "vi": c.get("text", "")} for c in vi_cues]
    return {"job_id": job_id, "rows": rows, "duration_secs": round(duration, 1)}


def _get_job(job_id: str):
    """Lấy job local trong tiến trình hiện tại."""
    job = jobs.get(job_id)
    if not job:
        return None, JSONResponse({"error": "Khong tim thay"}, status_code=404)
    return job, None


@app.get("/api/status/{job_id}")
async def status(job_id: str, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    return {k: v for k, v in job.items() if k not in ("vi_cues", "zh_cues")}


@app.get("/api/logs/{job_id}")
async def get_logs(job_id: str, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    return JSONResponse({"logs": job.get("logs", [])})


@app.get("/api/cues/{job_id}")
async def get_cues(job_id: str, request: Request):
    """Trả về bảng phụ đề để hiển thị/edit."""
    job, err = _get_job(job_id)
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
    job, err = _get_job(job_id)
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
    job, err = _get_job(job_id)
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
    job, err = _get_job(job_id)
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
    job, err = _get_job(job_id)
    if err: return err
    srt = job.get("srt_path")
    if not srt or not Path(srt).exists():
        return JSONResponse({"error": "Chua co SRT"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(srt, media_type="text/plain", filename=f"{fname}_vi.srt")


@app.post("/api/load-srt/{job_id}")
async def load_srt(job_id: str, request: Request, file: UploadFile = File(...)):
    """Nạp file SRT bên ngoài vào job, ghi đè bản dịch hiện tại."""
    job, err = _get_job(job_id)
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
static_dir = RUNTIME_DIR / "static_translate"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8005)
