"""Server nén video riêng cho dự án threads/ — kéo-thả video vào compress_tool.html,
server nén bằng ffmpeg (libx264 + CRF) rồi trả link tải file đã nén.

Tách riêng khỏi media_server.py (không dùng chung MEDIA_DIR — file nén ở đây
thường là video đã export xong, nặng, không cần lẫn với cache upload dropzone).

Chạy: python3 threads/compress_server.py (cổng 8012 — 8007=tts, 8010=media,
8011=export đã dùng, 8012 trống).
"""
import re
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

FFMPEG_BIN = "ffmpeg"
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "compress_uploads"
OUTPUT_DIR = BASE_DIR / "compress_output"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Threads Compress Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: dict[str, dict] = {}


def _safe_filename(name: str) -> str:
    name = Path(name).name
    stem = Path(name).stem
    suffix = Path(name).suffix
    stem = re.sub(r"[^\w\-]", "_", stem)[:80] or "file"
    return f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"


@app.post("/upload")
async def upload(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, "Thiếu tên file")
    out_name = _safe_filename(file.filename)
    out_path = UPLOAD_DIR / out_name
    content = await file.read()
    out_path.write_bytes(content)
    return {"filename": out_name, "originalName": file.filename, "size": len(content)}


class CompressRequest(BaseModel):
    filename: str
    crf: int = 28
    preset: str = "medium"


def _run_compress_job(job_id: str, in_path: Path, crf: int, preset: str):
    try:
        out_name = f"{in_path.stem}_compressed.mp4"
        out_path = OUTPUT_DIR / out_name
        cmd = [
            FFMPEG_BIN, "-y", "-i", str(in_path),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-progress", "pipe:1",
            str(out_path),
        ]
        jobs[job_id].update(status="encoding", message="Đang nén...")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            m = re.search(r"out_time_ms=(\d+)", line)
            if m:
                done_s = int(m.group(1)) / 1_000_000
                jobs[job_id].update(message=f"Đang nén... {done_s:.1f}s")
        stderr_out = proc.stderr.read()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg lỗi (mã {proc.returncode}): {stderr_out[-1500:]}")

        before = in_path.stat().st_size
        after = out_path.stat().st_size
        jobs[job_id].update(
            status="done", progress=100, message="Hoàn tất!",
            downloadUrl=f"/download/{out_name}", outputFilename=out_name,
            sizeBefore=before, sizeAfter=after,
        )
    except Exception as e:
        jobs[job_id].update(status="error", message=str(e))


@app.post("/compress")
def compress(req: CompressRequest):
    in_path = UPLOAD_DIR / Path(req.filename).name
    if not in_path.exists():
        raise HTTPException(404, f"Không tìm thấy file: {req.filename} — cần upload qua /upload trước")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "queued", "progress": 0, "message": "Đang chờ..."}
    threading.Thread(target=_run_compress_job, args=(job_id, in_path, req.crf, req.preset), daemon=True).start()
    return {"job_id": job_id}


@app.get("/compress-status/{job_id}")
def compress_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    return job


@app.get("/download/{filename}")
def download(filename: str):
    path = OUTPUT_DIR / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(path, filename=filename)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012)
