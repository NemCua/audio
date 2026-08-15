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
import subprocess
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
    FFMPEG_BIN,
    get_audio_duration,
    render_video,
    srt_time_to_sec,
    stt_groq,
    translate_srt,
    CAPCUT_VOICES_VI, EDGE_VOICES_VI,
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
               watermark: str, tts_speed_ratio: float = 1.0,
               state: dict = None, progress=gr.Progress()):
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
            speed_ratio=tts_speed_ratio,
        ))

        progress(0.6, desc="Render video...")
        output_path = work_dir / f"{Path(video_path).stem}_vi.mp4"
        render_video(video_path, tts_track, srt_path, output_path,
                     bg_music=bg_music, bg_volume=bg_volume, tts_volume=tts_volume,
                     original_audio=video_path if keep_original else None,
                     original_volume=original_volume if keep_original else 0.0,
                     watermark=watermark)
        progress(1.0, desc="Hoàn tất!")
        import tempfile, shutil
        suffix = Path(output_path).suffix
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=None)
        tmp.close()
        shutil.copy2(str(output_path), tmp.name)
        return tmp.name, "✓ Render xong"

    except Exception as e:
        raise gr.Error(str(e))


def run_preview(df: pd.DataFrame, bg_music_file, bg_volume: float, tts_volume: float,
                capcut_delay: float, capcut_voice_sel: str, capcut_rate: float,
                keep_original: bool, original_volume: float,
                watermark: str, tts_speed_ratio: float = 1.0,
                preview_dur: float = 60.0,
                state: dict = None, progress=gr.Progress()):
    """Render preview N giây đầu video."""
    if not state or not state.get("vi_cues"):
        raise gr.Error("Chưa có bản dịch.")
    if not state.get("video_path"):
        raise gr.Error("Chưa có video.")

    import tempfile, shutil
    video_path = Path(state["video_path"]).resolve()
    work_dir   = Path(state["work_dir"]).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # Cắt video gốc lấy N giây đầu
    preview_src = work_dir / "preview_src.mp4"
    progress(0.05, desc=f"Cắt {int(preview_dur)}s đầu...")
    subprocess.run([
        FFMPEG_BIN, "-y", "-ss", "0", "-i", str(video_path),
        "-t", str(preview_dur), "-c", "copy", str(preview_src)
    ], capture_output=True)

    # Lọc cues trong khoảng preview
    vi_cues = df_to_cues(df, state["vi_cues"])
    preview_cues = [c for c in vi_cues if c["start_sec"] < preview_dur]

    srt_path = work_dir / "preview_vi.srt"
    srt_path.write_text(build_srt(preview_cues), encoding="utf-8")

    bg_music    = Path(bg_music_file).resolve() if bg_music_file else None
    capcut_vt   = (capcut_voice_sel or "").strip()
    capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == capcut_vt), None)
    capcut_device_id   = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368") if capcut_info else state.get("capcut_device_id")
    capcut_voice_type  = capcut_info[1] if capcut_info else state.get("capcut_voice_type")
    capcut_resource_id = capcut_info[2] if capcut_info else state.get("capcut_resource_id")

    try:
        progress(0.1, desc="Tạo TTS preview...")
        preview_work = work_dir / "preview_work"
        preview_work.mkdir(exist_ok=True)
        tts_track = asyncio.run(build_tts_track(
            preview_cues, preview_work, preview_dur,
            beeknoee_key=state.get("beeknoee_key"),
            beeknoee_tts_model=state.get("beeknoee_tts_model"),
            beeknoee_tts_voice=state.get("beeknoee_tts_voice"),
            capcut_device_id=capcut_device_id,
            capcut_voice_type=capcut_voice_type,
            capcut_resource_id=capcut_resource_id,
            capcut_delay=capcut_delay,
            capcut_rate=str(round(capcut_rate, 1)),
            speed_ratio=tts_speed_ratio,
        ))

        progress(0.7, desc="Render preview...")
        output_path = work_dir / "preview_out.mp4"
        render_video(preview_src, tts_track, srt_path, output_path,
                     bg_music=bg_music, bg_volume=bg_volume, tts_volume=tts_volume,
                     original_audio=preview_src if keep_original else None,
                     original_volume=original_volume if keep_original else 0.0,
                     watermark=watermark)
        progress(1.0, desc="Preview xong!")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", dir=None)
        tmp.close()
        shutil.copy2(str(output_path), tmp.name)
        return tmp.name, f"✓ Preview {int(preview_dur)}s xong"
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
# CHUNK RENDER LOGIC
# ---------------------------------------------------------------------------

CHUNK_MINUTES = 20  # mỗi chunk 20 phút


def _sec_to_srt_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _split_into_chunks(vi_cues: list[dict], video_dur: float, chunk_secs: float) -> list[dict]:
    chunks = []
    n = 0
    t = 0.0
    while t < video_dur:
        end_t = min(t + chunk_secs, video_dur)
        cues = [c for c in vi_cues if c["start_sec"] < end_t and c["end_sec"] > t]
        chunks.append({
            "idx": n,
            "start_sec": t,
            "end_sec": end_t,
            "label": f"Chunk {n+1} [{int(t//60)}m – {int(end_t//60)}m]",
            "cues": cues,
            "status": "pending",
            "output": None,
        })
        n += 1
        t = end_t
    return chunks


def chunk_plan(state: dict) -> tuple[str, list[dict]]:
    if not state or not state.get("vi_cues"):
        return "<p>⚠️ Chưa load JSON bản dịch.</p>", []
    if not state.get("video_path"):
        return "<p>⚠️ Chưa có video. Load JSON có path hoặc gắn video ở tab Dịch Video.</p>", []
    vi_cues = state["vi_cues"]
    video_path = Path(state["video_path"])
    video_dur = get_audio_duration(video_path)
    chunks = _split_into_chunks(vi_cues, video_dur, CHUNK_MINUTES * 60)
    rows = "".join(
        f"<tr><td>{c['idx']+1}</td><td>{c['label']}</td><td>{len(c['cues'])} cues</td>"
        f"<td>⏳ Chờ</td><td>—</td></tr>"
        for c in chunks
    )
    html = f"""<table style='width:100%;border-collapse:collapse'>
<thead><tr><th>#</th><th>Thời gian</th><th>Số cue</th><th>Trạng thái</th><th>File</th></tr></thead>
<tbody>{rows}</tbody></table>"""
    return html, chunks


def run_chunk_render(
    chunk_state: list,
    bg_music_file,
    bg_volume: float,
    tts_volume: float,
    capcut_delay: float,
    capcut_voice_sel: str,
    capcut_rate: float,
    keep_original: bool,
    original_volume: float,
    watermark: str,
    tts_speed_ratio: float,
    session_state_val: dict,
    progress=gr.Progress(),
) -> tuple[str, list, str]:
    if not chunk_state:
        raise gr.Error("Chưa có danh sách chunk. Nhấn '🔍 Xem Chunks' trước.")
    if not session_state_val or not session_state_val.get("video_path"):
        raise gr.Error("Chưa có video trong session.")

    video_path = Path(session_state_val["video_path"]).resolve()
    work_dir = Path(session_state_val.get("work_dir", tempfile.mkdtemp(prefix="chunk_"))).resolve()
    chunk_dir = work_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    capcut_vt = (capcut_voice_sel or "").strip()
    capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == capcut_vt), None)
    capcut_device_id = (
        os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368")
        if capcut_info else session_state_val.get("capcut_device_id")
    )
    capcut_voice_type = capcut_info[1] if capcut_info else session_state_val.get("capcut_voice_type")
    capcut_resource_id = capcut_info[2] if capcut_info else session_state_val.get("capcut_resource_id")
    bg_music = Path(bg_music_file).resolve() if bg_music_file else None

    total = len(chunk_state)
    for i, chunk in enumerate(chunk_state):
        if chunk.get("status") == "done" and chunk.get("output"):
            progress((i + 1) / total, desc=f"Chunk {i+1}/{total} — đã xong, bỏ qua")
            continue

        progress(i / total, desc=f"Chunk {i+1}/{total}: cắt video...")
        chunk["status"] = "running"

        start_sec = chunk["start_sec"]
        end_sec = chunk["end_sec"]
        dur = end_sec - start_sec

        chunk_src = chunk_dir / f"chunk_{i:03d}_src.mp4"
        ret = subprocess.run([
            FFMPEG_BIN, "-y", "-ss", str(start_sec), "-i", str(video_path),
            "-t", str(dur), "-c", "copy", str(chunk_src),
        ], capture_output=True)
        if ret.returncode != 0:
            chunk["status"] = "error"
            chunk["error"] = ret.stderr.decode()[-200:]
            continue

        cues = chunk["cues"]
        shifted_cues = []
        for c in cues:
            ns = max(0.0, c["start_sec"] - start_sec)
            ne = min(dur, c["end_sec"] - start_sec)
            if ne <= ns:
                continue
            shifted = {**c, "start_sec": ns, "end_sec": ne,
                       "start": _sec_to_srt_ts(ns), "end": _sec_to_srt_ts(ne)}
            shifted_cues.append(shifted)

        if not shifted_cues:
            chunk["status"] = "done"
            chunk["output"] = None
            continue

        srt_path = chunk_dir / f"chunk_{i:03d}.srt"
        srt_path.write_text(build_srt(shifted_cues), encoding="utf-8")

        try:
            progress(i / total + 0.3 / total, desc=f"Chunk {i+1}/{total}: TTS...")
            tts_work = chunk_dir / f"chunk_{i:03d}_work"
            tts_work.mkdir(exist_ok=True)
            tts_track = asyncio.run(build_tts_track(
                shifted_cues, tts_work, dur,
                beeknoee_key=session_state_val.get("beeknoee_key"),
                beeknoee_tts_model=session_state_val.get("beeknoee_tts_model"),
                beeknoee_tts_voice=session_state_val.get("beeknoee_tts_voice"),
                capcut_device_id=capcut_device_id,
                capcut_voice_type=capcut_voice_type,
                capcut_resource_id=capcut_resource_id,
                capcut_delay=capcut_delay,
                capcut_rate=str(round(capcut_rate, 1)),
                speed_ratio=tts_speed_ratio,
            ))

            progress(i / total + 0.7 / total, desc=f"Chunk {i+1}/{total}: render...")
            out_path = chunk_dir / f"chunk_{i:03d}_out.mp4"
            render_video(
                chunk_src, tts_track, srt_path, out_path,
                bg_music=bg_music, bg_volume=bg_volume, tts_volume=tts_volume,
                original_audio=chunk_src if keep_original else None,
                original_volume=original_volume if keep_original else 0.0,
                watermark=watermark,
            )
            chunk["status"] = "done"
            chunk["output"] = str(out_path)
        except Exception as e:
            chunk["status"] = "error"
            chunk["error"] = str(e)

    rows = []
    for c in chunk_state:
        icon = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌"}.get(c["status"], "?")
        out_info = Path(c["output"]).name if c.get("output") else (c.get("error", "")[:40] if c.get("error") else "—")
        rows.append(
            f"<tr><td>{c['idx']+1}</td><td>{c['label']}</td><td>{len(c['cues'])} cues</td>"
            f"<td>{icon} {c['status']}</td><td style='font-size:0.8em'>{out_info}</td></tr>"
        )
    html = f"""<table style='width:100%;border-collapse:collapse'>
<thead><tr><th>#</th><th>Thời gian</th><th>Số cue</th><th>Trạng thái</th><th>File</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""

    done = sum(1 for c in chunk_state if c["status"] == "done")
    errors = sum(1 for c in chunk_state if c["status"] == "error")
    summary = f"✅ {done}/{len(chunk_state)} chunks xong" + (f" | ❌ {errors} lỗi" if errors else "")
    return html, chunk_state, summary


def run_chunk_rerender(chunk_idx: int, chunk_state: list, bg_music_file,
                       bg_volume, tts_volume, capcut_delay, capcut_voice_sel,
                       capcut_rate, keep_original, original_volume, watermark,
                       tts_speed_ratio, session_state_val, progress=gr.Progress()):
    if not chunk_state or chunk_idx < 0 or chunk_idx >= len(chunk_state):
        raise gr.Error("Chunk index không hợp lệ.")
    chunk_state[chunk_idx]["status"] = "pending"
    chunk_state[chunk_idx]["output"] = None
    return run_chunk_render(
        chunk_state, bg_music_file, bg_volume, tts_volume,
        capcut_delay, capcut_voice_sel, capcut_rate,
        keep_original, original_volume, watermark,
        tts_speed_ratio, session_state_val, progress,
    )


def run_merge_chunks(chunk_state: list, progress=gr.Progress()) -> tuple[str, str]:
    if not chunk_state:
        raise gr.Error("Chưa có danh sách chunk.")
    done_chunks = [c for c in chunk_state if c["status"] == "done" and c.get("output")]
    if not done_chunks:
        raise gr.Error("Không có chunk nào hoàn thành.")

    list_file = Path(tempfile.mkdtemp()) / "concat.txt"
    list_file.write_text("\n".join(f"file '{c['output']}'" for c in done_chunks), encoding="utf-8")
    out_path = list_file.parent / "merged_output.mp4"

    progress(0.3, desc=f"Ghép {len(done_chunks)} chunks...")
    ret = subprocess.run([
        FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(out_path),
    ], capture_output=True)
    if ret.returncode != 0:
        raise gr.Error(f"Ghép thất bại: {ret.stderr.decode()[-300:]}")

    progress(1.0, desc="Ghép xong!")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp.close()
    shutil.copy2(str(out_path), tmp.name)
    return tmp.name, f"✅ Ghép xong {len(done_chunks)} chunks"


def _merge_video_info(path: Path) -> dict:
    ffprobe = str(Path(FFMPEG_BIN).with_name("ffprobe"))
    probe = subprocess.run([
        ffprobe, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], capture_output=True, text=True)
    if probe.returncode != 0:
        raise gr.Error(f"Không đọc được {path.name}: {probe.stderr[-300:]}")
    try:
        data = json.loads(probe.stdout)
        video = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        raise gr.Error(f"{path.name} không có luồng video hợp lệ.")
    if duration <= 0 or width <= 0 or height <= 0:
        raise gr.Error(f"Không xác định được thời lượng/kích thước của {path.name}.")
    return {
        "width": width,
        "height": height,
        "duration": duration,
        "has_audio": any(stream.get("codec_type") == "audio" for stream in data["streams"]),
    }


def run_merge_video_files(video_1, video_2, progress=gr.Progress()) -> tuple[str, str]:
    if not video_1 or not video_2:
        raise gr.Error("Hãy chọn đủ Video 1 và Video 2.")
    paths = [Path(value) if isinstance(value, str) else Path(value.name)
             for value in (video_1, video_2)]
    if any(not path.exists() for path in paths):
        raise gr.Error("Không tìm thấy một trong hai file video.")

    work_dir = Path(tempfile.mkdtemp(prefix="merge_videos_")).resolve()
    try:
        progress(0.05, desc="Đang kiểm tra hai video...")
        infos = [_merge_video_info(path) for path in paths]

        # Dùng symlink tên đơn giản để ffconcat đọc được mọi đường dẫn nguồn,
        # nhưng không copy hay mã hóa lại các file video lớn.
        concat_inputs = []
        for index, path in enumerate(paths, start=1):
            link = work_dir / f"input-{index}{path.suffix.lower() or '.mp4'}"
            link.symlink_to(path.resolve())
            concat_inputs.append(link)

        list_file = work_dir / "concat.txt"
        list_file.write_text("\n".join(f"file '{path.name}'" for path in concat_inputs), encoding="utf-8")
        out_path = work_dir / "video-da-ghep.mp4"
        progress(0.35, desc="Đang nối nguyên bản Video 1 và Video 2...")
        result = subprocess.run([
            FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(out_path),
        ], cwd=work_dir, capture_output=True)
        if result.returncode != 0:
            error = result.stderr.decode(errors="replace")[-1200:]
            raise gr.Error(f"Ghép video thất bại: {error}")

        final_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        final_file.close()
        shutil.copy2(out_path, final_file.name)
        total_duration = sum(info["duration"] for info in infos)
        progress(1.0, desc="Ghép xong!")
        return final_file.name, (
            f"✅ Đã ghép nguyên bản Video 1 → Video 2 · không chuẩn hóa/mã hóa lại · "
            f"khoảng {total_duration:.1f} giây"
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
        edge_voices = [(f"{label} — {voice_type}", voice_type) for label, voice_type in EDGE_VOICES_VI]
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
                with gr.Group():
                    gr.Markdown("#### CapCut TTS")
                    tts_test_capcut = gr.Dropdown(
                        label="Chọn giọng CapCut",
                        choices=[("-- Không dùng CapCut --", "")] + [(n, vt) for n, vt, _ in CAPCUT_VOICES_VI],
                        value="BV421_vivn_streaming", scale=1,
                    )
                with gr.Group():
                    gr.Markdown("#### Edge TTS")
                    tts_test_voice = gr.Dropdown(
                        label="Chọn giọng Edge",
                        choices=[(f"{label} — {voice_type}", voice_type) for label, voice_type in EDGE_VOICES_VI],
                        value="vi-VN-HoaiMyNeural",
                    )
                    gr.Markdown("Chọn giọng Edge sẽ tự chuyển khỏi CapCut.")
                with gr.Row():
                    tts_test_model = gr.Dropdown(
                        label="Model khác (tuỳ chọn)",
                        choices=[("-- Edge TTS --", "")] + [(l, v) for l, v in TTS_MODELS],
                        value="", scale=1,
                    )
                    btn_test_tts = gr.Button("▶ Test", variant="secondary", scale=1)
                tts_test_audio = gr.Audio(label="Kết quả", interactive=False)
                tts_test_voice.change(
                    fn=lambda _voice: "",
                    inputs=tts_test_voice,
                    outputs=tts_test_capcut,
                )

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
            with gr.Row():
                tts_speed_slider    = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="⚡ Tốc độ TTS (atempo)")
            with gr.Row():
                keep_original_check    = gr.Checkbox(value=False, label="🔊 Giữ âm thanh gốc")
                original_volume_slider = gr.Slider(0.0, 1.0, value=0.3, step=0.05, label="Âm lượng gốc")
            watermark_input        = gr.Textbox(value="nem_vietsub", label="Tên logo DVD (để trống nếu không muốn)")
            capcut_delay_slider    = gr.Slider(value=1.5, visible=False)
            capcut_rate_slider     = gr.Slider(value=1.0, visible=False)

            with gr.Row():
                btn_optimize    = gr.Button("✨ Tối ưu AI",   variant="secondary", visible=False)
                btn_export_json = gr.Button("💾 Xuất JSON",    variant="secondary", visible=False)
                btn_preview     = gr.Button("👁 Preview",      variant="secondary", visible=False)
                btn_render      = gr.Button("🎬 Render Video", variant="primary",   visible=False)

            preview_dur_slider = gr.Slider(30, 120, value=60, step=10, label="⏱ Thời lượng preview (giây)", visible=False)

            json_download = gr.File(label="Tải JSON bản dịch", visible=False, interactive=False)
            status_optimize = gr.Textbox(label="Trạng thái tối ưu", interactive=False)
            status_render   = gr.Textbox(label="Trạng thái render",  interactive=False)
            video_output    = gr.Video(label="Video kết quả", interactive=False)

            # ── Events ────────────────────────────────────────────────────
            _stt_outs = [
                translation_table,
                btn_optimize, btn_export_json, btn_render, btn_translate_only,
                status_stt, video_stem_display, session_state,
            ]
            _show_preview = lambda *a: (gr.update(visible=True), gr.update(visible=True))

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
            ).then(fn=_show_preview, outputs=[btn_preview, preview_dur_slider])

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

            btn_preview.click(
                fn=run_preview,
                inputs=[translation_table, bg_music_input, bg_volume_slider, tts_volume_slider,
                        capcut_delay_slider, capcut_voice_input, capcut_rate_slider,
                        keep_original_check, original_volume_slider, watermark_input,
                        tts_speed_slider, preview_dur_slider, session_state],
                outputs=[video_output, status_render],
            )

            btn_render.click(
                fn=run_render,
                inputs=[translation_table, bg_music_input, bg_volume_slider, tts_volume_slider,
                        capcut_delay_slider, capcut_voice_input, capcut_rate_slider,
                        keep_original_check, original_volume_slider, watermark_input,
                        tts_speed_slider, session_state],
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

        # ══════════════════════════════════════════════════════════════════
        # TAB: TẠO VIDEO CONTENT
        # ══════════════════════════════════════════════════════════════════
        with gr.TabItem("🎞️ Tạo Video Content"):
            def run_content_video(
                content_text: str,
                cv_capcut_voice: str,
                cv_capcut_rate: float,
                local_images,
                progress=gr.Progress(),
            ):
                if not content_text.strip():
                    raise gr.Error("Chưa nhập nội dung.")

                from content_video import run_content_video_pipeline

                groq_key        = os.environ.get("GROQ_API_KEY", "")
                pexels_key      = os.environ.get("PEXELS_API_KEY", "")
                beeknoee_key    = os.environ.get("BEEKNOEE_API_KEY", "")
                pixabay_key     = os.environ.get("PIXABAY_API_KEY", "")
                wan2_server_url = os.environ.get("WAN2_SERVER_URL", "")

                if not groq_key and not beeknoee_key:
                    raise gr.Error("Chưa có GROQ_API_KEY hoặc BEEKNOEE_API_KEY trong .env")

                # Danh sách ảnh local nếu có upload
                local_image_paths = []
                if local_images:
                    for f in (local_images if isinstance(local_images, list) else [local_images]):
                        p = Path(f) if isinstance(f, str) else Path(f.name)
                        if p.exists():
                            local_image_paths.append(p)

                work_dir = Path(tempfile.mkdtemp(prefix="contentvid_")).resolve()
                capcut_info = next((v for v in CAPCUT_VOICES_VI if v[1] == cv_capcut_voice.strip()), None)
                capcut_device_id = os.environ.get("CAPCUT_DEVICE_ID", "7581502458217252368") if capcut_info else None

                try:
                    out = run_content_video_pipeline(
                        content=content_text.strip(),
                        work_dir=work_dir,
                        groq_key=groq_key,
                        pexels_key=pexels_key,
                        pixabay_key=pixabay_key,
                        beeknoee_key=beeknoee_key,
                        wan2_server_url=wan2_server_url,
                        capcut_voice_type=capcut_info[1] if capcut_info else None,
                        capcut_resource_id=capcut_info[2] if capcut_info else None,
                        capcut_device_id=capcut_device_id,
                        capcut_rate=str(round(cv_capcut_rate, 1)),
                        local_images=local_image_paths,
                        progress_cb=progress,
                    )
                    return str(out), f"✓ Xong — {work_dir.name}"
                except Exception as e:
                    raise gr.Error(str(e))

            with gr.Row():
                with gr.Column(scale=1):
                    cv_voice_input = gr.Dropdown(
                        choices=[("-- Edge TTS --", "")] + [(v[0], v[1]) for v in CAPCUT_VOICES_VI],
                        value="", label="Giọng CapCut TTS (để trống = Edge TTS)",
                    )
                    cv_rate_slider = gr.Slider(0.5, 2.0, value=1.0, step=0.1,
                                               label="Tốc độ đọc CapCut")
                    cv_local_images = gr.File(
                        label="Ảnh từ máy (tùy chọn — dùng thay Pexels/Pixabay)",
                        file_count="multiple",
                        file_types=["image"],
                    )
                    btn_gen_video  = gr.Button("🎞️ Tạo Video", variant="primary")
                    cv_status      = gr.Textbox(label="Trạng thái", interactive=False)
                with gr.Column(scale=2):
                    cv_content = gr.Textbox(
                        label="Nội dung thuyết minh",
                        lines=15,
                        placeholder="Dán nội dung vào đây. Nếu có upload ảnh từ máy sẽ dùng ảnh đó, không thì tìm từ Pexels/Pixabay...",
                    )

            cv_video_output = gr.Video(label="Video xuất ra")

            btn_gen_video.click(
                fn=run_content_video,
                inputs=[cv_content, cv_voice_input, cv_rate_slider, cv_local_images],
                outputs=[cv_video_output, cv_status],
            )

        # ══════════════════════════════════════════════════════════════════
        # TAB: RENDER CHUNK
        # ══════════════════════════════════════════════════════════════════
        with gr.TabItem("🧩 Render Chunk"):
            gr.Markdown(
                "## Render video theo từng chunk 20 phút\n"
                "Load JSON + gắn video ở tab **Dịch Video** trước, sau đó quay lại đây."
            )

            chunk_state_gr = gr.State([])

            with gr.Row():
                btn_chunk_plan = gr.Button("🔍 Xem Chunks", variant="secondary")
                chunk_plan_status = gr.Textbox(label="Trạng thái", interactive=False, scale=3)

            chunk_table_html = gr.HTML("<p>Nhấn '🔍 Xem Chunks' để bắt đầu.</p>")

            gr.Markdown("### Cài đặt render (dùng chung cho tất cả chunks)")
            with gr.Row():
                ck_bg_music  = gr.Audio(label="🎵 Nhạc nền", type="filepath", sources=["upload"])
                ck_bg_vol    = gr.Slider(0.0, 2.0, value=0.3, step=0.05, label="Âm lượng nhạc nền")
                ck_tts_vol   = gr.Slider(0.0, 3.0, value=1.8, step=0.05, label="Âm lượng TTS")
            with gr.Row():
                ck_tts_speed = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="⚡ Tốc độ TTS")
                ck_keep_orig = gr.Checkbox(value=False, label="🔊 Giữ âm thanh gốc")
                ck_orig_vol  = gr.Slider(0.0, 1.0, value=0.3, step=0.05, label="Âm lượng gốc")
            with gr.Row():
                ck_voice     = gr.Dropdown(
                    label="Giọng CapCut TTS",
                    choices=[("-- Edge TTS --", "")] + [(n, vt) for n, vt, _ in CAPCUT_VOICES_VI],
                    value="BV421_vivn_streaming",
                )
                ck_watermark = gr.Textbox(value="nem_vietsub", label="Watermark")
            ck_delay = gr.Slider(value=1.5, visible=False)
            ck_rate  = gr.Slider(value=1.0, visible=False)

            with gr.Row():
                btn_chunk_render = gr.Button("🚀 Render tất cả chunks", variant="primary")
                btn_chunk_merge  = gr.Button("🔗 Ghép tất cả chunks xong", variant="secondary")

            chunk_render_status = gr.Textbox(label="Trạng thái render", interactive=False)

            gr.Markdown("### Làm lại 1 chunk")
            with gr.Row():
                rerender_idx = gr.Number(label="Số thứ tự chunk (bắt đầu từ 1)", value=1, precision=0)
                btn_rerender = gr.Button("↺ Làm lại chunk này", variant="secondary")

            merged_video = gr.Video(label="Video sau khi ghép", interactive=False)

            def _chunk_plan_wrap(state):
                html, chunks = chunk_plan(state)
                n = len(chunks)
                status = f"📋 {n} chunks × {CHUNK_MINUTES} phút" if chunks else "⚠️ Thiếu dữ liệu"
                return html, chunks, status

            btn_chunk_plan.click(
                fn=_chunk_plan_wrap,
                inputs=[session_state],
                outputs=[chunk_table_html, chunk_state_gr, chunk_plan_status],
            )

            btn_chunk_render.click(
                fn=run_chunk_render,
                inputs=[
                    chunk_state_gr,
                    ck_bg_music, ck_bg_vol, ck_tts_vol,
                    ck_delay, ck_voice, ck_rate,
                    ck_keep_orig, ck_orig_vol, ck_watermark,
                    ck_tts_speed, session_state,
                ],
                outputs=[chunk_table_html, chunk_state_gr, chunk_render_status],
            )

            btn_rerender.click(
                fn=lambda idx, *args: run_chunk_rerender(int(idx) - 1, *args),
                inputs=[
                    rerender_idx, chunk_state_gr,
                    ck_bg_music, ck_bg_vol, ck_tts_vol,
                    ck_delay, ck_voice, ck_rate,
                    ck_keep_orig, ck_orig_vol, ck_watermark,
                    ck_tts_speed, session_state,
                ],
                outputs=[chunk_table_html, chunk_state_gr, chunk_render_status],
            )

            btn_chunk_merge.click(
                fn=run_merge_chunks,
                inputs=[chunk_state_gr],
                outputs=[merged_video, chunk_render_status],
            )

        # ══════════════════════════════════════════════════════════════════
        # TAB: GHÉP VIDEO
        # ══════════════════════════════════════════════════════════════════
        with gr.TabItem("🔗 Ghép Video"):
            gr.Markdown(
                "## Ghép 2 video thành 1\n"
                "Video 1 chạy trước, sau đó tới Video 2. Không chuẩn hóa hoặc mã hóa lại; "
                "hai video nên có cùng codec, kích thước và FPS để phát ổn định."
            )
            with gr.Row():
                merge_video_1 = gr.Video(label="Video 1 — chạy trước", sources=["upload"])
                merge_video_2 = gr.Video(label="Video 2 — chạy sau", sources=["upload"])
            btn_merge_files = gr.Button("🔗 Ghép video", variant="primary")
            merge_status    = gr.Textbox(label="Trạng thái", interactive=False)
            merge_output    = gr.Video(label="Video sau ghép", interactive=False)

            btn_merge_files.click(
                fn=run_merge_video_files,
                inputs=[merge_video_1, merge_video_2],
                outputs=[merge_output, merge_status],
            )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=8003,
        share=False,
        theme=gr.themes.Soft(),
        head=f"<script>{_BEEP_JS}</script>",
        show_error=True,
    )
