#!/usr/bin/env python3
"""
content_server.py
FastAPI server riêng cho tab Tạo Video Content — port 8004.
Gradio vẫn chạy port 8003 như cũ.
"""

import os
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lưu trạng thái các job
jobs: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    content: str
    voice: str = ""        # CapCut voice type, để trống = Edge TTS
    rate: float = 1.0


def _run_pipeline(job_id: str, req: GenerateRequest):
    from content_video import run_content_video_pipeline
    from app import CAPCUT_VOICES_VI

    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 0
    jobs[job_id]["message"] = "Đang khởi động..."

    def on_progress(pct, desc=""):
        jobs[job_id]["progress"] = int(pct * 100)
        jobs[job_id]["message"] = desc

    try:
        work_dir = Path(tempfile.mkdtemp(prefix=f"cv_{job_id[:8]}_"))

        groq_key        = os.environ.get("GROQ_API_KEY", "")
        pexels_key      = os.environ.get("PEXELS_API_KEY", "")
        pixabay_key     = os.environ.get("PIXABAY_API_KEY", "")
        beeknoee_key    = os.environ.get("BEEKNOEE_API_KEY", "")
        wan2_server_url = os.environ.get("WAN2_SERVER_URL", "")
        capcut_device_id = os.environ.get("CAPCUT_DEVICE_ID", "")

        capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == req.voice.strip()), None)

        out = run_content_video_pipeline(
            content=req.content.strip(),
            work_dir=work_dir,
            groq_key=groq_key,
            pexels_key=pexels_key,
            pixabay_key=pixabay_key,
            beeknoee_key=beeknoee_key,
            wan2_server_url=wan2_server_url,
            capcut_voice_type=capcut_info[1] if capcut_info else None,
            capcut_resource_id=capcut_info[2] if capcut_info else None,
            capcut_device_id=capcut_device_id if capcut_info else None,
            capcut_rate=str(round(req.rate, 1)),
            progress_cb=on_progress,
        )

        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = "Hoàn tất!"
        jobs[job_id]["output"] = str(out)

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = str(e)


@app.post("/api/preview")
async def preview(req: GenerateRequest):
    """Chỉ chạy AI chia cảnh, trả về danh sách scenes để user xem trước."""
    if not req.content.strip():
        return JSONResponse({"error": "Chưa nhập nội dung"}, status_code=400)
    try:
        from content_video import ai_split_scenes
        groq_key     = os.environ.get("GROQ_API_KEY", "")
        beeknoee_key = os.environ.get("BEEKNOEE_API_KEY", "")
        scenes = ai_split_scenes(req.content.strip(), groq_key, beeknoee_key=beeknoee_key)
        return {"scenes": scenes}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    if not req.content.strip():
        return JSONResponse({"error": "Chưa nhập nội dung"}, status_code=400)

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


# Serve static frontend
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
