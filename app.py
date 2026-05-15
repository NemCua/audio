#!/usr/bin/env python3
"""
app.py — Giao diện Gradio cho translate_video.py
Chạy: python3 app.py
Mở:   http://localhost:8080
"""

import asyncio
import json
import os
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
    parse_srt, render_video,
    scale_cues, sec_to_srt_time, separate_background,
    slowdown_video, srt_time_to_sec, stt_groq,
    translate_srt,
)

# Load .env
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# State toàn cục
_state: dict = {}

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


_REF_TEXT = "tức là để một giáo viên ăn trước bữa ăn của học sinh nửa giờ"
IDEAL_WPS = len(_REF_TEXT.split()) / 3.6


def count_words(text: str) -> int:
    return len(text.strip().split())


def reading_speed_label(text: str, start_sec: float, end_sec: float) -> str:
    duration = end_sec - start_sec
    if duration <= 0 or not text.strip():
        return "—"
    wps   = count_words(text) / duration
    ratio = wps / IDEAL_WPS
    pct   = (ratio - 1) * 100
    if abs(pct) <= 10:
        return "✅ Tốt nhất"
    elif pct > 0:
        return f"⚡ Nhanh hơn +{pct:.0f}%"
    else:
        return f"🐢 Chậm hơn {pct:.0f}%"


def reading_speed_pct(text: str, start_sec: float, end_sec: float) -> float:
    duration = end_sec - start_sec
    if duration <= 0 or not text.strip():
        return 0.0
    return (count_words(text) / duration / IDEAL_WPS - 1) * 100


def _normalize_cues(raw: list[dict]) -> tuple[list[dict], list[dict]]:
    """Chuẩn hóa cues từ server (zh/vi) hoặc local (text) → (vi_cues, zh_cues)."""
    vi_cues, zh_cues = [], []
    for c in raw:
        vi_text = c.get("vi") or c.get("text", "")
        zh_text = c.get("zh", "")
        vi_cues.append({**c, "text": vi_text})
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
            "Bản dịch": c.get("vi") or c.get("text", ""),
            "Tốc độ đọc": reading_speed_label(
                c.get("vi") or c.get("text", ""), c["start_sec"], c["end_sec"]
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
    d = Path(tempfile.mkdtemp(prefix="vidtrans_"))
    return d


# ---------------------------------------------------------------------------
# OPTIMIZE
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = """Bạn là biên tập viên phụ đề tiếng Việt. Nhiệm vụ: viết lại câu cho gần với độ dài mục tiêu (số từ) nhưng giữ nguyên nghĩa cốt lõi.
Quy tắc:
- Chỉ trả về câu đã viết lại, không giải thích.
- Không thêm dấu ngoặc kép hay ký tự thừa.
- Giữ văn phong tự nhiên tiếng Việt.
- Nếu cần rút ngắn: bỏ từ đệm, dùng từ ngắn hơn cùng nghĩa.
- Nếu cần dài hơn: thêm từ làm rõ nghĩa, không bịa thêm nội dung."""

ATEMPO_HARD_LIMIT = 1.5
WINDOW_BORROW_MAX = 0.4


def ai_rewrite(text: str, target_words: int, groq_key: str, beeknoee_key: str | None) -> str:
    from translate_video import _make_chat_client
    client, model = _make_chat_client(groq_key, beeknoee_key)
    current_words = count_words(text)
    direction = "ngắn hơn" if current_words > target_words else "dài hơn"
    prompt = (
        f"Câu gốc ({current_words} từ): {text}\n"
        f"Yêu cầu: viết lại {direction}, mục tiêu khoảng {target_words} từ. "
        f"Giữ nguyên nghĩa, tự nhiên."
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
    )
    result = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
    return result if result else text


def optimize_cues(cues: list[dict], groq_key: str, beeknoee_key: str | None = None, progress_cb=None) -> list[dict]:
    import time as _time
    result = [dict(c) for c in cues]
    total  = len(result)
    needs_fix = [i for i, c in enumerate(result)
                 if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]
    if not needs_fix:
        return result

    for step, i in enumerate(needs_fix):
        if progress_cb:
            progress_cb((step + 1) / len(needs_fix), desc=f"Tối ưu đoạn {i+1}/{total}...")

        c           = result[i]
        window      = c["end_sec"] - c["start_sec"]
        ideal_words = max(1, round(IDEAL_WPS * window))
        new_text    = ai_rewrite(c["text"], ideal_words, groq_key, beeknoee_key)
        result[i]   = {**c, "text": new_text}

        pct_after = reading_speed_pct(new_text, c["start_sec"], c["end_sec"])
        if abs(pct_after) <= 10:
            continue

        needed_window = count_words(new_text) / IDEAL_WPS
        delta = needed_window - window
        prev_end   = result[i-1]["end_sec"]   if i > 0         else 0.0
        next_start = result[i+1]["start_sec"] if i < total - 1 else c["end_sec"] + 10
        gap_before = c["start_sec"] - prev_end
        gap_after  = next_start - c["end_sec"]

        if delta > 0:
            borrow_before = min(gap_before * WINDOW_BORROW_MAX, delta / 2)
            borrow_after  = min(gap_after  * WINDOW_BORROW_MAX, delta - borrow_before)
            new_start = c["start_sec"] - borrow_before
            new_end   = c["end_sec"]   + borrow_after
        else:
            shrink = abs(delta) / 2
            new_start = c["start_sec"] + shrink
            new_end   = c["end_sec"]   - shrink
            if new_end <= new_start:
                new_start = c["start_sec"]
                new_end   = c["end_sec"]

        result[i] = {
            **result[i],
            "start":     sec_to_srt_time(new_start),
            "end":       sec_to_srt_time(new_end),
            "start_sec": new_start,
            "end_sec":   new_end,
        }
        _time.sleep(0.3)

    return result


def run_optimize(df: pd.DataFrame, progress=gr.Progress()):
    global _state
    if not _state.get("vi_cues"):
        raise gr.Error("Chưa chạy STT + Dịch.")

    groq_key     = _state.get("groq_key", "")
    beeknoee_key = _state.get("beeknoee_key")
    cues         = df_to_cues(df, _state["vi_cues"])

    needs_fix = [c for c in cues
                 if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]
    if not needs_fix:
        return df, "✅ Tất cả đoạn đã trong ngưỡng tốt, không cần tối ưu."

    optimized = optimize_cues(cues, groq_key, beeknoee_key=beeknoee_key, progress_cb=progress)
    _state["vi_cues"] = optimized
    fixed = sum(1 for c in optimized
                if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) <= 10)
    return cues_to_df(optimized, _state.get("zh_cues")), f"✓ Tối ưu xong — {fixed}/{len(needs_fix)} đoạn đã vào ngưỡng tốt"


# ---------------------------------------------------------------------------
# STEP 1: STT + Dịch
# ---------------------------------------------------------------------------

def _save_cache(work_dir: Path, vi_cues: list, video_path, bg_track):
    cache = {
        "video_path": str(video_path),
        "bg_track":   str(bg_track),
        "vi_cues":    vi_cues,
    }
    (work_dir / "vi_cues.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, progress_offset=0.0):
    """Dịch + tối ưu, lưu cache vào work_dir. Trả về vi_cues."""
    provider_name = f"Beeknoee ({BEEKNOEE_MODEL})" if beeknoee_key else "Groq LLaMA"
    video_path    = _state.get("video_path", "")
    bg_track      = _state.get("bg_track", "")

    def on_chunk_done(done, total, partial):
        pct = progress_offset + (done / total) * 0.3
        progress(pct, desc=f"Dịch {provider_name}: chunk {done}/{total} ({done*20}/{len(zh_cues)} đoạn)...")
        _save_cache(work_dir, partial, video_path, bg_track)

    progress(progress_offset, desc=f"Dịch Trung → Việt ({provider_name})...")
    vi_cues = translate_srt(zh_cues, groq_key, beeknoee_key=beeknoee_key, chunk_cb=on_chunk_done)

    needs_fix = [c for c in vi_cues
                 if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]
    if needs_fix:
        progress(progress_offset + 0.3, desc=f"Tối ưu AI {len(needs_fix)} đoạn...")
        vi_cues = optimize_cues(vi_cues, groq_key, beeknoee_key=beeknoee_key, progress_cb=None)
        _save_cache(work_dir, vi_cues, video_path, bg_track)

    return vi_cues, len(needs_fix) if needs_fix else 0


def _stt_outputs(df, vi_cues_exists: bool, translate_done: bool, status: str):
    return (
        df,
        gr.update(visible=True, value=df),
        gr.update(visible=vi_cues_exists),   # btn_optimize
        gr.update(visible=vi_cues_exists),   # btn_export_json
        gr.update(visible=vi_cues_exists),   # btn_render
        gr.update(visible=not translate_done and not vi_cues_exists is False),  # btn_translate_only — show khi STT xong chưa dịch
        status,
    )


def run_stt_translate(video_file, key_input, beeknoee_input, beeknoee_tts_input,
                      beeknoee_tts_voice_input, slow_factor, auto_translate,
                      progress=gr.Progress()):
    global _state
    _state = {}

    groq_key     = get_groq_key(key_input)
    beeknoee_key = get_beeknoee_key(beeknoee_input)

    if video_file is None:
        raise gr.Error("Chưa chọn video.")

    src_path = Path(video_file)
    work_dir = _tmp_dir()
    _state.update({
        "work_dir":           work_dir,
        "groq_key":           groq_key,
        "beeknoee_key":       beeknoee_key,
        "beeknoee_tts_model": beeknoee_tts_input.strip() or None,
        "beeknoee_tts_voice": beeknoee_tts_voice_input.strip() or None,
        "slow_factor":        slow_factor,
    })

    try:
        video_path = src_path
        if slow_factor != 1.0:
            progress(0.05, desc=f"Kéo dãn video {slow_factor}x...")
            slowed = work_dir / "slowed.mp4"
            video_path = slowdown_video(video_path, slowed, slow_factor)

        # Lưu bản copy video vào work_dir để không phụ thuộc path tạm của Gradio
        video_copy = work_dir / ("video" + src_path.suffix)
        shutil.copy2(video_path, video_copy)
        video_path = video_copy
        _state["video_path"] = video_path

        progress(0.2, desc="Tách audio nền...")
        bg_track = separate_background(video_path, work_dir)
        _state["bg_track"] = bg_track

        _state["video_stem"] = src_path.stem

        if not auto_translate:
            return (
                gr.update(visible=False),  # translation_table
                gr.update(visible=False),  # btn_optimize
                gr.update(visible=False),  # btn_export_json
                gr.update(visible=False),  # btn_render
                gr.update(visible=False),  # btn_translate_only
                f"✓ Tách audio xong — chờ JSON từ server",
                gr.update(visible=True, value=src_path.stem),  # video_stem_display
            )

        progress(0.35, desc="Tách audio STT...")
        audio_stt = work_dir / "audio_stt.mp3"
        extract_audio_for_stt(video_path, audio_stt)

        progress(0.5, desc="STT Groq Whisper...")
        zh_cues = stt_groq(audio_stt, groq_key)
        _state["zh_cues"] = zh_cues

        vi_cues, fixed = _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, 0.6)
        _state["vi_cues"] = vi_cues

        progress(1.0, desc="Xong!")
        df = cues_to_df(vi_cues, zh_cues)
        return (
            gr.update(visible=True, value=df),  # translation_table
            gr.update(visible=True),   # btn_optimize
            gr.update(visible=True),   # btn_export_json
            gr.update(visible=True),   # btn_render
            gr.update(visible=False),  # btn_translate_only
            f"✓ STT + Dịch + Tối ưu xong — {len(vi_cues)} đoạn ({fixed} đoạn đã tối ưu)",
            gr.update(visible=False),  # video_stem_display
        )

    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise gr.Error(str(e))


def run_translate_only(progress=gr.Progress()):
    global _state
    if not _state.get("zh_cues"):
        raise gr.Error("Chưa có dữ liệu STT. Chạy Bước 1 trước.")

    groq_key     = _state.get("groq_key", "")
    beeknoee_key = _state.get("beeknoee_key")
    zh_cues      = _state["zh_cues"]
    work_dir     = _state["work_dir"]

    vi_cues, fixed = _do_translate(zh_cues, groq_key, beeknoee_key, work_dir, progress, 0.0)
    _state["vi_cues"] = vi_cues

    progress(1.0, desc="Dịch xong!")
    df = cues_to_df(vi_cues, zh_cues)
    return (
        gr.update(visible=True, value=df),  # translation_table
        gr.update(visible=True),   # btn_optimize
        gr.update(visible=True),   # btn_export_json
        gr.update(visible=True),   # btn_render
        gr.update(visible=False),  # btn_translate_only
        f"✓ Dịch + Tối ưu xong — {len(vi_cues)} đoạn ({fixed} đoạn đã tối ưu)",
        gr.update(visible=False),  # video_stem_display
    )


# ---------------------------------------------------------------------------
# LOAD JSON (từ server hoặc export trước)
# ---------------------------------------------------------------------------

def run_load_json(json_file):
    global _state

    if json_file is None:
        raise gr.Error("Chưa chọn file JSON.")

    raw = json.loads(Path(json_file).read_text(encoding="utf-8"))

    if isinstance(raw, list):
        raise gr.Error("File JSON này không có thông tin video. Dùng file vi_cues.json được tạo từ app.")

    vi_cues, zh_cues = _normalize_cues(raw.get("vi_cues", []))
    video_path = Path(raw["video_path"])
    bg_track   = Path(raw["bg_track"])

    missing = []
    if not video_path.exists():
        missing.append(f"video: {video_path}")
    if not bg_track.exists():
        missing.append(f"nhạc nền: {bg_track}")
    if missing:
        raise gr.Error("File không còn tồn tại:\n" + "\n".join(missing))

    _state["vi_cues"]      = vi_cues
    _state["zh_cues"]      = zh_cues
    _state["video_path"]   = video_path
    _state["bg_track"]     = bg_track
    _state["work_dir"]     = video_path.parent
    _state["slow_factor"]  = raw.get("slow_factor", 1.0)
    _state["groq_key"]     = _state.get("groq_key", os.environ.get("GROQ_API_KEY", ""))
    _state["beeknoee_key"] = _state.get("beeknoee_key", os.environ.get("BEEKNOEE_API_KEY"))

    df = cues_to_df(vi_cues, zh_cues or None)
    stem = video_path.stem
    return (
        gr.update(visible=True, value=df),
        gr.update(visible=True),   # btn_optimize
        gr.update(visible=True),   # btn_export_json
        gr.update(visible=True),   # btn_render
        gr.update(visible=False),  # btn_translate_only
        f"✓ Load {len(vi_cues)} đoạn — video: {stem}",
        gr.update(visible=False),  # video_stem_display
    )


# ---------------------------------------------------------------------------
# EXPORT JSON
# ---------------------------------------------------------------------------

def run_export_json(df: pd.DataFrame):
    global _state
    if not _state.get("vi_cues") and not _state.get("zh_cues"):
        raise gr.Error("Chưa có bản dịch để xuất.")

    vi_cues = df_to_cues(df, _state.get("vi_cues", []))
    zh_map  = {c["idx"]: c["text"] for c in _state.get("zh_cues", [])}

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
# TEST TTS
# ---------------------------------------------------------------------------

def run_test_tts(text: str, tts_model: str, tts_voice: str):
    if not text.strip():
        raise gr.Error("Nhập text để test.")

    beeknoee_key = os.environ.get("BEEKNOEE_API_KEY") or _state.get("beeknoee_key")
    if not beeknoee_key:
        raise gr.Error("Chưa có Beeknoee API key.")

    from openai import OpenAI
    client = OpenAI(api_key=beeknoee_key, base_url=BEEKNOEE_BASE_URL)
    resp = client.audio.speech.create(
        model=tts_model.strip() or "google/google-tts",
        voice=tts_voice.strip() or "vi",
        input=text.strip(),
        response_format="mp3",
    )
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.write(resp.content)
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# RENDER
# ---------------------------------------------------------------------------

def run_render(df: pd.DataFrame, bg_volume: float, tts_volume: float, progress=gr.Progress()):
    global _state

    if not _state.get("vi_cues"):
        raise gr.Error("Chưa có bản dịch.")
    if not _state.get("video_path"):
        raise gr.Error("Chưa có video. Upload video trong mục 'Load JSON + Video'.")
    if not _state.get("bg_track"):
        raise gr.Error("Chưa tách nhạc nền.")

    video_path = _state["video_path"]
    work_dir   = _state["work_dir"]
    bg_track   = _state["bg_track"]
    work_dir.mkdir(parents=True, exist_ok=True)

    vi_cues  = df_to_cues(df, _state["vi_cues"])
    srt_path = work_dir / "captions_vi.srt"
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

    try:
        progress(0.1, desc="Tạo TTS tiếng Việt...")
        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(
            vi_cues, work_dir, video_dur,
            beeknoee_key=_state.get("beeknoee_key"),
            beeknoee_tts_model=_state.get("beeknoee_tts_model"),
            beeknoee_tts_voice=_state.get("beeknoee_tts_voice"),
        ))

        progress(0.6, desc="Render video...")
        slow   = _state.get("slow_factor", 1.0)
        suffix = f"_slow{slow}x" if slow != 1.0 else ""
        stem   = Path(video_path).stem.replace("_slowed", "").replace("slowed", "")
        output_path = work_dir / f"{stem}_vi{suffix}.mp4"

        render_video(video_path, tts_track, bg_track, srt_path, output_path, bg_volume, tts_volume)
        progress(1.0, desc="Hoàn tất!")

        return str(output_path), f"✓ Render xong — bấm tải về bên dưới"

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
const observer = new MutationObserver(() => {
    document.querySelectorAll('.toast-wrap .error, .svelte-notification.error').forEach(el => {
        if (!el.dataset.beeped) { el.dataset.beeped = '1'; playErrorBeep(); }
    });
});
observer.observe(document.body, { childList: true, subtree: true });
"""

TTS_MODELS = [
    ("🆓 Google TTS Free ($0)",         "google/google-tts"),
    ("Google Standard ($4/1M)",          "google/standard"),
    ("Google WaveNet ($16/1M)",          "google/wavenet"),
    ("Google Neural2 ($16/1M)",         "google/neural2"),
    ("Google Chirp 3 HD ($30/1M)",      "google/chirp3-hd"),
    ("OpenAI TTS-1 ($15/1M)",           "openai/tts-1"),
    ("OpenAI TTS-1 HD ($30/1M)",        "openai/tts-1-hd"),
    ("OpenAI GPT-4o Mini TTS ($12/1M)", "openai/gpt-4o-mini-tts"),
]

TTS_VOICES = {
    "google/google-tts": [("🇻🇳 Tiếng Việt", "vi"), ("🇺🇸 English", "en"), ("🇨🇳 中文", "zh"), ("🇯🇵 日本語", "ja")],
    "google/standard":   [("Nữ A (miền Bắc)", "vi-VN-Standard-A"), ("Nam B", "vi-VN-Standard-B"), ("Nữ C", "vi-VN-Standard-C"), ("Nam D", "vi-VN-Standard-D")],
    "google/wavenet":    [("Nữ A", "vi-VN-WaveNet-A"), ("Nam B", "vi-VN-WaveNet-B"), ("Nữ C", "vi-VN-WaveNet-C"), ("Nam D", "vi-VN-WaveNet-D")],
    "google/neural2":    [("Nữ A", "vi-VN-Neural2-A"), ("Nam B", "vi-VN-Neural2-B"), ("Nữ C", "vi-VN-Neural2-C"), ("Nam D", "vi-VN-Neural2-D")],
    "google/chirp3-hd":  [("Nữ A", "vi-VN-Chirp-HD-A"), ("Nam B", "vi-VN-Chirp-HD-B"), ("Nữ C", "vi-VN-Chirp-HD-C"), ("Nam D", "vi-VN-Chirp-HD-D"), ("Nữ F", "vi-VN-Chirp-HD-F"), ("Trung tính O", "vi-VN-Chirp-HD-O")],
    "openai/tts-1":      [("nova", "nova"), ("alloy", "alloy"), ("echo", "echo"), ("onyx", "onyx"), ("shimmer", "shimmer"), ("ash", "ash"), ("ballad", "ballad"), ("coral", "coral"), ("sage", "sage"), ("verse", "verse"), ("fable", "fable")],
    "openai/tts-1-hd":   [("nova", "nova"), ("alloy", "alloy"), ("echo", "echo"), ("onyx", "onyx"), ("shimmer", "shimmer"), ("ash", "ash"), ("ballad", "ballad"), ("coral", "coral"), ("sage", "sage"), ("verse", "verse"), ("fable", "fable")],
    "openai/gpt-4o-mini-tts": [("nova", "nova"), ("alloy", "alloy"), ("echo", "echo"), ("onyx", "onyx"), ("shimmer", "shimmer"), ("ash", "ash"), ("ballad", "ballad"), ("coral", "coral"), ("sage", "sage"), ("verse", "verse"), ("fable", "fable")],
}


def get_voices(model: str):
    voices = TTS_VOICES.get(model, [("vi", "vi")])
    return gr.update(choices=voices, value=voices[0][1])


with gr.Blocks(title="Dịch Video Tiếng Trung → Tiếng Việt") as demo:
    gr.Markdown("# Dịch Video Tiếng Trung → Tiếng Việt")

    # ── PHẦN 1: Upload video + chạy pipeline ──────────────────────────────
    gr.Markdown("## Bước 1 — Upload & Xử lý")
    with gr.Row():
        with gr.Column(scale=2):
            video_input = gr.Video(label="Upload video tiếng Trung", sources=["upload"])
        with gr.Column(scale=1):
            key_input = gr.Textbox(
                label="Groq API Key (STT)",
                placeholder="gsk_... (để trống nếu đã có trong .env)",
                type="password",
            )
            beeknoee_input = gr.Textbox(
                label="Beeknoee API Key (Dịch + TTS)",
                placeholder="sk-bee-... (tùy chọn)",
                type="password",
            )
            beeknoee_tts_input = gr.Dropdown(
                label="TTS Model",
                choices=[("-- Edge TTS mặc định --", "")] + [(l, v) for l, v in TTS_MODELS],
                value="",
            )
            beeknoee_tts_voice_input = gr.Dropdown(
                label="TTS Voice",
                choices=[("vi", "vi")],
                value="vi",
            )
            slow_slider = gr.Slider(
                minimum=1.0, maximum=2.0, value=1.0, step=0.1,
                label="Kéo dãn video (1.0 = giữ nguyên)",
            )
            auto_translate_toggle = gr.Checkbox(
                label="Tự động STT + Dịch sau khi tách audio",
                value=True,
                info="Tắt = chỉ tách audio nền, dừng lại chờ JSON bản dịch từ server",
            )
            btn_stt = gr.Button("▶ Chạy Bước 1", variant="primary")

    status_stt = gr.Textbox(label="Trạng thái", interactive=False)
    video_stem_display = gr.Textbox(interactive=False, visible=False)

    # ── PHẦN 2: Khôi phục từ cache ────────────────────────────────────────
    with gr.Accordion("♻️ Khôi phục bản dịch (vi_cues.json)", open=False):
        gr.Markdown("Load file `vi_cues.json` đã lưu trước đó để tiếp tục chỉnh sửa hoặc render lại.")
        load_json_file = gr.File(label="Chọn file vi_cues.json", file_types=[".json"])
        btn_load_json  = gr.Button("📂 Load", variant="secondary")

    # ── PHẦN 3: Test TTS ───────────────────────────────────────────────────
    with gr.Accordion("🔊 Test giọng đọc TTS", open=False):
        tts_test_text = gr.Textbox(label="Text thử", placeholder="Nhập câu tiếng Việt...")
        with gr.Row():
            tts_test_model = gr.Dropdown(
                label="Model", choices=[(l, v) for l, v in TTS_MODELS],
                value="google/google-tts", scale=1,
            )
            tts_test_voice = gr.Dropdown(
                label="Voice", choices=TTS_VOICES["google/google-tts"],
                value="vi", scale=1,
            )
            btn_test_tts = gr.Button("▶ Test", variant="secondary", scale=1)
        tts_test_audio = gr.Audio(label="Kết quả", interactive=False)

    # ── PHẦN 4: Bảng dịch + nút hành động ─────────────────────────────────
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
        bg_volume_slider  = gr.Slider(0.0, 2.0, value=0.3, step=0.05, label="Âm lượng audio gốc")
        tts_volume_slider = gr.Slider(0.0, 3.0, value=1.8, step=0.05, label="Âm lượng lồng tiếng")

    with gr.Row():
        btn_optimize    = gr.Button("✨ Tối ưu AI",       variant="secondary", visible=False)
        btn_export_json = gr.Button("💾 Xuất JSON",        variant="secondary", visible=False)
        btn_render      = gr.Button("🎬 Render Video",     variant="primary",   visible=False)

    json_download   = gr.File(label="Tải JSON bản dịch", visible=False, interactive=False)
    status_optimize = gr.Textbox(label="Trạng thái tối ưu", interactive=False)
    status_render   = gr.Textbox(label="Trạng thái render",  interactive=False)
    video_output    = gr.Video(label="Video kết quả (bấm tải về)", interactive=False)

    # ── Events ────────────────────────────────────────────────────────────
    _stt_outputs_list = [
        translation_table,
        btn_optimize, btn_export_json, btn_render, btn_translate_only,
        status_stt, video_stem_display,
    ]

    btn_stt.click(
        fn=run_stt_translate,
        inputs=[video_input, key_input, beeknoee_input, beeknoee_tts_input,
                beeknoee_tts_voice_input, slow_slider, auto_translate_toggle],
        outputs=_stt_outputs_list,
    )

    btn_translate_only.click(
        fn=run_translate_only,
        inputs=[],
        outputs=_stt_outputs_list,
    )

    btn_load_json.click(
        fn=run_load_json,
        inputs=[load_json_file],
        outputs=_stt_outputs_list,
    )

    beeknoee_tts_input.change(fn=get_voices, inputs=[beeknoee_tts_input], outputs=[beeknoee_tts_voice_input])
    tts_test_model.change(fn=get_voices, inputs=[tts_test_model], outputs=[tts_test_voice])

    btn_test_tts.click(
        fn=run_test_tts,
        inputs=[tts_test_text, tts_test_model, tts_test_voice],
        outputs=[tts_test_audio],
    )

    translation_table.change(
        fn=refresh_speed_col,
        inputs=[translation_table],
        outputs=[translation_table],
    )

    btn_optimize.click(
        fn=run_optimize,
        inputs=[translation_table],
        outputs=[translation_table, status_optimize],
    )

    btn_export_json.click(
        fn=run_export_json,
        inputs=[translation_table],
        outputs=[json_download],
    )

    btn_render.click(
        fn=run_render,
        inputs=[translation_table, bg_volume_slider, tts_volume_slider],
        outputs=[video_output, status_render],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=8080,
        share=False,
        theme=gr.themes.Soft(),
        head=f"<script>{_BEEP_JS}</script>",
    )
