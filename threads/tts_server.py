"""TTS server riêng cho dự án threads/ (short video Threads).

Đây LÀ một service tách biệt, KHÔNG phải một phần của content_video.py /
translate_video.py — chỉ tái sử dụng phần hạ tầng gọi CapCut TTS đã được kiểm
chứng ổn định ở gốc repo (capcut_tts_batch, xoay device_id khi bị block) thay
vì viết lại từ đầu. Xem threads/CONTEXT.md mục "CHƯA làm" để biết lý do và
ràng buộc (không đụng vào 2 file kia, chỉ import hàm dùng chung).

Chạy: python3 threads/tts_server.py  (mặc định cổng 8007, các server khác
trong repo dùng 8004/8005/8006 nên tránh trùng).
"""
import os
import random
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent  # gốc repo, nơi có translate_video.py
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))

import translate_video as tv  # noqa: E402  (tái dùng capcut_tts_batch + danh sách giọng VN)

AUDIO_DIR = Path(__file__).resolve().parent / "tts_audio"
AUDIO_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Threads TTS Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TTSRequest(BaseModel):
    text: str
    voice_type: str | None = None   # vd "BV074_streaming" — bỏ trống dùng mặc định
    resource_id: str | None = None
    rate: str = "1.0"
    # Hiệu ứng "vang" (mục nhiều đoạn TTS trong index.html) — người dùng chọn
    # sau khi nghe thử 3 mẫu (echo nhẹ/đậm, reverb phòng), chốt dùng đúng
    # công thức aecho=0.8:0.7:60:0.4 (1 tiếng dội mờ dần ngay sau, kiểu nhấn
    # nhá gây cười — KHÔNG phải reverb liên tục). Áp dụng cho TOÀN BỘ 1 đoạn
    # TTS (không tách riêng từng từ — CapCut không trả timestamp từng từ để
    # cắt chính xác).
    echo: bool = False


def _probe_duration(path: Path) -> float:
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip() or "0")


def _apply_echo(path: Path) -> None:
    """Ghi đè file mp3 tại chỗ với hiệu ứng 'vang' (aecho) — công thức đã
    chốt sau khi người dùng nghe thử 3 mẫu (echo nhẹ/đậm, reverb phòng),
    chọn "echo nhẹ": 1 tiếng dội mờ dần ngay sau giọng gốc, dùng để nhấn nhá
    1 câu thoại cho hài/gây cười, không phải hiệu ứng vọng liên tục kiểu
    phòng lớn. in_gain=0.8, out_gain=0.7 (tiếng dội nhỏ hơn gốc, không đè
    lấn), delay=60ms (đủ tách bạch, không dính liền thành tiếng đôi), decay=0.4."""
    import subprocess
    tmp_out = path.with_suffix(".echo.mp3")
    # BUG đã gặp lúc verify: không chỉ định -b:a, FFmpeg tự chọn bitrate mp3
    # mặc định rất thấp (~33kbps thay vì 160kbps gốc CapCut trả về) — nghe
    # rè/mất chất lượng rõ rệt dù hiệu ứng echo đúng. Ép lại đúng 160k để
    # khớp chất lượng gốc.
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-af", "aecho=0.8:0.7:60:0.4", "-b:a", "160k", str(tmp_out)],
        capture_output=True, check=True,
    )
    tmp_out.replace(path)


@app.get("/voices")
def list_voices():
    return [
        {"label": label, "voice_type": voice_type, "resource_id": resource_id}
        for label, voice_type, resource_id in tv.CAPCUT_VOICES_VI
    ]


@app.post("/tts")
def generate_tts(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text rỗng")

    voice_type = req.voice_type or tv.CAPCUT_VOICES_VI[1][1]
    resource_id = req.resource_id or tv.CAPCUT_VOICES_VI[1][2]
    device_id = os.environ.get("CAPCUT_DEVICE_ID")
    if not device_id:
        raise HTTPException(500, "Thiếu CAPCUT_DEVICE_ID trong .env")

    out_name = f"{uuid.uuid4().hex}.mp3"
    out_path = AUDIO_DIR / out_name

    current_did = device_id
    last_err = None
    for attempt in range(5):
        try:
            tv.capcut_tts_batch([text], voice_type, resource_id, [out_path], current_did, rate=req.rate)
            if req.echo:
                _apply_echo(out_path)
            duration = _probe_duration(out_path)
            if duration <= 0:
                raise RuntimeError("File TTS trả về rỗng/0 giây")
            return {"audio_url": f"/audio/{out_name}", "duration": duration}
        except Exception as e:
            last_err = e
            current_did = str(random.randint(7000000000000000000, 7999999999999999999))
    raise HTTPException(502, f"CapCut TTS lỗi sau 5 lần đổi device_id: {last_err}")


@app.get("/audio/{filename}")
def get_audio(filename: str):
    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy file audio")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
