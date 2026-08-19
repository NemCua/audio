#!/usr/bin/env python3
"""
translate_server.py â€” FastAPI port 8005
Web UI cho tab Dá»‹ch Video (thay tháº¿ Gradio)
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
import re
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

# Khi frozen (PyInstaller), load .env cáº¡nh exe; khi dev, load tá»« thÆ° má»¥c script
_env_base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
load_dotenv(_env_base / ".env")

RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
FFMPEG_BIN      = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN     = os.getenv("FFPROBE_BIN") or (
    str(Path(FFMPEG_BIN).with_name("ffprobe.exe"))
    if os.name == "nt" and Path(FFMPEG_BIN).suffix.lower() == ".exe"
    else str(Path(FFMPEG_BIN).with_name("ffprobe"))
)

# â”€â”€ Billing helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_video_duration(path: str | Path) -> float:
    """Tráº£ vá» duration (giĂ¢y) dĂ¹ng ffprobe."""
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
voice_preview_cache: dict[str, str] = {}
VOICE_PREVIEW_NAMES = {
    "BV421_vivn_streaming": "Nhỏ Ngọt Ngào",
    "BV074_streaming": "Cô Gái Hoạt Ngôn",
    "vi_female_huong": "Giọng Nữ Phổ Thông",
    "BV074_streaming_dsp": "Giọng Bé",
    "BV562_streaming": "Mai",
    "multi_female_peiqi_uranus_bigtts": "Gái Mới Lớn",
    "multi_female_yangguangnv_uranus_bigtts": "Ban Mai",
    "multi_female_sisi_uranus_bigtts": "Bản Tin Nữ",
    "BV075_streaming": "Thanh Niên Tự Tin",
    "multi_male_felipe_uranus_bigtts": "Giọng Nam Trầm",
    "BV560_streaming": "Alex Đại Đế",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TranslateRequest(BaseModel):
    job_id: str
    voice: str = "BV421_vivn_streaming"
    bg_volume: float = 0.3
    tts_volume: float = 1.0
    original_volume: float = 0.25
    groq_key: str = ""          # user tá»± nháº­p
    beeknoee_key: str = ""


class SubtitleRowsRequest(BaseModel):
    rows: list[dict]
    groq_key: str = ""
    beeknoee_key: str = ""


class FixCueRequest(BaseModel):
    idx: int
    rows: list[dict]
    groq_key: str = ""
    beeknoee_key: str = ""


class SplitLongCuesRequest(BaseModel):
    rows: list[dict]
    max_duration: float = 7.0
    max_chars: int = 95


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


def _rows_from_cues(job: dict, vi_cues: list[dict] | None = None):
    vi_cues = vi_cues if vi_cues is not None else job.get("vi_cues", [])
    zh_cues = job.get("zh_cues", [])
    rows = []
    for i, c in enumerate(vi_cues, 1):
        zh = zh_cues[i - 1].get("text", "") if i - 1 < len(zh_cues) else c.get("zh", "")
        rows.append({
            "idx": i,
            "start": c["start"],
            "end": c["end"],
            "zh": zh,
            "vi": c.get("text", c.get("vi", "")),
        })
    return rows


def _rows_to_cues(rows: list[dict]) -> list[dict]:
    from translate_video import srt_time_to_sec
    cues = []
    for i, r in enumerate(rows, 1):
        start = str(r.get("start", "00:00:00,000"))
        end = str(r.get("end", "00:00:00,000"))
        cues.append({
            "idx": i,
            "start": start,
            "end": end,
            "start_sec": float(r.get("start_sec", srt_time_to_sec(start))),
            "end_sec": float(r.get("end_sec", srt_time_to_sec(end))),
            "text": str(r.get("vi", r.get("text", ""))),
        })
    return cues


def _write_job_subtitles(job: dict):
    from translate_video import build_srt
    work_dir = Path(job["work_dir"])
    vi_cues = [{**c, "idx": i} for i, c in enumerate(job.get("vi_cues", []), 1)]
    job["vi_cues"] = vi_cues
    srt_path = Path(job.get("srt_path") or (work_dir / "captions_vi.srt"))
    json_path = Path(job.get("json_path") or (work_dir / "captions_vi.json"))
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")
    json_path.write_text(json.dumps(_rows_from_cues(job, vi_cues), ensure_ascii=False, indent=2), encoding="utf-8")
    job["srt_path"] = str(srt_path)
    job["json_path"] = str(json_path)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text or ""))


def _repair_vi_text(zh_text: str, vi_text: str, groq_key: str = "", beeknoee_key: str = "") -> str:
    from translate_video import _make_chat_client

    beeknoee_key = (beeknoee_key or os.environ.get("BEEKNOEE_API_KEY", "")).strip()
    groq_key = (groq_key or os.environ.get("GROQ_API_KEY", "")).strip()
    if not beeknoee_key and not groq_key:
        raise RuntimeError("Chua co API key de sua ban dich")

    client, model = _make_chat_client(groq_key, beeknoee_key)
    prompt = (
        "Sua lai ban dich phu de sau sang tieng Viet tu nhien, ngan gon, dung nghia.\n"
        "Chi tra ve 1 cau tieng Viet, khong giai thich, khong giu chu Trung Quoc/Han tu.\n\n"
        f"Cau goc tieng Trung: {zh_text}\n"
        f"Ban dich hien tai: {vi_text}"
    )
    last = ""
    for _ in range(3):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ban la bien dich phu de Trung sang Viet. Dau ra chi la tieng Viet."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.05,
        )
        last = (resp.choices[0].message.content or "").strip()
        last = re.sub(r"^```(?:\w+)?|```$", "", last).strip()
        last = last.strip('"â€œâ€` \n\r\t')
        if last and not _has_cjk(last):
            return last
        prompt += "\n\nBan vua tra ve van con Han tu. Hay tra ve lai chi bang tieng Viet."
    if _has_cjk(last):
        raise RuntimeError("AI sua lai nhung van con tieng Trung")
    return last


def _split_text_natural(text: str, parts: int) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if parts <= 1 or not text:
        return [text]

    tokens = re.findall(r"\S+\s*", text)
    if len(tokens) <= parts:
        return [t.strip() for t in tokens if t.strip()]

    total = sum(len(t.strip()) for t in tokens) or 1
    target = total / parts
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    remaining_parts = parts

    for token in tokens:
        clean = token.strip()
        current.append(token)
        current_len += len(clean)
        remaining_tokens = len(tokens) - sum(len(re.findall(r"\S+", c)) for c in chunks) - len(current)
        can_cut = len(chunks) < parts - 1 and remaining_tokens >= remaining_parts - 1
        natural = bool(re.search(r"[,.!?;:ï¼Œă€‚ï¼ï¼Ÿï¼›ï¼]$", clean))
        if can_cut and current_len >= target * 0.75 and (current_len >= target or natural):
            chunks.append("".join(current).strip())
            current = []
            current_len = 0
            remaining_parts -= 1

    if current:
        chunks.append("".join(current).strip())

    while len(chunks) > parts:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]} {tail}".strip()
    return [c for c in chunks if c]


def _split_long_rows(rows: list[dict], max_duration: float = 7.0, max_chars: int = 95):
    from translate_video import srt_time_to_sec, sec_to_srt_time

    new_rows: list[dict] = []
    split_items: list[dict] = []

    for row in rows:
        start = str(row.get("start", "00:00:00,000"))
        end = str(row.get("end", "00:00:00,000"))
        start_sec = float(row.get("start_sec", srt_time_to_sec(start)))
        end_sec = float(row.get("end_sec", srt_time_to_sec(end)))
        duration = max(0.05, end_sec - start_sec)
        vi = str(row.get("vi", row.get("text", ""))).strip()
        zh = str(row.get("zh", "")).strip()
        target_parts = max(
            1,
            int((duration + max_duration - 0.001) // max_duration),
            int((len(vi) + max_chars - 1) // max_chars) if vi else 1,
        )

        if target_parts <= 1:
            new_rows.append({**row, "start_sec": start_sec, "end_sec": end_sec})
            continue

        vi_parts = _split_text_natural(vi, target_parts)
        parts = len(vi_parts)
        zh_parts = _split_text_natural(zh, parts) if zh else [""] * parts
        if len(zh_parts) < parts:
            zh_parts += [""] * (parts - len(zh_parts))

        weights = [max(1, len(p)) for p in vi_parts]
        total_weight = sum(weights)
        cursor = start_sec
        original_idx = int(row.get("idx") or len(new_rows) + 1)
        for part_i, (vi_part, weight) in enumerate(zip(vi_parts, weights)):
            if part_i == parts - 1:
                next_time = end_sec
            else:
                next_time = start_sec + duration * (sum(weights[:part_i + 1]) / total_weight)
            new_rows.append({
                **row,
                "start": sec_to_srt_time(cursor),
                "end": sec_to_srt_time(next_time),
                "start_sec": cursor,
                "end_sec": next_time,
                "zh": zh_parts[part_i] if part_i < len(zh_parts) else "",
                "vi": vi_part,
            })
            cursor = next_time
        split_items.append({"idx": original_idx, "parts": parts})

    for i, row in enumerate(new_rows, 1):
        row["idx"] = i
    return new_rows, split_items


def _shift_cues(cues: list[dict], offset: float) -> list[dict]:
    from translate_video import sec_to_srt_time
    offset = float(offset or 0.0)
    shifted = []
    for i, c in enumerate(cues, 1):
        s = max(0.0, float(c["start_sec"]) + offset)
        e = max(s + 0.05, float(c["end_sec"]) + offset)
        shifted.append({**c, "idx": i, "start_sec": s, "end_sec": e,
                        "start": sec_to_srt_time(s), "end": sec_to_srt_time(e)})
    return shifted


def _run_translate_pipeline(job_id: str, video_path: str, req: TranslateRequest):
    """BÆ°á»›c 1: STT + Dá»‹ch â€” dá»«ng láº¡i Ä‘á»ƒ ngÆ°á»i dĂ¹ng xem/sá»­a phá»¥ Ä‘á»."""
    from translate_video import (
        extract_audio_for_stt, stt_groq, translate_srt, build_srt,
    )

    jobs[job_id].update(status="running", progress=0, message="Dang khoi dong...")

    groq_key     = req.groq_key.strip() or os.environ.get("GROQ_API_KEY", "")
    beeknoee_key = req.beeknoee_key.strip() or os.environ.get("BEEKNOEE_API_KEY", "")

    if not groq_key:
        jobs[job_id].update(status="error", message="Chua co Groq API key")
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
        jobs[job_id]["original_volume"] = req.original_volume

        # 1. TĂ¡ch audio STT
        _prog(job_id, 0.1, "Tach audio...")
        audio_stt = work_dir / "audio_stt.mp3"
        extract_audio_for_stt(video_copy, audio_stt)

        # 2. STT
        _prog(job_id, 0.3, "Nhan dang giong noi (Whisper)...")
        zh_cues = stt_groq(audio_stt, groq_key)
        jobs[job_id]["zh_cues"] = zh_cues

        # 3. Dá»‹ch
        _prog(job_id, 0.5, f"Dich {len(zh_cues)} doan...")

        def on_chunk(done, total, _partial):
            pct = 0.5 + (done / total) * 0.45
            _prog(job_id, pct, f"Dich chunk {done}/{total}...")

        vi_cues = translate_srt(zh_cues, groq_key, beeknoee_key=beeknoee_key, chunk_cb=on_chunk)
        jobs[job_id]["vi_cues"] = vi_cues

        _write_job_subtitles(jobs[job_id])

        jobs[job_id].update(status="translated", progress=100,
                            message=f"Dich xong {len(vi_cues)} doan. Xem va nhan Tao video.")

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))
        _maybe_refund(job_id)


def _run_render(job_id: str, voice: str, bg_volume: float, tts_volume: float,
                keep_original_audio: bool = False, original_volume: float = 0.25,
                sync_offset: float = 0.0,
                capcut_rate: str = "1.0", speed_ratio: float = 1.0,
                bg_music_path: str | None = None,
                logo_path: str | None = None,
                logo_pos: str = "topright", logo_size: int = 100,
                logo_tab: str = "none", logo_text: str = "",
                logo_fontsize: int = 28, logo_color: str = "#ffffff",
                render_preset: str = "veryfast"):
    """BÆ°á»›c 2: TTS CapCut + render video."""
    from translate_video import (
        build_srt, build_tts_track, render_video, get_audio_duration,
        CAPCUT_DEVICE_ID, CAPCUT_VOICES_VI,
    )

    job = jobs[job_id]
    jobs[job_id].update(status="rendering", progress=0, message="Bat dau tao TTS...")

    try:
        work_dir   = Path(job["work_dir"])
        video_path = Path(job["video_path"])
        vi_cues    = _shift_cues(job["vi_cues"], sync_offset)
        srt_path   = work_dir / "captions_vi.srt"
        srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

        _prog(job_id, 0.1, "Tao TTS (CapCut)...")
        jobs[job_id]["logs"] = []
        capcut_info        = next((v for v in CAPCUT_VOICES_VI if v[1] == voice), None)
        capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", CAPCUT_DEVICE_ID) if capcut_info else None
        capcut_voice_type  = capcut_info[1] if capcut_info else None
        capcut_resource_id = capcut_info[2] if capcut_info else None

        def _tts_progress(done: int, total: int):
            pct = 0.1 + 0.7 * (done / total) if total else 0.1
            _prog(job_id, pct, f"TTS {done}/{total} doan...")

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
                     original_audio=video_path if keep_original_audio and original_volume > 0 else None,
                     original_volume=original_volume,
                     logo=Path(logo_path) if logo_path else None,
                     logo_pos=logo_pos, logo_size=logo_size,
                     watermark=logo_text,
                     render_preset=render_preset)

        jobs[job_id].update(status="done", progress=100,
                            message="Hoan tat!", output=str(output_path))

    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))
        return


def _run_preview(job_id: str, voice: str, bg_volume: float, tts_volume: float,
                 keep_original_audio: bool, original_volume: float,
                 sync_offset: float,
                 preview_start: float, preview_duration: float,
                 capcut_rate: str = "1.0", speed_ratio: float = 1.0,
                 bg_music_path: str | None = None,
                 logo_text: str = "",
                 render_preset: str = "veryfast"):
    """Render a short preview clip with shifted cues and fresh CapCut TTS."""
    from translate_video import (
        FFMPEG_BIN, build_srt, build_tts_track, render_video, sec_to_srt_time,
        CAPCUT_DEVICE_ID, CAPCUT_VOICES_VI, run,
    )

    job = jobs[job_id]
    preview_duration = max(30.0, min(float(preview_duration), 300.0))
    preview_start = max(0.0, float(preview_start))
    preview_end = preview_start + preview_duration
    jobs[job_id].update(status="rendering", progress=0,
                        message=f"Tao preview {preview_start:.0f}s-{preview_end:.0f}s...")

    try:
        work_dir = Path(job["work_dir"])
        src_video = Path(job["video_path"])
        preview_dir = work_dir / "preview"
        preview_dir.mkdir(exist_ok=True)
        clip_path = preview_dir / "preview_src.mp4"
        srt_path = preview_dir / "captions_preview.srt"
        output_path = preview_dir / "preview_vi.mp4"

        run([
            FFMPEG_BIN, "-y",
            "-ss", f"{preview_start:.3f}",
            "-t", f"{preview_duration:.3f}",
            "-i", str(src_video),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "160k",
            str(clip_path),
        ], timeout=900)

        shifted = []
        for cue in _shift_cues(job.get("vi_cues", []), sync_offset):
            s = float(cue["start_sec"])
            e = float(cue["end_sec"])
            if e <= preview_start or s >= preview_end:
                continue
            ns = max(0.0, s - preview_start)
            ne = min(preview_duration, e - preview_start)
            if ne - ns < 0.05:
                continue
            shifted.append({
                **cue,
                "start_sec": ns,
                "end_sec": ne,
                "start": sec_to_srt_time(ns),
                "end": sec_to_srt_time(ne),
            })

        if not shifted:
            raise RuntimeError("Khoang preview khong co phu de nao")

        srt_path.write_text(build_srt(shifted), encoding="utf-8")
        capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == voice), None)
        capcut_device_id = os.environ.get("CAPCUT_DEVICE_ID", CAPCUT_DEVICE_ID) if capcut_info else None
        capcut_voice_type = capcut_info[1] if capcut_info else None
        capcut_resource_id = capcut_info[2] if capcut_info else None

        def _tts_progress(done: int, total: int):
            pct = 0.1 + 0.65 * (done / total) if total else 0.1
            _prog(job_id, pct, f"Preview TTS {done}/{total}...")

        def _tts_log(msg: str):
            _job_log(job_id, "[preview] " + msg)

        _prog(job_id, 0.1, "Tao TTS preview...")
        jobs[job_id]["logs"] = []
        tts_track = asyncio.run(build_tts_track(
            shifted, preview_dir, preview_duration,
            capcut_device_id=capcut_device_id,
            capcut_voice_type=capcut_voice_type,
            capcut_resource_id=capcut_resource_id,
            capcut_delay=1.5,
            capcut_rate=capcut_rate,
            speed_ratio=speed_ratio,
            progress_cb=_tts_progress,
            log_cb=_tts_log,
        ))

        _prog(job_id, 0.85, "Render preview...")
        render_video(
            clip_path, tts_track, srt_path, output_path,
            bg_music=Path(bg_music_path) if bg_music_path else None,
            bg_volume=bg_volume, tts_volume=tts_volume,
            original_audio=clip_path if keep_original_audio and original_volume > 0 else None,
            original_volume=original_volume,
            watermark=logo_text,
            render_preset=render_preset,
        )

        jobs[job_id].update(status="translated", progress=100,
                            message="Preview xong.", preview_output=str(output_path))
    except Exception as e:
        import traceback; traceback.print_exc()
        jobs[job_id].update(status="error", message=str(e))


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
    tts_volume: float = Form(1.0),
    original_volume: float = Form(0.25),
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
                                  original_volume=original_volume,
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
    tts_volume:float = Form(1.0),
    original_volume: float = Form(0.25),
):
    """Upload video + SRT sáºµn cĂ³, bá» qua STT + dá»‹ch AI."""
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
    json_path = work_dir / "captions_vi.json"

    jobs[job_id] = {
        "status":       "translated",
        "progress":     100,
        "message":      f"Da nap SRT ({len(vi_cues)} doan)",
        "filename":     file.filename,
        "work_dir":     str(work_dir),
        "video_path":   str(video_copy),
        "srt_path":     str(srt_path),
        "json_path":    str(json_path),
        "vi_cues":      vi_cues,
        "zh_cues":      [],
        "voice":        voice,
        "bg_volume":    bg_volume,
        "tts_volume":   tts_volume,
        "original_volume": original_volume,
        "duration_secs": round(duration, 1),
    }

    _write_job_subtitles(jobs[job_id])
    rows = _rows_from_cues(jobs[job_id], vi_cues)
    return {"job_id": job_id, "rows": rows, "duration_secs": round(duration, 1)}


@app.post("/api/upload-with-json")
async def upload_with_json(
    request:   Request,
    file:      UploadFile = File(...),
    captions:  UploadFile = File(...),
    voice:     str   = Form("BV421_vivn_streaming"),
    bg_volume: float = Form(0.3),
    tts_volume:float = Form(1.0),
    original_volume: float = Form(0.25),
):
    """Upload video + captions_vi.json, bo qua STT + dich AI."""
    from translate_video import build_srt

    suffix  = Path(file.filename).suffix or ".mp4"
    tmp     = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(await file.read())
    tmp.close()

    duration = get_video_duration(tmp.name)

    try:
        data = json.loads((await captions.read()).decode("utf-8-sig", errors="replace"))
        rows = data.get("rows", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("JSON phai la mang hoac object co field rows")
        vi_cues = _rows_to_cues(rows)
    except Exception as e:
        os.unlink(tmp.name)
        return JSONResponse({"error": f"JSON khong hop le: {e}"}, status_code=400)
    if not vi_cues:
        os.unlink(tmp.name)
        return JSONResponse({"error": "JSON rong hoac sai dinh dang"}, status_code=400)

    zh_cues = []
    for i, row in enumerate(rows, 1):
        start = vi_cues[i - 1]["start"]
        end = vi_cues[i - 1]["end"]
        zh_cues.append({
            "idx": i,
            "start": start,
            "end": end,
            "start_sec": vi_cues[i - 1]["start_sec"],
            "end_sec": vi_cues[i - 1]["end_sec"],
            "text": str(row.get("zh", "")),
        })

    job_id     = str(uuid.uuid4())
    work_dir   = Path(tempfile.mkdtemp(prefix=f"tr_{job_id[:8]}_"))
    video_copy = work_dir / ("video" + suffix)
    shutil.copy2(tmp.name, video_copy)
    os.unlink(tmp.name)

    srt_path = work_dir / "captions_vi.srt"
    json_path = work_dir / "captions_vi.json"

    jobs[job_id] = {
        "status":       "translated",
        "progress":     100,
        "message":      f"Da nap JSON ({len(vi_cues)} doan)",
        "filename":     file.filename,
        "work_dir":     str(work_dir),
        "video_path":   str(video_copy),
        "srt_path":     str(srt_path),
        "json_path":    str(json_path),
        "vi_cues":      vi_cues,
        "zh_cues":      zh_cues,
        "voice":        voice,
        "bg_volume":    bg_volume,
        "tts_volume":   tts_volume,
        "original_volume": original_volume,
        "duration_secs": round(duration, 1),
    }

    _write_job_subtitles(jobs[job_id])
    return {"job_id": job_id, "rows": _rows_from_cues(jobs[job_id]), "duration_secs": round(duration, 1)}


def _get_job(job_id: str):
    """Láº¥y job local trong tiáº¿n trĂ¬nh hiá»‡n táº¡i."""
    job = jobs.get(job_id)
    if not job:
        return None, JSONResponse({"error": "Khong tim thay"}, status_code=404)
    return job, None


@app.get("/api/voices")
async def voices(request: Request):
    from translate_video import CAPCUT_VOICES_VI
    return {
        "voices": [
            {"name": name, "voice_type": voice_type, "resource_id": resource_id}
            for name, voice_type, resource_id in CAPCUT_VOICES_VI
        ]
    }


@app.get("/api/app-settings")
async def app_settings(request: Request):
    return {
        "has_beeknoee_key": bool(os.environ.get("BEEKNOEE_API_KEY", "")),
    }


@app.get("/api/voice-preview/{voice_type}")
async def voice_preview(voice_type: str, request: Request):
    from translate_video import CAPCUT_DEVICE_ID, CAPCUT_VOICES_VI, capcut_tts_batch

    info = next((v for v in CAPCUT_VOICES_VI if v[1] == voice_type), None)
    if not info:
        return JSONResponse({"error": "Khong tim thay giong doc"}, status_code=404)

    name, _, resource_id = info
    cache_key = voice_type
    cached = voice_preview_cache.get(cache_key)
    if cached and Path(cached).exists():
        return FileResponse(cached, media_type="audio/mpeg", filename=f"{voice_type}.mp3")

    preview_dir = RUNTIME_DIR / "voice_previews"
    preview_dir.mkdir(exist_ok=True)
    out_path = preview_dir / f"{voice_type}.mp3"
    preview_name = VOICE_PREVIEW_NAMES.get(voice_type, name)
    text = f"Xin ch\u00e0o b\u1ea1n, \u0111\u00e2y l\u00e0 gi\u1ecdng \u0111\u1ecdc {preview_name}."
    device_id = os.environ.get("CAPCUT_DEVICE_ID", CAPCUT_DEVICE_ID)
    try:
        capcut_tts_batch([text], voice_type, resource_id, [out_path], device_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    voice_preview_cache[cache_key] = str(out_path)
    return FileResponse(out_path, media_type="audio/mpeg", filename=f"{voice_type}.mp3")


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
    """Tráº£ vá» báº£ng phá»¥ Ä‘á» Ä‘á»ƒ hiá»ƒn thá»‹/edit."""
    job, err = _get_job(job_id)
    if err: return err
    vi_cues = job.get("vi_cues", [])
    rows = _rows_from_cues(job, vi_cues)
    return {"rows": rows}


@app.post("/api/save-cues/{job_id}")
async def save_cues(job_id: str, body: dict, request: Request):
    """LÆ°u báº£n dá»‹ch Ä‘Ă£ edit vĂ o memory."""
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error", "rendering"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    rows    = body.get("rows", [])
    vi_cues = job.get("vi_cues", [])
    updated = []
    for i, c in enumerate(vi_cues):
        text = rows[i].get("vi", c.get("text", "")) if i < len(rows) else c.get("text", "")
        updated.append({**c, "idx": i + 1, "text": text})
    job["vi_cues"] = updated
    _write_job_subtitles(job)
    return {"ok": True}


@app.post("/api/detect-chinese/{job_id}")
async def detect_chinese(job_id: str, body: SubtitleRowsRequest, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    rows = body.rows or _rows_from_cues(job)
    hits = []
    for i, row in enumerate(rows):
        vi = str(row.get("vi", row.get("text", "")))
        if _has_cjk(vi):
            hits.append({
                "idx": int(row.get("idx") or i + 1),
                "row": i,
                "vi": vi,
                "zh": str(row.get("zh", "")),
            })
    return {"ok": True, "count": len(hits), "hits": hits}


@app.post("/api/fix-chinese/{job_id}")
async def fix_chinese(job_id: str, body: FixCueRequest, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error", "rendering"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    rows = body.rows or _rows_from_cues(job)
    target_pos = None
    for i, row in enumerate(rows):
        if int(row.get("idx") or i + 1) == int(body.idx):
            target_pos = i
            break
    if target_pos is None:
        return JSONResponse({"error": "Khong tim thay dong can sua"}, status_code=404)

    row = rows[target_pos]
    zh_text = str(row.get("zh", ""))
    vi_text = str(row.get("vi", row.get("text", "")))
    if not zh_text:
        zh_cues = job.get("zh_cues", [])
        if target_pos < len(zh_cues):
            zh_text = str(zh_cues[target_pos].get("text", ""))

    try:
        fixed = _repair_vi_text(zh_text, vi_text, body.groq_key, body.beeknoee_key)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    rows[target_pos]["vi"] = fixed
    vi_cues = job.get("vi_cues", [])
    updated = []
    for i, c in enumerate(vi_cues):
        text = rows[i].get("vi", c.get("text", "")) if i < len(rows) else c.get("text", "")
        updated.append({**c, "idx": i + 1, "text": text})
    job["vi_cues"] = updated
    _write_job_subtitles(job)
    return {"ok": True, "idx": int(body.idx), "vi": fixed, "has_chinese": _has_cjk(fixed)}


@app.post("/api/split-long-cues/{job_id}")
async def split_long_cues(job_id: str, body: SplitLongCuesRequest, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error", "rendering"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    rows = body.rows or _rows_from_cues(job)
    new_rows, split_items = _split_long_rows(rows, body.max_duration, body.max_chars)
    if not split_items:
        return {"ok": True, "changed": False, "count": 0, "split_items": [], "rows": rows}

    vi_cues = _rows_to_cues(new_rows)
    zh_cues = []
    for i, row in enumerate(new_rows, 1):
        zh_cues.append({
            "idx": i,
            "start": row["start"],
            "end": row["end"],
            "start_sec": row.get("start_sec"),
            "end_sec": row.get("end_sec"),
            "text": str(row.get("zh", "")),
        })

    job["vi_cues"] = vi_cues
    job["zh_cues"] = zh_cues
    _write_job_subtitles(job)
    saved_rows = _rows_from_cues(job, vi_cues)
    return {
        "ok": True,
        "changed": True,
        "count": len(split_items),
        "split_items": split_items,
        "rows": saved_rows,
    }


@app.post("/api/render/{job_id}")
async def render(
    job_id:       str,
    request:      Request,
    background_tasks: BackgroundTasks,
    voice:        str   = Form("BV421_vivn_streaming"),
    bg_volume:    float = Form(0.3),
    tts_volume:   float = Form(1.0),
    keep_original_audio: bool = Form(False),
    original_volume: float = Form(0.25),
    sync_offset:  float = Form(0.0),
    speed_ratio:  float = Form(1.0),
    logo_pos:     str   = Form("topright"),
    logo_size:    int   = Form(100),
    logo_tab:     str   = Form("none"),
    logo_text:    str   = Form(""),
    logo_fontsize:int   = Form(28),
    logo_color:   str   = Form("#ffffff"),
    render_preset:str   = Form("veryfast"),
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
        keep_original_audio, original_volume, sync_offset,
        "1.0", speed_ratio, bg_music_path,
        logo_path, logo_pos, logo_size,
        logo_tab, logo_text.strip(), logo_fontsize, logo_color,
        render_preset,
    )
    return {"ok": True}


@app.post("/api/preview/{job_id}")
async def preview(
    job_id:       str,
    request:      Request,
    background_tasks: BackgroundTasks,
    voice:        str   = Form("BV421_vivn_streaming"),
    bg_volume:    float = Form(0.3),
    tts_volume:   float = Form(1.0),
    keep_original_audio: bool = Form(False),
    original_volume: float = Form(0.25),
    sync_offset:  float = Form(0.0),
    speed_ratio:  float = Form(1.0),
    preview_start: float = Form(0.0),
    preview_duration: float = Form(30.0),
    logo_text:    str   = Form(""),
    render_preset:str   = Form("veryfast"),
    bg_music:     Optional[UploadFile] = File(None),
):
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua san sang preview"}, status_code=400)

    work_dir = Path(job["work_dir"])
    bg_music_path = None
    if bg_music and bg_music.filename:
        suffix = Path(bg_music.filename).suffix or ".mp3"
        p = work_dir / f"preview_bg_music{suffix}"
        p.write_bytes(await bg_music.read())
        bg_music_path = str(p)

    background_tasks.add_task(
        _run_preview, job_id, voice, bg_volume, tts_volume,
        keep_original_audio, original_volume, sync_offset,
        preview_start, preview_duration, "1.0", speed_ratio,
        bg_music_path, logo_text.strip(), render_preset,
    )
    return {"ok": True}


@app.post("/api/rerender/{job_id}")
async def rerender(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    voice:        str   = Form("BV421_vivn_streaming"),
    bg_volume:    float = Form(0.3),
    tts_volume:   float = Form(1.0),
    keep_original_audio: bool = Form(False),
    original_volume: float = Form(0.25),
    sync_offset:  float = Form(0.0),
    speed_ratio:  float = Form(1.0),
    logo_pos:     str   = Form("topright"),
    logo_size:    int   = Form(100),
    logo_tab:     str   = Form("none"),
    logo_text:    str   = Form(""),
    logo_fontsize:int   = Form(28),
    logo_color:   str   = Form("#ffffff"),
    render_preset:str   = Form("veryfast"),
    bg_music:     Optional[UploadFile] = File(None),
    logo:         Optional[UploadFile] = File(None),
):
    return await render(job_id, request, background_tasks, voice, bg_volume, tts_volume,
                        keep_original_audio, original_volume, sync_offset,
                        speed_ratio, logo_pos, logo_size, logo_tab, logo_text,
                        logo_fontsize, logo_color, render_preset, bg_music, logo)


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


@app.get("/api/download-preview/{job_id}")
async def download_preview(job_id: str, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    out = job.get("preview_output")
    if not out or not Path(out).exists():
        return JSONResponse({"error": "Preview chua san sang"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(out, media_type="video/mp4", filename=f"{fname}_preview.mp4")


@app.get("/api/download-srt/{job_id}")
async def download_srt(job_id: str, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    srt = job.get("srt_path")
    if not srt or not Path(srt).exists():
        return JSONResponse({"error": "Chua co SRT"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(srt, media_type="text/plain", filename=f"{fname}_vi.srt")


@app.get("/api/download-json/{job_id}")
async def download_json(job_id: str, request: Request):
    job, err = _get_job(job_id)
    if err: return err
    _write_job_subtitles(job)
    json_path = job.get("json_path")
    if not json_path or not Path(json_path).exists():
        return JSONResponse({"error": "Chua co JSON"}, status_code=404)
    fname = Path(job.get("filename", "video.mp4")).stem
    return FileResponse(json_path, media_type="application/json", filename=f"{fname}_vi.json")


@app.post("/api/load-json/{job_id}")
async def load_json(job_id: str, request: Request, file: UploadFile = File(...)):
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    try:
        data = json.loads((await file.read()).decode("utf-8-sig", errors="replace"))
        rows = data.get("rows", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("JSON phai la mang hoac object co field rows")
        new_cues = _rows_to_cues(rows)
    except Exception as e:
        return JSONResponse({"error": f"JSON khong hop le: {e}"}, status_code=400)

    if not new_cues:
        return JSONResponse({"error": "JSON rong"}, status_code=400)

    job["vi_cues"] = new_cues
    _write_job_subtitles(job)
    return {"ok": True, "rows": _rows_from_cues(job, new_cues)}


@app.post("/api/load-srt/{job_id}")
async def load_srt(job_id: str, request: Request, file: UploadFile = File(...)):
    """Náº¡p file SRT bĂªn ngoĂ i vĂ o job, ghi Ä‘Ă¨ báº£n dá»‹ch hiá»‡n táº¡i."""
    job, err = _get_job(job_id)
    if err: return err
    if job.get("status") not in ("translated", "done", "error"):
        return JSONResponse({"error": "Job chua dich xong"}, status_code=400)

    from translate_video import parse_srt
    content = (await file.read()).decode("utf-8", errors="replace")

    try:
        new_cues = parse_srt(content)
    except Exception as e:
        return JSONResponse({"error": f"SRT khong hop le: {e}"}, status_code=400)

    if not new_cues:
        return JSONResponse({"error": "SRT rong hoac sai dinh dang"}, status_code=400)

    job["vi_cues"] = new_cues
    srt_path = Path(job["srt_path"])
    srt_path.write_text(content, encoding="utf-8")

    zh_cues = job.get("zh_cues", [])
    zh_map  = {c["idx"]: c["text"] for c in zh_cues}
    rows    = [{"idx": c["idx"], "start": c["start"], "end": c["end"],
                "zh": zh_map.get(c["idx"], ""), "vi": c.get("text", "")}
               for c in new_cues]
    return {"ok": True, "rows": rows}


# Serve static assets.
voice_preview_dir = RUNTIME_DIR / "voice_previews"
voice_preview_dir.mkdir(exist_ok=True)
app.mount("/voice_previews", StaticFiles(directory=str(voice_preview_dir)), name="voice_previews")

# Serve static â€” pháº£i Ä‘áº·t cuá»‘i
static_dir = RUNTIME_DIR / "static_translate"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8005)
