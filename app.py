#!/usr/bin/env python3
"""
app.py — Giao diện Gradio cho translate_video.py
Chạy: python3 app.py
Mở:   http://localhost:8080
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import gradio as gr
import pandas as pd

from translate_video import (
    BASE_DIR,
    BEEKNOEE_BASE_URL, BEEKNOEE_MODEL,
    build_srt, build_tts_track,
    extract_audio_for_stt,
    get_audio_duration,
    render_video,
    srt_time_to_sec,
    stt_groq,
    translate_srt,
    CAPCUT_VOICES_VI,
)

# Load .env
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# _state đã chuyển sang gr.State() per-session — xem bên dưới trong gr.Blocks

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def get_groq_key(key_input: str) -> str:
    k = (key_input or "").strip() or os.environ.get("GROQ_API_KEY", "")
    if not k or k == "your_groq_key_here":
        raise ValueError("Chưa có Groq API key. Điền vào ô hoặc vào file .env")
    return k


def get_beeknoee_key(key_input: str) -> str | None:
    k = (key_input or "").strip() or os.environ.get("BEEKNOEE_API_KEY", "")
    return k if k else None


DEFAULT_WPS = 2.5  # từ/giây mặc định nếu chưa đo


def count_words(text: str) -> int:
    return len(text.strip().split())


def get_user_wps() -> float:
    return DEFAULT_WPS


def reading_speed_label(text: str, start_sec: float, end_sec: float) -> str:
    duration = end_sec - start_sec
    if duration <= 0 or not text.strip():
        return "—"
    wps   = count_words(text) / duration
    ideal = get_user_wps()
    ratio = wps / ideal
    pct   = (ratio - 1) * 100
    if abs(pct) <= 20:
        return "✅ Trong ngưỡng"
    elif pct > 0:
        return f"⚡ Nhanh hơn +{pct:.0f}%"
    else:
        return f"🐢 Chậm hơn {pct:.0f}%"


def reading_speed_pct(text: str, start_sec: float, end_sec: float) -> float:
    duration = end_sec - start_sec
    if duration <= 0 or not text.strip():
        return 0.0
    return (count_words(text) / duration / get_user_wps() - 1) * 100


def _normalize_cues(raw: list[dict]) -> tuple[list[dict], list[dict]]:
    """Chuẩn hóa cues từ server (zh/vi) hoặc local (text) → (vi_cues, zh_cues).
    Luôn dùng "text" làm nguồn duy nhất, xóa trường "vi" để tránh conflict."""
    vi_cues, zh_cues = [], []
    for c in raw:
        vi_text = c.get("vi") or c.get("text", "")
        zh_text = c.get("zh", "")
        # Bỏ "vi" khỏi dict, chỉ giữ "text" — tránh cues_to_df đọc "vi" cũ thay vì "text" mới
        clean = {k: v for k, v in c.items() if k != "vi"}
        vi_cues.append({**clean, "text": vi_text})
        if zh_text:
            zh_cues.append({"idx": c["idx"], "text": zh_text})
    return vi_cues, zh_cues


def cues_to_df(cues: list[dict], zh_cues: list[dict] | None = None) -> pd.DataFrame:
    zh_list = zh_cues or []
    return pd.DataFrame([
        {
            "#": c["idx"],
            "Bắt đầu": c["start"],
            "Kết thúc": c["end"],
            "Tiếng Trung": c.get("zh") or (zh_list[i]["text"] if i < len(zh_list) else ""),
            "Bản dịch": c.get("text", ""),
            "Tốc độ đọc": reading_speed_label(
                c.get("text", ""), c["start_sec"], c["end_sec"]
            ),
        }
        for i, c in enumerate(cues)
    ])


def df_to_cues(df: pd.DataFrame, original_cues: list[dict]) -> list[dict]:
    cues = []
    rows = df.to_dict("records")
    for i, row in enumerate(rows):
        base  = original_cues[i] if i < len(original_cues) else {}
        text  = str(row.get("Bản dịch", row.get("text", ""))).strip()
        start = str(row.get("Bắt đầu", base.get("start", "00:00:00,000")))
        end   = str(row.get("Kết thúc", base.get("end",  "00:00:00,000")))
        s_sec = srt_time_to_sec(start)
        e_sec = srt_time_to_sec(end)
        cues.append({
            **base,
            "idx":       i + 1,
            "start":     start,
            "end":       end,
            "start_sec": s_sec,
            "end_sec":   e_sec,
            "text":      text,
        })
    return cues


def refresh_speed_col(df: pd.DataFrame) -> pd.DataFrame:
    rows = df.to_dict("records")
    for row in rows:
        try:
            s = srt_time_to_sec(str(row.get("Bắt đầu", "00:00:00,000")))
            e = srt_time_to_sec(str(row.get("Kết thúc", "00:00:00,000")))
            row["Tốc độ đọc"] = reading_speed_label(str(row.get("Bản dịch", "")), s, e)
        except Exception:
            row["Tốc độ đọc"] = "—"
    return pd.DataFrame(rows)


def _tmp_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="vidtrans_")).resolve()
    return d


# ---------------------------------------------------------------------------
# OPTIMIZE
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = """Bạn là biên tập viên phụ đề tiếng Việt.

Nhiệm vụ: Nhận danh sách các đoạn phụ đề (JSON), viết lại toàn bộ sao cho:
1. Mỗi đoạn có số từ không vượt quá max_words đã cho (tốc độ đọc không quá 120% tốc độ chuẩn).
2. Xưng hô nhất quán xuyên suốt (chọn một cách xưng hô phù hợp và giữ nguyên).
3. Giữ nguyên nghĩa cốt lõi, văn phong tự nhiên tiếng Việt.
4. Nếu câu đã ngắn hơn max_words thì giữ nguyên (không cần kéo dài).

Trả về JSON array với đúng cấu trúc: [{"idx": ..., "text": "..."}, ...]
Không giải thích, không markdown, chỉ JSON thuần."""


def ai_rewrite_batch(cues: list[dict], user_wps: float, groq_key: str, beeknoee_key: str | None) -> list[dict]:
    """Gọi AI 1 lần để viết lại toàn bộ cues, nhất quán xưng hô + chỉnh số từ."""
    from translate_video import _make_chat_client
    import json as _json
    client, model = _make_chat_client(groq_key, beeknoee_key)

    payload = []
    for c in cues:
        window = c["end_sec"] - c["start_sec"]
        max_words = max(1, int(window * user_wps * 1.2))
        current = count_words(c["text"])
        payload.append({
            "idx":       c["idx"],
            "text":      c["text"],
            "duration":  round(window, 2),
            "max_words": max_words,
            "current_words": current,
        })

    prompt = (
        f"Tốc độ đọc chuẩn: {user_wps:.2f} từ/giây. "
        f"Mỗi đoạn có trường max_words = số từ tối đa cho phép (120% tốc độ chuẩn × thời lượng).\n\n"
        f"Danh sách đoạn phụ đề:\n{_json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": REWRITE_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.15,
            )
            raw = (resp.choices[0].message.content or "").strip()
            raw = re.sub(r"```\w*\n?", "", raw).strip().rstrip("`")
            result = _json.loads(raw)
            by_idx = {r["idx"]: r["text"] for r in result}
            return [{**c, "text": by_idx.get(c["idx"], c["text"])} for c in cues]
        except Exception as e:
            if attempt == 2:
                print(f"  ⚠ AI rewrite batch thất bại: {e}")
                return cues
            time.sleep(3)
    return cues


def optimize_cues(cues: list[dict], groq_key: str, beeknoee_key: str | None = None, progress_cb=None) -> list[dict]:
    user_wps = get_user_wps()
    needs_fix = [c for c in cues if reading_speed_pct(c["text"], c["start_sec"], c["end_sec"]) > 20]
    if not needs_fix:
        return cues

    if progress_cb:
        progress_cb(0.1, desc=f"AI viết lại {len(needs_fix)}/{len(cues)} đoạn quá dài...")

    result = ai_rewrite_batch(cues, user_wps, groq_key, beeknoee_key)

    if progress_cb:
        progress_cb(1.0, desc="Xong!")
    return result


def run_optimize(df: pd.DataFrame, state: dict, progress=gr.Progress()):
    if not state.get("vi_cues"):
        raise gr.Error("Chưa chạy STT + Dịch.")

    groq_key     = state.get("groq_key", "")
    beeknoee_key = state.get("beeknoee_key")
    cues         = df_to_cues(df, state["vi_cues"])
    user_wps     = get_user_wps()

    needs_fix = [c for c in cues if reading_speed_pct(c["text"], c["start_sec"], c["end_sec"]) > 20]
    if not needs_fix:
        return df, f"✅ Tất cả đoạn trong ngưỡng tốt (tốc độ chuẩn: {user_wps:.1f} từ/giây).", state

    optimized = optimize_cues(cues, groq_key, beeknoee_key=beeknoee_key, progress_cb=progress)
    state["vi_cues"] = optimized
    fixed = sum(1 for c in optimized
                if reading_speed_pct(c["text"], c["start_sec"], c["end_sec"]) <= 20)
    return cues_to_df(optimized, state.get("zh_cues")), (
        f"✓ Tối ưu xong — {fixed}/{len(needs_fix)} đoạn đã vào ngưỡng "
        f"(tốc độ chuẩn: {user_wps:.1f} từ/giây)"
    ), state


# ---------------------------------------------------------------------------
# STEP 1: STT + Dịch
# ---------------------------------------------------------------------------

def _save_cache(work_dir: Path, vi_cues: list, video_path):
    cache = {
        "video_path": str(video_path),
        "vi_cues":    vi_cues,
    }
    (work_dir / "vi_cues.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, progress_offset=0.0, video_path=""):
    """Dịch, lưu cache vào work_dir. Trả về vi_cues."""
    provider_name = f"Beeknoee ({BEEKNOEE_MODEL})" if beeknoee_key else "Groq LLaMA"

    def on_chunk_done(done, total, partial):
        pct = progress_offset + (done / total) * 0.4
        progress(pct, desc=f"Dịch {provider_name}: chunk {done}/{total} ({done*20}/{len(zh_cues)} đoạn)...")
        _save_cache(work_dir, partial, video_path)

    progress(progress_offset, desc=f"Dịch Trung → Việt ({provider_name})...")
    vi_cues = translate_srt(zh_cues, groq_key, beeknoee_key=beeknoee_key, chunk_cb=on_chunk_done)
    _save_cache(work_dir, vi_cues, video_path)

    return vi_cues



def run_stt_translate(video_file, beeknoee_tts_input,
                      beeknoee_tts_voice_input, capcut_voice_sel, auto_translate,
                      state: dict, progress=gr.Progress()):
    state = {}

    groq_key     = get_groq_key("")
    beeknoee_key = get_beeknoee_key("")

    if video_file is None:
        raise gr.Error("Chưa chọn video.")

    capcut_vt   = (capcut_voice_sel or "").strip()
    capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == capcut_vt), None)

    src_path = Path(video_file)
    work_dir = _tmp_dir()
    state = {
        "work_dir":           work_dir,
        "groq_key":           groq_key,
        "beeknoee_key":       beeknoee_key,
        "beeknoee_tts_model": beeknoee_tts_input.strip() or None,
        "beeknoee_tts_voice": beeknoee_tts_voice_input.strip() or None,
        "capcut_device_id":   os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368") if capcut_info else None,
        "capcut_voice_type":  capcut_info[1] if capcut_info else None,
        "capcut_resource_id": capcut_info[2] if capcut_info else None,
    }

    try:
        video_copy = work_dir / ("video" + src_path.suffix)
        shutil.copy2(src_path, video_copy)
        video_path = video_copy
        state["video_path"] = video_path
        state["video_stem"] = src_path.stem

        if not auto_translate:
            return (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
                "✓ Đã nhận video — chờ JSON từ server",
                gr.update(visible=True, value=src_path.stem),
                state,
            )

        progress(0.15, desc="Tách audio STT...")
        audio_stt = work_dir / "audio_stt.mp3"
        extract_audio_for_stt(video_path, audio_stt)

        progress(0.5, desc="STT Groq Whisper...")
        zh_cues = stt_groq(audio_stt, groq_key)
        state["zh_cues"] = zh_cues

        vi_cues = _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, 0.6, video_path)
        state["vi_cues"] = vi_cues

        progress(1.0, desc="Xong!")
        df = cues_to_df(vi_cues, zh_cues)
        return (
            gr.update(visible=True, value=df),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=True),
            gr.update(visible=False),
            f"✓ STT + Dịch xong — {len(vi_cues)} đoạn.",
            gr.update(visible=False),
            state,
        )

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise gr.Error(str(e))


def run_translate_only(state: dict, progress=gr.Progress()):
    if not state.get("zh_cues"):
        raise gr.Error("Chưa có dữ liệu STT. Chạy Bước 1 trước.")

    groq_key     = state.get("groq_key", "")
    beeknoee_key = state.get("beeknoee_key")
    zh_cues      = state["zh_cues"]
    work_dir     = state["work_dir"]

    vi_cues = _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, 0.0, state.get("video_path", ""))
    state["vi_cues"] = vi_cues

    progress(1.0, desc="Dịch xong!")
    df = cues_to_df(vi_cues, zh_cues)
    return (
        gr.update(visible=True, value=df),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
        f"✓ Dịch xong — {len(vi_cues)} đoạn.",
        gr.update(visible=False),
        state,
    )


# ---------------------------------------------------------------------------
# LOAD JSON (từ server hoặc export trước)
# ---------------------------------------------------------------------------

def _parse_json_cues(json_file):
    """Đọc file JSON, trả về (vi_cues, zh_cues, raw_dict_hoặc_None)."""
    raw = json.loads(Path(json_file).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        vi_cues, zh_cues = _normalize_cues(raw)
        return vi_cues, zh_cues, None
    vi_cues, zh_cues = _normalize_cues(raw.get("vi_cues", []))
    return vi_cues, zh_cues, raw


def run_load_json(json_file, state: dict):
    if json_file is None:
        raise gr.Error("Chưa chọn file JSON.")

    vi_cues, zh_cues, raw = _parse_json_cues(json_file)
    groq_key     = os.environ.get("GROQ_API_KEY", "")
    beeknoee_key = os.environ.get("BEEKNOEE_API_KEY")

    def _make_state(video_path=None):
        s = {**state, "vi_cues": vi_cues, "zh_cues": zh_cues,
             "groq_key": groq_key, "beeknoee_key": beeknoee_key}
        if video_path:
            s["video_path"] = video_path
            s["work_dir"]   = Path(video_path).parent
        return s

    df = cues_to_df(vi_cues, zh_cues or None)
    ok = (gr.update(visible=True, value=df),
          gr.update(visible=True), gr.update(visible=True),
          gr.update(visible=True), gr.update(visible=False))

    if raw is None:
        has_video = bool(state.get("video_path"))
        return (*ok,
                f"✓ Load {len(vi_cues)} đoạn — {'sẵn sàng render' if has_video else 'upload video bên dưới rồi bấm Render'}",
                gr.update(visible=False), _make_state())

    video_path = Path(raw["video_path"])
    if not video_path.exists():
        return (*ok,
                f"✓ Load {len(vi_cues)} đoạn — video gốc không còn, upload lại bên dưới rồi bấm Render",
                gr.update(visible=False), _make_state())

    return (*ok,
            f"✓ Load {len(vi_cues)} đoạn — video: {video_path.stem}",
            gr.update(visible=False), _make_state(video_path))


def run_attach_video(video_file, state: dict):
    """Upload video cho JSON đã load."""
    if video_file is None:
        raise gr.Error("Chưa chọn video.")
    if not state.get("vi_cues"):
        raise gr.Error("Load file JSON trước.")

    src_path = Path(video_file)
    work_dir = _tmp_dir()
    video_copy = work_dir / ("video" + src_path.suffix)
    shutil.copy2(src_path, video_copy)
    state["work_dir"]   = work_dir
    state["video_path"] = video_copy
    state["video_stem"] = src_path.stem

    return (
        gr.update(visible=True),
        gr.update(visible=True),
        f"✓ Đã gắn video '{src_path.stem}' — {len(state['vi_cues'])} đoạn sẵn sàng render",
        state,
    )


# ---------------------------------------------------------------------------
# EXPORT JSON
# ---------------------------------------------------------------------------

def run_export_json(df: pd.DataFrame, state: dict):
    if not state.get("vi_cues") and not state.get("zh_cues"):
        raise gr.Error("Chưa có bản dịch để xuất.")

    vi_cues = df_to_cues(df, state.get("vi_cues", []))
    zh_map  = {c["idx"]: c["text"] for c in state.get("zh_cues", [])}

    export = [{
        "idx":       c["idx"],
        "start":     c["start"],
        "end":       c["end"],
        "start_sec": c["start_sec"],
        "end_sec":   c["end_sec"],
        "zh":        zh_map.get(c["idx"], ""),
        "vi":        c["text"],
    } for c in vi_cues]

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w", encoding="utf-8")
    json.dump(export, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    return gr.update(value=tmp.name, visible=True)


# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# TEST TTS
# ---------------------------------------------------------------------------

def run_test_tts(text: str, tts_model: str, tts_voice: str, capcut_voice: str):
    if not text.strip():
        raise gr.Error("Nhập text để test.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()

    # CapCut TTS
    if capcut_voice:
        device_id = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368")
        # Tìm resource_id từ CAPCUT_VOICES_VI
        voice_info = next((v for v in CAPCUT_VOICES_VI if v[1] == capcut_voice), None)
        if not voice_info:
            raise gr.Error(f"Không tìm thấy giọng CapCut: {capcut_voice}")
        from translate_video import capcut_tts_sync
        capcut_tts_sync(text.strip(), voice_info[1], voice_info[2], Path(tmp.name), device_id)
        return tmp.name

    model = tts_model.strip()
    voice = tts_voice.strip()

    if not model:
        import asyncio as _aio
        import edge_tts
        edge_voice = voice or "vi-VN-HoaiMyNeural"
        async def _run_edge():
            comm = edge_tts.Communicate(text.strip(), edge_voice)
            await comm.save(tmp.name)
        _aio.run(_run_edge())
    else:
        beeknoee_key = os.environ.get("BEEKNOEE_API_KEY", "")
        if not beeknoee_key:
            raise gr.Error("Chưa có Beeknoee API key.")
        from openai import OpenAI
        client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
        resp = client.audio.speech.create(
            model=model, voice=voice or "vi",
            input=text.strip(), response_format="mp3",
        )
        Path(tmp.name).write_bytes(resp.content)

    return tmp.name


# ---------------------------------------------------------------------------
# RENDER
# ---------------------------------------------------------------------------

def run_render(df: pd.DataFrame, bg_music_file, bg_volume: float, tts_volume: float,
               capcut_delay: float, capcut_voice_sel: str, capcut_rate: float,
               keep_original: bool, original_volume: float,
               watermark: str,
               state: dict, progress=gr.Progress()):
    if not state.get("vi_cues"):
        raise gr.Error("Chưa có bản dịch.")
    if not state.get("video_path"):
        raise gr.Error("Chưa có video — upload video vào ô 'Gắn video' bên dưới trước.")

    video_path = Path(state["video_path"]).resolve()
    work_dir   = Path(state["work_dir"]).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    bg_music    = Path(bg_music_file).resolve() if bg_music_file else None
    capcut_vt   = (capcut_voice_sel or "").strip()
    capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == capcut_vt), None)
    capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368") if capcut_info else state.get("capcut_device_id")
    capcut_voice_type  = capcut_info[1] if capcut_info else state.get("capcut_voice_type")
    capcut_resource_id = capcut_info[2] if capcut_info else state.get("capcut_resource_id")

    vi_cues  = df_to_cues(df, state["vi_cues"])
    srt_path = work_dir / "captions_vi.srt"
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

    try:
        progress(0.1, desc=f"Tạo TTS ({capcut_voice_type or 'Edge TTS'})...")
        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(
            vi_cues, work_dir, video_dur,
            beeknoee_key=state.get("beeknoee_key"),
            beeknoee_tts_model=state.get("beeknoee_tts_model"),
            beeknoee_tts_voice=state.get("beeknoee_tts_voice"),
            capcut_device_id=capcut_device_id,
            capcut_voice_type=capcut_voice_type,
            capcut_resource_id=capcut_resource_id,
            capcut_delay=capcut_delay,
            capcut_rate=str(round(capcut_rate, 1)),
        ))

        progress(0.6, desc="Render video...")
        output_path = work_dir / f"{Path(video_path).stem}_vi.mp4"
        render_video(video_path, tts_track, srt_path, output_path,
                     bg_music=bg_music, bg_volume=bg_volume, tts_volume=tts_volume,
                     original_audio=None, original_volume=0.0,
                     watermark=watermark)
        progress(1.0, desc="Hoàn tất!")
        # Copy to a named temp file so Gradio can serve it correctly
        import tempfile, shutil
        suffix = Path(output_path).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=None)
        tmp.close()
        shutil.copy2(str(output_path), tmp.name)
        return tmp.name, "✓ Render xong — bấm tải về bên dưới"

    except Exception as e:
        raise gr.Error(str(e))




# ---------------------------------------------------------------------------
# THUMBNAIL
# ---------------------------------------------------------------------------

def _get_thumb_source(image_file, frame_file):
    """Trả về path ảnh nguồn: frame ưu tiên nếu có, không thì dùng ảnh upload."""
    if frame_file and Path(frame_file).exists():
        return Path(frame_file)
    if image_file and Path(image_file).exists():
        return Path(image_file)
    return None


def _hex_to_rgba(hex_color: str, opacity_pct: float) -> tuple:
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    a = int((opacity_pct / 100) * 255)
    return r, g, b, a


def _draw_thumbnail(src_path: Path, text: str, font_size: int, bold: bool,
                    text_color: str, outline_color: str, outline_width: int,
                    bg_color: str, bg_opacity: float, bg_padding: int,
                    pos_x: float, pos_y: float, align: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(src_path).convert("RGBA")
    W, H = img.size

    # Tìm font
    font = None
    if bold:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    if not text.strip():
        out = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.convert("RGB").save(out.name)
        return Path(out.name)

    draw_tmp = ImageDraw.Draw(img)
    lines = text.split("\n")

    # Đo kích thước từng dòng
    line_bboxes = [draw_tmp.textbbox((0, 0), line, font=font) for line in lines]
    line_widths  = [bb[2] - bb[0] for bb in line_bboxes]
    line_heights = [bb[3] - bb[1] for bb in line_bboxes]
    line_gap     = max(4, font_size // 6)
    total_w = max(line_widths) if line_widths else 0
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)

    cx = int(W * pos_x / 100)
    cy = int(H * pos_y / 100)

    # Vẽ nền chữ nếu opacity > 0
    if bg_opacity > 0:
        r, g, b, a = _hex_to_rgba(bg_color, bg_opacity)
        pad = bg_padding
        bg_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bg_draw  = ImageDraw.Draw(bg_layer)
        bg_draw.rectangle(
            [cx - total_w // 2 - pad, cy - total_h // 2 - pad,
             cx + total_w // 2 + pad, cy + total_h // 2 + pad],
            fill=(r, g, b, a),
        )
        img = Image.alpha_composite(img, bg_layer)

    draw = ImageDraw.Draw(img)

    y_cursor = cy - total_h // 2
    for i, line in enumerate(lines):
        lw = line_widths[i]
        lh = line_heights[i]
        if align == "left":
            tx = cx - total_w // 2
        elif align == "right":
            tx = cx + total_w // 2 - lw
        else:
            tx = cx - lw // 2

        # Viền chữ
        if outline_width > 0:
            oc = outline_color
            for dx in range(-outline_width, outline_width + 1):
                for dy in range(-outline_width, outline_width + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((tx + dx, y_cursor + dy), line, font=font, fill=oc)

        draw.text((tx, y_cursor), line, font=font, fill=text_color)
        y_cursor += lh + line_gap

    out = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.convert("RGB").save(out.name)
    return Path(out.name)


def run_thumb_video_loaded(video_file):
    """Cập nhật slider max theo độ dài video."""
    if not video_file:
        return gr.update(maximum=100.0, value=0.0)
    try:
        dur = get_audio_duration(Path(video_file))
        return gr.update(maximum=round(dur, 1), value=0.0)
    except Exception:
        return gr.update(maximum=100.0, value=0.0)


def run_capture_frame(video_file, seek_sec):
    if not video_file:
        raise gr.Error("Chưa upload video.")
    from translate_video import FFMPEG_BIN, run as _run
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    out.close()
    _run([
        FFMPEG_BIN, "-y", "-ss", str(seek_sec), "-i", str(video_file),
        "-frames:v", "1", "-q:v", "2", out.name,
    ])
    return gr.update(value=out.name, visible=True), f"✓ Chụp frame tại {seek_sec:.1f}s"


def run_thumb_render(image_file, frame_file, text, font_size, bold,
                     text_color, outline_color, outline_width,
                     bg_color, bg_opacity, bg_padding,
                     pos_x, pos_y, align):
    src = _get_thumb_source(image_file, frame_file)
    if src is None:
        raise gr.Error("Chưa có ảnh. Upload ảnh hoặc chụp frame từ video.")
    try:
        out = _draw_thumbnail(
            src, text, int(font_size), bold,
            text_color, outline_color, int(outline_width),
            bg_color, bg_opacity, int(bg_padding),
            pos_x, pos_y, align,
        )
        return str(out), "✓ Preview xong"
    except Exception as e:
        raise gr.Error(str(e))


def run_thumb_export(image_file, frame_file, text, font_size, bold,
                     text_color, outline_color, outline_width,
                     bg_color, bg_opacity, bg_padding,
                     pos_x, pos_y, align):
    src = _get_thumb_source(image_file, frame_file)
    if src is None:
        raise gr.Error("Chưa có ảnh. Upload ảnh hoặc chụp frame từ video.")
    try:
        out = _draw_thumbnail(
            src, text, int(font_size), bold,
            text_color, outline_color, int(outline_width),
            bg_color, bg_opacity, int(bg_padding),
            pos_x, pos_y, align,
        )
        return gr.update(value=str(out), visible=True), "✓ Xuất xong — bấm tải về"
    except Exception as e:
        raise gr.Error(str(e))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_BEEP_JS = """
function playErrorBeep() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'square';
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.8);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.8);
    } catch(e) {}
}

function playDoneChime() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const gain = ctx.createGain();
        gain.connect(ctx.destination);
        // 2 nốt: sol → do cao — nghe như "ding-dong"
        [[784, 0, 0.15], [1046, 0.18, 0.25]].forEach(([freq, delay, dur]) => {
            const osc = ctx.createOscillator();
            osc.connect(gain);
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, ctx.currentTime + delay);
            gain.gain.setValueAtTime(0.0, ctx.currentTime + delay);
            gain.gain.linearRampToValueAtTime(0.25, ctx.currentTime + delay + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + dur);
            osc.start(ctx.currentTime + delay);
            osc.stop(ctx.currentTime + delay + dur);
        });
    } catch(e) {}
}

// Theo dõi progress bar: khi ẩn đi (xong việc) → phát chime
let _wasWorking = false;
const observer = new MutationObserver(() => {
    // Lỗi → beep
    document.querySelectorAll('.toast-wrap .error, .svelte-notification.error').forEach(el => {
        if (!el.dataset.beeped) { el.dataset.beeped = '1'; playErrorBeep(); }
    });
    // Progress bar xuất hiện → đang chạy
    const bar = document.querySelector('.progress-bar, .eta-bar, [class*="progress"]');
    const working = bar && bar.offsetParent !== null;
    if (_wasWorking && !working) { playDoneChime(); }
    _wasWorking = working;
});
observer.observe(document.body, { childList: true, subtree: true, attributes: true });
"""

TTS_MODELS = [
    ("🆓 Google TTS Free ($0)",         "google/google-tts"),
    ("OpenAI GPT-4o Mini TTS ($12/1M)", "openai/gpt-4o-mini-tts"),
]

TTS_VOICES = {
    "google/google-tts":      [("🇻🇳 Tiếng Việt", "vi"), ("🇺🇸 English", "en"), ("🇨🇳 中文", "zh"), ("🇯🇵 日本語", "ja")],
    "openai/gpt-4o-mini-tts": [("nova", "nova"), ("alloy", "alloy"), ("echo", "echo"), ("onyx", "onyx"), ("shimmer", "shimmer"), ("ash", "ash"), ("coral", "coral"), ("sage", "sage"), ("verse", "verse"), ("fable", "fable")],
}


def get_voices(model: str):
    if not model:
        edge_voices = [
            ("vi-VN-HoaiMyNeural (Nữ)", "vi-VN-HoaiMyNeural"),
            ("vi-VN-NamMinhNeural (Nam)", "vi-VN-NamMinhNeural"),
        ]
        return gr.update(choices=edge_voices, value="vi-VN-HoaiMyNeural")
    voices = TTS_VOICES.get(model, [("vi", "vi")])
    return gr.update(choices=voices, value=voices[0][1])


with gr.Blocks(title="Dịch Video Tiếng Trung → Tiếng Việt") as demo:
    app_title = gr.Textbox(value="Dịch Video Tiếng Trung → Tiếng Việt", label="Tên ứng dụng", interactive=True)
    title_md = gr.Markdown("# Dịch Video Tiếng Trung → Tiếng Việt")
    app_title.change(fn=lambda t: f"# {t}", inputs=app_title, outputs=title_md)
    session_state = gr.State({})

    with gr.Tabs():

        # ══════════════════════════════════════════════════════════════════
        # TAB 1: DỊCH VIDEO
        # ══════════════════════════════════════════════════════════════════
        with gr.TabItem("🎬 Dịch Video"):

            # ── PHẦN 1: Upload video + chạy pipeline ──────────────────────
            gr.Markdown("## Bước 1 — Upload & Xử lý")
            with gr.Row():
                with gr.Column(scale=2):
                    video_input = gr.Video(label="Upload video tiếng Trung", sources=["upload"])
                with gr.Column(scale=1):
                    capcut_voice_input = gr.Dropdown(
                        label="Giọng đọc TTS",
                        choices=[("-- Edge TTS mặc định --", "")] + [(n, vt) for n, vt, _ in CAPCUT_VOICES_VI],
                        value="BV421_vivn_streaming",
                    )
                    beeknoee_tts_input    = gr.Textbox(value="",  visible=False)
                    beeknoee_tts_voice_input = gr.Textbox(value="vi", visible=False)
                    auto_translate_toggle = gr.Checkbox(value=True, visible=False)
                    btn_stt = gr.Button("▶ Chạy Bước 1", variant="primary")

            status_stt = gr.Textbox(label="Trạng thái", interactive=False)
            video_stem_display = gr.Textbox(interactive=False, visible=False)

            # ── PHẦN 2: Khôi phục từ cache ────────────────────────────────
            with gr.Accordion("♻️ Khôi phục bản dịch (vi_cues.json)", open=False):
                gr.Markdown("Load file JSON bản dịch. Nếu file không có đường dẫn video (JSON cũ), upload video bên dưới để gắn vào.")
                load_json_file = gr.File(label="Chọn file JSON bản dịch", file_types=[".json"])
                btn_load_json  = gr.Button("📂 Load JSON", variant="secondary")
                with gr.Group() as attach_video_group:
                    gr.Markdown("**Upload video để gắn vào bản dịch** (cần khi JSON không có đường dẫn video):")
                    attach_video_input  = gr.Video(label="Upload video gốc", sources=["upload"])
                    btn_attach_video    = gr.Button("🎬 Gắn video + Tách audio nền", variant="primary")
                    status_attach_video = gr.Textbox(label="Trạng thái", interactive=False)

            # ── PHẦN 3: Test TTS ───────────────────────────────────────────
            with gr.Accordion("🔊 Test giọng đọc TTS", open=False):
                tts_test_text = gr.Textbox(label="Text thử", placeholder="Nhập câu tiếng Việt...")
                with gr.Row():
                    tts_test_capcut = gr.Dropdown(
                        label="CapCut TTS (ưu tiên)",
                        choices=[("-- Không dùng --", "")] + [(n, vt) for n, vt, _ in CAPCUT_VOICES_VI],
                        value="BV421_vivn_streaming", scale=1,
                    )
                    tts_test_model = gr.Dropdown(
                        label="Model khác (khi không dùng CapCut)",
                        choices=[("-- Edge TTS --", "")] + [(l, v) for l, v in TTS_MODELS],
                        value="", scale=1,
                    )
                    tts_test_voice = gr.Dropdown(
                        label="Voice",
                        choices=[("vi-VN-HoaiMyNeural (Nữ)", "vi-VN-HoaiMyNeural"), ("vi-VN-NamMinhNeural (Nam)", "vi-VN-NamMinhNeural")],
                        value="vi-VN-HoaiMyNeural", scale=1,
                    )
                    btn_test_tts = gr.Button("▶ Test", variant="secondary", scale=1)
                tts_test_audio = gr.Audio(label="Kết quả", interactive=False)

            # ── PHẦN 4: Bảng dịch + nút hành động ────────────────────────
            gr.Markdown("## Chỉnh sửa bản dịch")
            gr.Markdown("Bấm vào ô **Bản dịch** để sửa trực tiếp.")

            translation_table = gr.Dataframe(
                headers=["#", "Bắt đầu", "Kết thúc", "Tiếng Trung", "Bản dịch", "Tốc độ đọc"],
                datatype=["number", "str", "str", "str", "str", "str"],
                column_count=(6, "fixed"),
                interactive=True,
                wrap=True,
                visible=False,
                column_widths=["4%", "9%", "9%", "30%", "30%", "18%"],
            )

            btn_translate_only = gr.Button("▶ Dịch Trung → Việt", variant="primary", visible=False)

            with gr.Row():
                bg_music_input = gr.Audio(
                    label="🎵 Nhạc nền (tùy chọn — sẽ loop tự động)",
                    type="filepath", sources=["upload"], scale=2,
                )
            with gr.Row():
                bg_volume_slider    = gr.Slider(0.0, 2.0, value=0.3, step=0.05, label="Âm lượng nhạc nền")
                tts_volume_slider   = gr.Slider(0.0, 3.0, value=1.8, step=0.05, label="Âm lượng lồng tiếng")
            watermark_input        = gr.Textbox(value="nem_vietsub", label="Tên logo DVD (để trống nếu không muốn)")
            capcut_delay_slider    = gr.Slider(value=1.5, visible=False)
            capcut_rate_slider     = gr.Slider(value=1.0, visible=False)
            keep_original_check    = gr.Checkbox(value=False, visible=False)
            original_volume_slider = gr.Slider(value=0.3,  visible=False)

            with gr.Row():
                btn_optimize    = gr.Button("✨ Tối ưu AI",   variant="secondary", visible=False)
                btn_export_json = gr.Button("💾 Xuất JSON",    variant="secondary", visible=False)
                btn_render      = gr.Button("🎬 Render Video", variant="primary",   visible=False)

            json_download = gr.File(label="Tải JSON bản dịch", visible=False, interactive=False)
            status_optimize = gr.Textbox(label="Trạng thái tối ưu", interactive=False)
            status_render   = gr.Textbox(label="Trạng thái render",  interactive=False)
            video_output    = gr.File(label="Video kết quả (bấm tải về)", interactive=False)

            # ── Events ────────────────────────────────────────────────────
            _stt_outs = [
                translation_table,
                btn_optimize, btn_export_json, btn_render, btn_translate_only,
                status_stt, video_stem_display, session_state,
            ]

            btn_stt.click(
                fn=run_stt_translate,
                inputs=[video_input, beeknoee_tts_input,
                        beeknoee_tts_voice_input, capcut_voice_input,
                        auto_translate_toggle, session_state],
                outputs=_stt_outs,
            )

            btn_translate_only.click(
                fn=run_translate_only,
                inputs=[session_state],
                outputs=_stt_outs,
            )

            btn_load_json.click(
                fn=run_load_json,
                inputs=[load_json_file, session_state],
                outputs=_stt_outs,
            )

            btn_attach_video.click(
                fn=run_attach_video,
                inputs=[attach_video_input, session_state],
                outputs=[btn_optimize, btn_render, status_attach_video, session_state],
            )

            tts_test_model.change(fn=get_voices, inputs=[tts_test_model], outputs=[tts_test_voice])

            btn_test_tts.click(
                fn=run_test_tts,
                inputs=[tts_test_text, tts_test_model, tts_test_voice, tts_test_capcut],
                outputs=[tts_test_audio],
            )

            translation_table.change(
                fn=refresh_speed_col,
                inputs=[translation_table],
                outputs=[translation_table],
            )

            btn_optimize.click(
                fn=run_optimize,
                inputs=[translation_table, session_state],
                outputs=[translation_table, status_optimize, session_state],
            )

            btn_export_json.click(
                fn=run_export_json,
                inputs=[translation_table, session_state],
                outputs=[json_download],
            )

            btn_render.click(
                fn=run_render,
                inputs=[translation_table, bg_music_input, bg_volume_slider, tts_volume_slider,
                        capcut_delay_slider, capcut_voice_input, capcut_rate_slider,
                        keep_original_check, original_volume_slider, watermark_input, session_state],
                outputs=[video_output, status_render],
            )


        # ══════════════════════════════════════════════════════════════════
        # TAB: LỒNG TIẾNG VIDEO
        # ══════════════════════════════════════════════════════════════════
        with gr.TabItem("🔊 Lồng tiếng"):
            gr.Markdown("## Ghép âm thanh vào video\nGiữ nguyên âm thanh gốc, thêm audio mới đè lên.")
            with gr.Row():
                with gr.Column():
                    dub_video_input = gr.Video(label="Video gốc")
                    dub_audio_input = gr.Audio(label="File âm thanh", type="filepath")
                    dub_audio_vol   = gr.Slider(0.1, 3.0, value=1.0, step=0.05, label="Âm lượng audio mới")
                    dub_btn         = gr.Button("🎬 Ghép âm thanh", variant="primary")
                    dub_status      = gr.Textbox(label="Trạng thái", interactive=False)
                with gr.Column():
                    dub_output = gr.Video(label="Video kết quả")

            def run_dub(video_path, audio_path, audio_vol):
                if not video_path:
                    return None, "❌ Chưa chọn video"
                if not audio_path:
                    return None, "❌ Chưa chọn file âm thanh"
                import subprocess, tempfile
                from translate_video import FFMPEG_BIN as _FFMPEG
                out = Path(tempfile.mkdtemp()) / "dubbed.mp4"
                cmd = [
                    _FFMPEG, "-y",
                    "-i", video_path,
                    "-i", audio_path,
                    "-filter_complex",
                    f"[1:a]volume={audio_vol}[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]",
                    "-map", "0:v",
                    "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    str(out),
                ]
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    return str(out), "✅ Hoàn tất!"
                except subprocess.CalledProcessError as e:
                    return None, f"❌ Lỗi: {e.stderr.decode()[-300:]}"

            dub_btn.click(fn=run_dub,
                          inputs=[dub_video_input, dub_audio_input, dub_audio_vol],
                          outputs=[dub_output, dub_status])


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=8003,
        share=False,
        theme=gr.themes.Soft(),
        head=f"<script>{_BEEP_JS}</script>",
    )
