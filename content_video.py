#!/usr/bin/env python3
"""
content_video.py
Tạo video từ text content:
  1. AI chia content thành cảnh + keyword Pexels
  2. Download video/ảnh từ Pexels
  3. TTS toàn bộ content (CapCut hoặc Edge TTS)
  4. Ghép video/ảnh theo timeline TTS → output mp4
"""

import asyncio
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

BASE_DIR   = Path(__file__).parent
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
TARGET_W   = 1920
TARGET_H   = 1080
FPS        = 30


# ---------------------------------------------------------------------------
# STEP 1: AI chia cảnh
# ---------------------------------------------------------------------------

def ai_split_scenes(content: str, groq_key: str, beeknoee_key: str = '') -> list[dict]:
    """Dùng Claude Sonnet (Beeknoee) hoặc Groq để chia content thành cảnh B-roll."""
    import requests, json

    prompt = f'''You are an expert science communicator AND cinematic video director.

Your task: read the content deeply, identify EVERY distinct concept, term, phenomenon, or type that needs its own visual explanation, then assign a cinematic B-roll prompt to each one.

KEY RULE — concept splitting:
- If content mentions "Type Ia and Type II supernovae" → TWO scenes: one for Type Ia, one for Type II
- If content explains "black holes have two types: stellar and supermassive" → TWO scenes
- If content lists steps of a process → one scene per step
- If a sentence introduces a new technical term, mechanism, or named phenomenon → it gets its OWN scene
- Do NOT merge different concepts into one scene just to save scenes
- A 500-word article should typically produce 8-15 scenes minimum

For each scene, write a cinematic visual prompt that:
- Visualizes EXACTLY that one concept — be specific, not generic
- Uses real visual analogies when abstract: spaghettification → show stretching object near black hole (NOT spaghetti food)
- Uses cinematic language: camera angles, lighting, motion, atmosphere
- Is achievable as stock footage or AI video (no real faces, no text overlays)

Stock footage search query rules (query field):
- Must be in ENGLISH, 2-4 words
- Must describe what a camera would actually capture
- Example: concept="spaghettification" → query="object stretching gravity" NOT "spaghetti noodles"
- Example: concept="Type Ia supernova" → query="white dwarf explosion space"
- Example: concept="Type II supernova" → query="massive star core collapse"

Return ONLY a JSON array, no markdown, no explanation:
[{{"query": "stock footage search term", "prompt": "full cinematic visual description...", "duration": 6}}, ...]

Content to analyze:
{content}'''

    # Dùng Claude Sonnet qua Beeknoee nếu có key — hiểu ngữ cảnh tốt hơn
    if beeknoee_key:
        r = requests.post(
            'https://platform.beeknoee.com/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {beeknoee_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'claude-sonnet-4-6',
                'max_tokens': 4096,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=120,
        )
    else:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
            },
            timeout=60,
        )
    r.raise_for_status()
    text = r.json()['choices'][0]['message']['content'].strip()
    # Strip markdown code block nếu có
    if '```' in text:
        text = re.sub(r'```(?:json)?\s*', '', text).strip()
    start = text.find('[')
    end   = text.rfind(']') + 1

    if start == -1 or end == 0:
        # Beeknoee không trả JSON (hết tiền?) — fallback Groq
        print(f'  ⚠ Beeknoee không trả JSON: {text[:100]} — fallback Groq...')
        if not groq_key:
            raise RuntimeError('Không có GROQ_API_KEY để fallback')
        r2 = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
            },
            timeout=60,
        )
        r2.raise_for_status()
        text = r2.json()['choices'][0]['message']['content'].strip()
        start = text.find('[')
        end   = text.rfind(']') + 1
        if start == -1 or end == 0:
            raise RuntimeError(f'Groq cũng không trả JSON: {text[:200]}')

    scenes = json.loads(text[start:end])
    return scenes


# ---------------------------------------------------------------------------
# STEP 2: Download media từ Pexels
# ---------------------------------------------------------------------------

def search_pexels_video(query: str, pexels_key: str, used_ids: set,
                        page: int = 1) -> dict | None:
    import requests
    r = requests.get(
        'https://api.pexels.com/videos/search',
        headers={'Authorization': pexels_key},
        params={'query': query, 'per_page': 15, 'orientation': 'landscape',
                'page': page},
        timeout=15,
    )
    for v in r.json().get('videos', []):
        if v['id'] not in used_ids:
            files = sorted(
                [f for f in v['video_files'] if 640 <= f.get('width', 0) <= 1280],
                key=lambda f: f.get('width', 0), reverse=True,
            )
            if not files:
                files = sorted(
                    [f for f in v['video_files'] if f.get('width', 0) > 0],
                    key=lambda f: f.get('width', 0),
                )
            if files:
                used_ids.add(v['id'])
                return {'url': files[0]['link'], 'duration': v['duration'], 'id': v['id']}
    return None


def search_pexels_image(query: str, pexels_key: str, used_ids: set) -> dict | None:
    import requests
    r = requests.get(
        'https://api.pexels.com/v1/search',
        headers={'Authorization': pexels_key},
        params={'query': query, 'per_page': 5, 'orientation': 'landscape'},
        timeout=15,
    )
    for p in r.json().get('photos', []):
        if p['id'] not in used_ids:
            used_ids.add(p['id'])
            return {'url': p['src']['large2x'], 'id': p['id']}
    return None


def search_pixabay_video(query: str, pixabay_key: str, used_ids: set) -> dict | None:
    """Search Pixabay video — fallback khi Pexels không có kết quả."""
    import requests
    r = requests.get(
        'https://pixabay.com/api/videos/',
        params={
            'key': pixabay_key,
            'q': query,
            'per_page': 15,
            'order': 'popular',
        },
        timeout=15,
    )
    for v in r.json().get('hits', []):
        vid_id = f"pixabay_{v['id']}"
        if vid_id not in used_ids:
            # Ưu tiên small (640px) để download nhanh
            video = v.get('videos', {})
            stream = (video.get('small') or video.get('medium') or
                      video.get('tiny') or video.get('large') or {})
            url = stream.get('url', '')
            if url:
                used_ids.add(vid_id)
                return {'url': url, 'duration': v['duration'], 'id': vid_id}
    return None


def generate_wan2_video(prompt: str, dst: Path, server_url: str, steps: int = 20) -> bool:
    """Gọi Wan2.1 server (Windows) để generate video. Trả về True nếu thành công."""
    import requests

    server_url = server_url.rstrip('/')

    # Submit job
    r = requests.post(f'{server_url}/generate',
                      json={'prompt': prompt, 'steps': steps},
                      timeout=30)
    if not r.ok:
        print(f'  ✗ Wan2.1 submit lỗi: {r.status_code} {r.text[:200]}')
        return False

    job_id = r.json().get('job_id')
    if not job_id:
        return False

    print(f'  ⏳ Wan2.1 job {job_id[:8]}... đang xử lý')

    # Poll tối đa 15 phút
    for _ in range(180):
        time.sleep(5)
        st = requests.get(f'{server_url}/status/{job_id}', timeout=15)
        data = st.json()
        st_val = data.get('status', '')
        progress = data.get('progress', 0)
        print(f'     progress={progress}%')
        if st_val == 'done':
            dl = requests.get(f'{server_url}/video/{job_id}', stream=True, timeout=120)
            dl.raise_for_status()
            with open(dst, 'wb') as f:
                for chunk in dl.iter_content(65536):
                    if chunk:
                        f.write(chunk)
            print(f'  ✓ Wan2.1 xong → {dst.name}')
            return True
        elif st_val == 'failed':
            print(f'  ✗ Wan2.1 failed: {data.get("error", "")}')
            return False

    print('  ✗ Wan2.1 timeout sau 15 phút')
    return False


def download_file(url: str, dst: Path):
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, timeout=60)
            r.raise_for_status()
            with open(dst, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            return
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)


# ---------------------------------------------------------------------------
# STEP 3: Xử lý từng clip
# ---------------------------------------------------------------------------

def process_video_clip(src: Path, dst: Path, duration: float):
    """Cắt video đúng duration, scale về 1920x1080."""
    subprocess.run([
        FFMPEG_BIN, '-y',
        '-i', str(src),
        '-t', str(duration),
        '-vf', f'scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,'
               f'pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,'
               f'setsar=1',
        '-r', str(FPS),
        '-an',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        str(dst),
    ], check=True, capture_output=True)


def process_image_clip(src: Path, dst: Path, duration: float):
    """Ảnh tĩnh + zoom in nhẹ trong duration giây."""
    frames = int(duration * FPS)
    subprocess.run([
        FFMPEG_BIN, '-y',
        '-loop', '1', '-i', str(src),
        '-vf', f'scale=iw*1.2:ih*1.2:force_original_aspect_ratio=increase,'
               f'crop={TARGET_W}:{TARGET_H},'
               f'zoompan=z=\'min(zoom+0.002,1.15)\':'
               f'x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':'
               f'd={frames}:s={TARGET_W}x{TARGET_H}:fps={FPS},'
               f'setsar=1',
        '-frames:v', str(frames),
        '-r', str(FPS),
        '-an',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        str(dst),
    ], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# STEP 4: TTS toàn bộ content
# ---------------------------------------------------------------------------

def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip() or '0')


def _split_sentences(content: str) -> list[str]:
    import re as _re
    sentences = [s.strip() for s in _re.split(r'(?<=[.!?।…])\s+', content) if s.strip()]
    return sentences or [content]


async def tts_content(content: str, out_path: Path, voice: str = 'vi-VN-HoaiMyNeural') -> list[tuple[str, float]]:
    """Edge TTS từng câu → ghép lại, trả về [(sentence, duration), ...]."""
    import edge_tts

    sentences = _split_sentences(content)
    tmp_dir = out_path.parent / 'tts_parts'
    tmp_dir.mkdir(exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []

    for i, sentence in enumerate(sentences):
        part_path = tmp_dir / f'part_{i:04d}.mp3'
        await edge_tts.Communicate(sentence, voice).save(str(part_path))
        dur = _probe_duration(part_path)
        parts.append(part_path)
        durations.append(dur)

    concat_list = tmp_dir / 'parts.txt'
    concat_list.write_text('\n'.join(f"file '{p.resolve()}'" for p in parts), encoding='utf-8')
    subprocess.run([
        FFMPEG_BIN, '-y', '-f', 'concat', '-safe', '0',
        '-i', str(concat_list),
        '-acodec', 'libmp3lame', '-b:a', '128k',
        str(out_path),
    ], check=True, capture_output=True)

    return list(zip(sentences, durations))


def capcut_tts_content(content: str, out_path: Path,
                       voice_type: str, resource_id: str, device_id: str,
                       rate: str = '1.0') -> list[tuple[str, float]]:
    """CapCut TTS từng câu → ghép lại, trả về [(sentence, duration), ...]."""
    import random
    from translate_video import capcut_tts_sync

    sentences = _split_sentences(content)
    tmp_dir = out_path.parent / 'tts_parts'
    tmp_dir.mkdir(exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []
    current_device_id = device_id

    for i, sentence in enumerate(sentences):
        part_path = tmp_dir / f'part_{i:04d}.mp3'
        for attempt in range(5):
            try:
                capcut_tts_sync(sentence, voice_type, resource_id, part_path, current_device_id)
                dur = _probe_duration(part_path)
                parts.append(part_path)
                durations.append(dur)
                break
            except Exception as e:
                current_device_id = str(random.randint(7000000000000000000, 7999999999999999999))
                print(f'  CapCut TTS câu {i+1} lỗi lần {attempt+1}: {e}')
                time.sleep(2)

    if not parts:
        raise RuntimeError('CapCut TTS thất bại hoàn toàn')

    concat_list = tmp_dir / 'parts.txt'
    concat_list.write_text('\n'.join(f"file '{p.resolve()}'" for p in parts if p.exists()), encoding='utf-8')
    subprocess.run([
        FFMPEG_BIN, '-y', '-f', 'concat', '-safe', '0',
        '-i', str(concat_list),
        '-acodec', 'libmp3lame', '-b:a', '128k',
        str(out_path),
    ], check=True, capture_output=True)

    return list(zip(sentences, durations))


# ---------------------------------------------------------------------------
# STEP 5: Ghép video theo timeline
# ---------------------------------------------------------------------------

def build_content_video(
    scenes: list[dict],
    tts_path: Path,
    output_path: Path,
    work_dir: Path,
    sentence_durations: list[tuple[str, float]] | None = None,
):
    """Ghép clips theo timestamp TTS thật từng câu."""
    tts_duration = _probe_duration(tts_path)

    clips = [s for s in scenes if s.get('clip_path')]
    if not clips:
        raise RuntimeError('Không có clip nào hợp lệ')

    # Tính timestamp bắt đầu của từng scene dựa trên TTS duration thật
    # sentence_durations: [(sentence, dur), ...] — map 1-1 với scenes nếu có
    if sentence_durations and len(sentence_durations) >= len(clips):
        # Tính cumulative timestamp cho từng câu
        timestamps: list[float] = []
        t = 0.0
        for _, dur in sentence_durations[:len(clips)]:
            timestamps.append(t)
            t += dur
        # Scene i chiếu từ timestamps[i] đến timestamps[i+1] (hoặc hết TTS)
        scene_durations = []
        for i, clip in enumerate(clips):
            start = timestamps[i]
            end = timestamps[i + 1] if i + 1 < len(timestamps) else tts_duration
            scene_durations.append(max(end - start, 1.0))
    else:
        # Fallback: chia đều theo TTS duration
        per = tts_duration / len(clips)
        scene_durations = [per] * len(clips)

    # Tạo concat list với duration đúng cho từng clip
    concat_list = work_dir / 'content_concat.txt'
    entries = []
    for clip, dur in zip(clips, scene_durations):
        clip_dur = _probe_duration(Path(clip['clip_path']))
        if dur <= clip_dur:
            entries.append(f"file '{Path(clip['clip_path']).resolve()}'\nduration {dur:.3f}")
        else:
            # Clip ngắn hơn thời gian cần → lặp lại
            loops = int(dur / clip_dur) + 1
            for _ in range(loops):
                entries.append(f"file '{Path(clip['clip_path']).resolve()}'")
            # Trim bằng cách ghi duration vào entry cuối
            entries[-1] += f"\nduration {dur % clip_dur or clip_dur:.3f}"

    concat_list.write_text('\n'.join(entries), encoding='utf-8')

    subprocess.run([
        FFMPEG_BIN, '-y',
        '-f', 'concat', '-safe', '0', '-i', str(concat_list),
        '-i', str(tts_path),
        '-map', '0:v', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k',
        '-shortest',
        str(output_path),
    ], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# MAIN pipeline
# ---------------------------------------------------------------------------

def run_content_video_pipeline(
    content: str,
    work_dir: Path,
    groq_key: str,
    pexels_key: str,
    pixabay_key: str = '',
    beeknoee_key: str = '',
    wan2_server_url: str = '',
    capcut_voice_type: str | None = None,
    capcut_resource_id: str | None = None,
    capcut_device_id: str | None = None,
    capcut_rate: str = '1.0',
    edge_voice: str = 'vi-VN-HoaiMyNeural',
    progress_cb=None,
) -> Path:
    def _progress(pct, msg):
        print(f'  [{pct:.0%}] {msg}')
        if progress_cb:
            progress_cb(pct, desc=msg)

    work_dir.mkdir(parents=True, exist_ok=True)
    media_dir = work_dir / 'media'
    media_dir.mkdir(exist_ok=True)
    clips_dir = work_dir / 'clips'
    clips_dir.mkdir(exist_ok=True)

    # 1. AI chia cảnh
    _progress(0.05, 'AI đang phân tích nội dung...')
    scenes = ai_split_scenes(content, groq_key, beeknoee_key=beeknoee_key)
    _progress(0.10, f'Chia được {len(scenes)} cảnh')

    # 2. Download + xử lý từng cảnh
    used_ids: set = set()
    for i, scene in enumerate(scenes):
        _progress(0.10 + 0.40 * i / len(scenes),
                  f'Tải media {i+1}/{len(scenes)}: {scene["query"]}')

        duration = float(scene.get('duration', 6))
        clip_path = clips_dir / f'clip_{i:04d}.mp4'
        raw_path = media_dir / f'raw_{i:04d}'

        # Tìm video: Pexels → Pixabay → broad query
        broad_query = ' '.join(scene['query'].split()[:-1]) or scene['query']
        media_info = search_pexels_video(scene['query'], pexels_key, used_ids)
        if not media_info and pixabay_key:
            media_info = search_pixabay_video(scene['query'], pixabay_key, used_ids)
        if not media_info:
            media_info = search_pexels_video(broad_query, pexels_key, used_ids)
        if not media_info and pixabay_key:
            media_info = search_pixabay_video(broad_query, pixabay_key, used_ids)

        if media_info:
            raw_path = raw_path.with_suffix('.mp4')
            download_file(media_info['url'], raw_path)
            actual_dur = min(duration, float(media_info.get('duration', duration)), 8.0)
            try:
                process_video_clip(raw_path, clip_path, actual_dur)
                scene['duration'] = actual_dur
            except subprocess.CalledProcessError:
                scene['clip_path'] = None
                continue
        else:
            # Ảnh last resort
            img_info = search_pexels_image(scene['query'], pexels_key, used_ids)
            if not img_info:
                print(f'  ⚠ Không có media cho: {scene["query"]}')
                scene['clip_path'] = None
                continue
            raw_path = raw_path.with_suffix('.jpg')
            download_file(img_info['url'], raw_path)
            try:
                process_image_clip(raw_path, clip_path, duration)
            except subprocess.CalledProcessError:
                scene['clip_path'] = None
                continue

        if clip_path.exists():
            scene['clip_path'] = str(clip_path)
            print(f'  ✓ Clip {i+1}: {scene["query"]} ({scene.get("duration",6)}s)')
        else:
            scene['clip_path'] = None

    # 3. TTS
    _progress(0.55, 'Tạo TTS...')
    tts_path = work_dir / 'content_tts.mp3'
    if capcut_voice_type and capcut_resource_id and capcut_device_id:
        sentence_durations = capcut_tts_content(content, tts_path, capcut_voice_type,
                                                capcut_resource_id, capcut_device_id,
                                                rate=capcut_rate)
    else:
        sentence_durations = asyncio.run(tts_content(content, tts_path, voice=edge_voice))

    # 4. Ghép video
    _progress(0.75, 'Ghép video...')
    valid_scenes = [s for s in scenes if s.get('clip_path')]
    if not valid_scenes:
        raise RuntimeError('Không có clip nào được tạo thành công.')

    output_path = work_dir / 'content_video.mp4'
    build_content_video(valid_scenes, tts_path, output_path, work_dir,
                        sentence_durations=sentence_durations)

    _progress(1.0, 'Hoàn tất!')
    return output_path
