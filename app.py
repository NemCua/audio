#!/usr/bin/env python3
"""
app.py — Giao diện Gradio cho translate_video.py
Chạy: python3 app.py
Mở:   http://localhost:7860
"""

import os
import shutil
import asyncio
import time
import json
from pathlib import Path

import gradio as gr
import pandas as pd

# Import các hàm từ translate_video
from translate_video import (
    BASE_DIR, OUTPUT_DIR, WORK_DIR,
    extract_audio_for_stt, separate_background,
    stt_groq, translate_srt,
    build_srt, parse_srt, scale_cues,
    build_tts_track, render_video, slowdown_video,
    get_audio_duration, sec_to_srt_time, srt_time_to_sec,
    BEEKNOEE_MODEL,
)

# Load .env
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

OUTPUT_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)

# State toàn cục cho job hiện tại
_state: dict = {}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def get_groq_key(key_input: str) -> str:
    k = (key_input or "").strip() or os.environ.get("GROQ_API_KEY", "")
    if not k or k == "your_groq_key_here":
        raise ValueError("Chưa có Groq API key. Điền vào ô hoặc vào file .env")
    return k


# Tốc độ đọc chuẩn: "tức là để một giáo viên ăn trước bữa ăn của học sinh nửa giờ"
# 14 từ / 3.0 giây ≈ 4.67 từ/giây — đây là mốc "Tốt nhất"
_REF_TEXT = "tức là để một giáo viên ăn trước bữa ăn của học sinh nửa giờ"
IDEAL_WPS = len(_REF_TEXT.split()) / 3.6   # words per second (~3.9 wps, chậm hơn 20%)


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


def cues_to_df(cues: list[dict], zh_cues: list[dict] | None = None) -> pd.DataFrame:
    zh_list = zh_cues or []
    return pd.DataFrame([
        {
            "#": c["idx"],
            "Bắt đầu": c["start"],
            "Kết thúc": c["end"],
            "Tiếng Trung": zh_list[i]["text"] if i < len(zh_list) else "",
            "Bản dịch": c["text"],
            "Tốc độ đọc": reading_speed_label(c["text"], c["start_sec"], c["end_sec"]),
        }
        for i, c in enumerate(cues)
    ])


def df_to_cues(df: pd.DataFrame, original_cues: list[dict]) -> list[dict]:
    """Áp dụng text đã edit từ DataFrame vào cues gốc (giữ nguyên timing)."""
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


def reading_speed_pct(text: str, start_sec: float, end_sec: float) -> float:
    """Trả về % lệch so với chuẩn. Dương = nhanh, âm = chậm."""
    duration = end_sec - start_sec
    if duration <= 0 or not text.strip():
        return 0.0
    return (count_words(text) / duration / IDEAL_WPS - 1) * 100


def refresh_speed_col(df: pd.DataFrame) -> pd.DataFrame:
    """Tính lại cột Tốc độ đọc sau khi user edit bản dịch."""
    rows = df.to_dict("records")
    for row in rows:
        try:
            s = srt_time_to_sec(str(row.get("Bắt đầu", "00:00:00,000")))
            e = srt_time_to_sec(str(row.get("Kết thúc", "00:00:00,000")))
            row["Tốc độ đọc"] = reading_speed_label(str(row.get("Bản dịch", "")), s, e)
        except Exception:
            row["Tốc độ đọc"] = "—"
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tối ưu bản dịch: AI rút ngắn/dài → atempo → giãn timestamp
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = """Bạn là biên tập viên phụ đề tiếng Việt. Nhiệm vụ: viết lại câu cho gần với độ dài mục tiêu (số ký tự) nhưng giữ nguyên nghĩa cốt lõi.
Quy tắc:
- Chỉ trả về câu đã viết lại, không giải thích.
- Không thêm dấu ngoặc kép hay ký tự thừa.
- Giữ văn phong tự nhiên tiếng Việt.
- Nếu cần rút ngắn: bỏ từ đệm, dùng từ ngắn hơn cùng nghĩa.
- Nếu cần dài hơn: thêm từ làm rõ nghĩa, không bịa thêm nội dung."""

ATEMPO_HARD_LIMIT = 1.5   # >50% nhanh hơn: atempo sẽ nghe méo, cần giãn timestamp
WINDOW_BORROW_MAX = 0.4   # mượn tối đa 40% khoảng trống hai bên để giãn timestamp


def ai_rewrite(text: str, target_words: int, groq_key: str, beeknoee_key: str | None = None) -> str:
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
    """
    Với mỗi cue lệch >10%:
      B1: AI viết lại để gần target_chars
      B2: Nếu sau AI vẫn >ATEMPO_HARD_LIMIT → giãn/thu timestamp mượn khoảng trống
    """
    result = [dict(c) for c in cues]
    total  = len(result)
    needs_fix = [i for i, c in enumerate(result)
                 if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]

    if not needs_fix:
        return result

    for step, i in enumerate(needs_fix):
        if progress_cb:
            progress_cb((step + 1) / len(needs_fix), desc=f"Tối ưu đoạn {i+1}/{total}...")

        c       = result[i]
        window      = c["end_sec"] - c["start_sec"]
        ideal_words = max(1, round(IDEAL_WPS * window))

        # B1: AI viết lại
        new_text = ai_rewrite(c["text"], ideal_words, groq_key, beeknoee_key)
        result[i] = {**c, "text": new_text}

        # B2: Kiểm tra sau AI
        pct_after = reading_speed_pct(new_text, c["start_sec"], c["end_sec"])
        if abs(pct_after) <= 10:
            continue  # đã ổn

        # Vẫn lệch nhiều → thử giãn/thu timestamp mượn khoảng trống
        needed_window = count_words(new_text) / IDEAL_WPS
        delta = needed_window - window  # >0 cần thêm thời gian, <0 cần bớt

        prev_end  = result[i-1]["end_sec"]   if i > 0           else 0.0
        next_start= result[i+1]["start_sec"] if i < total - 1   else c["end_sec"] + 10

        gap_before = c["start_sec"] - prev_end
        gap_after  = next_start - c["end_sec"]

        if delta > 0:
            # Cần mở rộng: mượn từ khoảng trống trước/sau
            can_borrow = min(delta,
                             gap_before * WINDOW_BORROW_MAX + gap_after * WINDOW_BORROW_MAX)
            borrow_before = min(gap_before * WINDOW_BORROW_MAX, delta / 2)
            borrow_after  = min(gap_after  * WINDOW_BORROW_MAX, delta - borrow_before)
            new_start = c["start_sec"] - borrow_before
            new_end   = c["end_sec"]   + borrow_after
        else:
            # Cần thu hẹp: co từ hai phía đều nhau
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
        time.sleep(0.3)  # tránh rate limit Groq

    return result


def run_optimize(df: pd.DataFrame, progress=gr.Progress()):
    global _state
    if not _state.get("vi_cues"):
        raise gr.Error("Chưa chạy STT + Dịch.")

    groq_key     = _state.get("groq_key", "")
    beeknoee_key = _state.get("beeknoee_key")
    if not groq_key:
        raise gr.Error("Không tìm thấy Groq API key trong state.")

    cues = df_to_cues(df, _state["vi_cues"])

    needs_fix = [c for c in cues
                 if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]
    if not needs_fix:
        return df, "✅ Tất cả đoạn đã trong ngưỡng tốt, không cần tối ưu."

    optimized = optimize_cues(cues, groq_key, beeknoee_key=beeknoee_key, progress_cb=progress)
    _state["vi_cues"] = optimized

    df_out = cues_to_df(optimized)
    fixed = sum(1 for c in optimized
                if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) <= 10)
    total_fixed = len(needs_fix)
    return df_out, f"✓ Tối ưu xong — {fixed}/{total_fixed} đoạn đã vào ngưỡng tốt"


# ---------------------------------------------------------------------------
# STEP 1: STT + Dịch
# ---------------------------------------------------------------------------

def get_beeknoee_key(key_input: str) -> str | None:
    k = (key_input or "").strip() or os.environ.get("BEEKNOEE_API_KEY", "")
    return k if k else None


def run_stt_translate(video_file, key_input: str, beeknoee_input: str, beeknoee_tts_input: str, beeknoee_tts_voice_input: str, slow_factor: float, progress=gr.Progress()):
    global _state
    _state = {}

    groq_key      = get_groq_key(key_input)
    beeknoee_key  = get_beeknoee_key(beeknoee_input)

    if video_file is None:
        raise gr.Error("Chưa chọn video.")

    video_path = Path(video_file)
    job_id   = f"{video_path.stem}_{int(time.time())}"
    work_dir = WORK_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    _state["work_dir"]           = work_dir
    _state["groq_key"]           = groq_key
    _state["beeknoee_key"]       = beeknoee_key
    _state["beeknoee_tts_model"] = beeknoee_tts_input.strip() or None
    _state["beeknoee_tts_voice"] = beeknoee_tts_voice_input.strip() or None
    _state["slow_factor"]        = slow_factor
    _state["input_tmp"]    = str(video_file)  # path tạm Gradio tạo, xóa sau render

    try:
        # Kéo dãn nếu cần
        if slow_factor != 1.0:
            progress(0.05, desc=f"Kéo dãn video {slow_factor}x...")
            slowed = work_dir / "slowed.mp4"
            video_path = slowdown_video(video_path, slowed, slow_factor)

        _state["video_path"] = video_path

        # Tách audio STT
        progress(0.1, desc="Tách audio...")
        audio_stt = work_dir / "audio_stt.mp3"
        extract_audio_for_stt(video_path, audio_stt)

        # Demucs
        progress(0.2, desc="Tách nhạc nền (Demucs)...")
        bg_track = separate_background(video_path, work_dir)
        _state["bg_track"] = bg_track

        # STT
        progress(0.45, desc="STT Groq Whisper...")
        zh_cues = stt_groq(audio_stt, groq_key)

        _state["zh_cues"] = zh_cues

        # Dịch — lưu cache sau mỗi chunk, cập nhật progress
        provider_name = f"Beeknoee ({BEEKNOEE_MODEL})" if beeknoee_key else "Groq LLaMA"
        cache_path = work_dir / "vi_cues.json"

        def on_chunk_done(done, total, partial):
            pct = 0.65 + (done / total) * 0.15  # 65% → 80%
            progress(pct, desc=f"Dịch {provider_name}: chunk {done}/{total} ({done * 20}/{len(zh_cues)} đoạn)...")
            cache_path.write_text(json.dumps({
                "vi_cues":     partial,
                "video_path":  str(video_path),
                "bg_track":    str(bg_track),
                "slow_factor": slow_factor,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        progress(0.65, desc=f"Dịch Trung → Việt ({provider_name})...")
        vi_cues = translate_srt(zh_cues, groq_key, beeknoee_key=beeknoee_key,
                                chunk_cb=on_chunk_done)

        _state["vi_cues"] = vi_cues

        # Tối ưu AI ngay sau khi dịch
        needs_fix = [c for c in vi_cues
                     if abs(reading_speed_pct(c["text"], c["start_sec"], c["end_sec"])) > 10]
        if needs_fix:
            progress(0.85, desc=f"Tối ưu AI {len(needs_fix)} đoạn lệch tốc độ...")
            vi_cues = optimize_cues(vi_cues, groq_key, beeknoee_key=beeknoee_key,
                                    progress_cb=None)
            _state["vi_cues"] = vi_cues
            # Lưu cache đã tối ưu
            cache_path.write_text(json.dumps({
                "vi_cues":     vi_cues,
                "video_path":  str(video_path),
                "bg_track":    str(bg_track),
                "slow_factor": slow_factor,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        progress(1.0, desc="Xong! Kiểm tra và chỉnh sửa bản dịch bên dưới.")
        df = cues_to_df(vi_cues, _state.get("zh_cues"))
        fixed = len(needs_fix) if needs_fix else 0
        return (
            df,
            gr.update(visible=True, value=df),
            gr.update(visible=True),   # btn_optimize
            gr.update(visible=True),   # btn_render
            f"✓ STT + Dịch + Tối ưu xong — {len(vi_cues)} đoạn ({fixed} đoạn đã tối ưu)",
        )

    except Exception as e:
        if _state.get("work_dir"):
            shutil.rmtree(_state["work_dir"], ignore_errors=True)
        raise gr.Error(str(e))


# ---------------------------------------------------------------------------
# LOAD CACHE
# ---------------------------------------------------------------------------

def run_load_cache(cache_file):
    global _state
    if cache_file is None:
        raise gr.Error("Chưa chọn file cache.")

    cache_path = Path(cache_file)
    if not cache_path.exists():
        raise gr.Error("File không tồn tại.")

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    vi_cues    = data["vi_cues"]
    video_path = Path(data["video_path"])
    bg_track   = Path(data["bg_track"])

    if not video_path.exists():
        raise gr.Error(f"File video không còn tồn tại: {video_path}")
    if not bg_track.exists():
        raise gr.Error(f"File nhạc nền không còn tồn tại: {bg_track}")

    _state["vi_cues"]     = vi_cues
    _state["video_path"]  = video_path
    _state["bg_track"]    = bg_track
    _state["work_dir"]    = cache_path.parent
    _state["slow_factor"] = data.get("slow_factor", 1.0)
    _state["groq_key"]    = _state.get("groq_key", os.environ.get("GROQ_API_KEY", ""))
    _state["beeknoee_key"]= _state.get("beeknoee_key", os.environ.get("BEEKNOEE_API_KEY"))

    df = cues_to_df(vi_cues)
    return (
        df,
        gr.update(visible=True, value=df),
        gr.update(visible=True),
        gr.update(visible=True),
        f"✓ Đã load {len(vi_cues)} đoạn từ cache — sẵn sàng Render",
    )


# ---------------------------------------------------------------------------
# STEP 2: Render
# ---------------------------------------------------------------------------

def run_render(df: pd.DataFrame, progress=gr.Progress()):
    global _state

    if not _state.get("vi_cues"):
        raise gr.Error("Chưa chạy STT + Dịch.")

    video_path = _state["video_path"]
    work_dir   = _state["work_dir"]
    bg_track   = _state["bg_track"]

    # Tạo lại work_dir nếu đã bị xóa (do lỗi lần trước)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Lấy cues đã edit từ table
    vi_cues = df_to_cues(df, _state["vi_cues"])

    # Ghi SRT
    srt_path = work_dir / "captions_vi.srt"
    srt_path.write_text(build_srt(vi_cues), encoding="utf-8")

    try:
        # TTS
        progress(0.1, desc="Tạo TTS tiếng Việt...")
        video_dur = get_audio_duration(video_path)
        tts_track = asyncio.run(build_tts_track(
            vi_cues, work_dir, video_dur,
            beeknoee_key=_state.get("beeknoee_key"),
            beeknoee_tts_model=_state.get("beeknoee_tts_model"),
            beeknoee_tts_voice=_state.get("beeknoee_tts_voice"),
        ))

        # Render
        progress(0.6, desc="Render video...")
        slow = _state.get("slow_factor", 1.0)
        suffix = f"_slow{slow}x" if slow != 1.0 else ""
        stem   = Path(video_path).stem.replace("_slowed", "").replace("slowed", "")
        output_path = OUTPUT_DIR / f"{stem}_vi{suffix}.mp4"

        render_video(video_path, tts_track, bg_track, srt_path, output_path)

        progress(1.0, desc="Hoàn tất!")

        # Dọn dẹp: xóa thư mục làm việc và file input tạm của Gradio
        shutil.rmtree(work_dir, ignore_errors=True)
        input_tmp = _state.get("input_tmp")
        if input_tmp:
            Path(input_tmp).unlink(missing_ok=True)

        return str(output_path), f"✓ Video xuất tại: {output_path.name}"

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

// Theo dõi toast lỗi của Gradio
const observer = new MutationObserver(() => {
    document.querySelectorAll('.toast-wrap .error, .svelte-notification.error').forEach(el => {
        if (!el.dataset.beeped) {
            el.dataset.beeped = '1';
            playErrorBeep();
        }
    });
});
observer.observe(document.body, { childList: true, subtree: true });
"""

with gr.Blocks(title="Dịch Video Tiếng Trung → Tiếng Việt") as demo:
    gr.Markdown("# 🎬 Dịch Video Tiếng Trung → Tiếng Việt")
    gr.Markdown("Upload video → STT + Dịch tự động → Chỉnh sửa bản dịch → Render")

    with gr.Row():
        with gr.Column(scale=2):
            video_input = gr.Video(label="Video tiếng Trung", sources=["upload"])
        with gr.Column(scale=1):
            key_input = gr.Textbox(
                label="Groq API Key (STT)",
                placeholder="gsk_... (để trống nếu đã có trong .env)",
                type="password",
            )
            beeknoee_input = gr.Textbox(
                label="Beeknoee API Key (Dịch + TTS — để trống dùng Groq/Edge TTS)",
                placeholder="sk-bee-... (tùy chọn)",
                type="password",
            )
            beeknoee_tts_input = gr.Textbox(
                label="Beeknoee TTS Model (để trống dùng Edge TTS mặc định)",
                placeholder="google-tts, neural2, wavenet, tts-1...",
            )
            beeknoee_tts_voice_input = gr.Textbox(
                label="Beeknoee TTS Voice (để trống dùng 'vi')",
                placeholder="vi, vi-VN-Neural2-A, vi-VN-WaveNet-A, nova...",
            )
            slow_slider = gr.Slider(
                minimum=1.0, maximum=2.0, value=1.0, step=0.1,
                label="Kéo dãn video (1.0 = giữ nguyên, 1.2 = chậm 20%)",
            )
            btn_stt = gr.Button("▶ Bước 1: STT + Dịch", variant="primary")

    status_stt = gr.Textbox(label="Trạng thái", interactive=False)

    with gr.Accordion("♻️ Khôi phục bản dịch cũ", open=False):
        gr.Markdown("Nếu app bị lỗi sau khi dịch xong, chọn file `vi_cues.json` trong thư mục `workdir/job_xxx/` để tiếp tục Render mà không cần dịch lại.")
        cache_input = gr.File(label="Chọn file vi_cues.json", file_types=[".json"])
        btn_load_cache = gr.Button("📂 Load bản dịch", variant="secondary")

    gr.Markdown("## ✏️ Chỉnh sửa bản dịch")
    gr.Markdown("Sau khi STT + Dịch xong, bảng bên dưới hiện ra — bấm vào ô **Bản dịch** để sửa.")
    translation_table = gr.Dataframe(
        headers=["#", "Bắt đầu", "Kết thúc", "Tiếng Trung", "Bản dịch", "Tốc độ đọc"],
        datatype=["number", "str", "str", "str", "str", "str"],
        column_count=(6, "fixed"),
        interactive=True,
        wrap=True,
        visible=False,
        column_widths=["4%", "9%", "9%", "30%", "30%", "18%"],
    )
    with gr.Row():
        btn_optimize = gr.Button("✨ Tối ưu bản dịch (AI)", variant="secondary", visible=False)
        btn_render   = gr.Button("🎬 Bước 2: Render Video", variant="primary",   visible=False)

    status_optimize = gr.Textbox(label="Trạng thái tối ưu", interactive=False)
    status_render   = gr.Textbox(label="Trạng thái render",  interactive=False)
    video_output    = gr.Video(label="Video kết quả", interactive=False)


    # Events
    btn_stt.click(
        fn=run_stt_translate,
        inputs=[video_input, key_input, beeknoee_input, beeknoee_tts_input, beeknoee_tts_voice_input, slow_slider],
        outputs=[translation_table, translation_table, btn_optimize, btn_render, status_stt],
    )

    btn_load_cache.click(
        fn=run_load_cache,
        inputs=[cache_input],
        outputs=[translation_table, translation_table, btn_optimize, btn_render, status_stt],
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

    btn_render.click(
        fn=run_render,
        inputs=[translation_table],
        outputs=[video_output, status_render],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=8080, share=False,
                theme=gr.themes.Soft(), head=f"<script>{_BEEP_JS}</script>")
