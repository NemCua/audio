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

    prompt = f'''You are a professional video editor creating B-roll shot lists for a narrated documentary.

STEP 1 — Read the ENTIRE content carefully and identify:
- The MAIN TOPIC (e.g. astronomy, history, biology, technology, economics...)
- Key visual moments in each sentence
- The emotional tone (dramatic, calm, educational, exciting...)

STEP 2 — For each sentence/idea, choose a search query that:
- Captures the SPECIFIC visual described (not just the topic)
- Uses concrete, searchable English terms that stock footage sites have
- Avoids abstract concepts — think "what does the camera actually show?"

Examples of GOOD queries (specific, visual):
- "black hole accretion disk spinning" (not just "black hole")
- "neutron star explosion supernova" (not "space explosion")
- "astronaut spacewalk earth orbit" (not "astronaut")
- "ancient rome colosseum crowd" (not "ancient history")
- "DNA double helix rotating" (not "biology")
- "stock market traders floor panic" (not "economy")

Rules:
- duration: 4-8 seconds per scene
- query: 3-5 English words, highly specific and visual
- ALL queries must relate to the main topic — no random B-roll
- Every query MUST be DIFFERENT — no repeats
- Prefer motion: spinning, flowing, exploding, flying, moving...
- Only use "image" type if absolutely no video could exist for that scene

Return ONLY a JSON array, no explanation:
[{{"query": "...", "type": "video", "duration": 5}}, ...]

Content:
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
    start = text.find('[')
    end   = text.rfind(']') + 1
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

async def tts_content(content: str, out_path: Path, voice: str = 'vi-VN-HoaiMyNeural'):
    """Edge TTS toàn bộ content → 1 file MP3."""
    import edge_tts
    await edge_tts.Communicate(content, voice).save(str(out_path))


def capcut_tts_content(content: str, out_path: Path,
                       voice_type: str, resource_id: str, device_id: str,
                       rate: str = '1.0'):
    """CapCut TTS toàn bộ content — chia câu, sync từng câu, ghép lại."""
    import re as _re, random
    from translate_video import capcut_tts_sync

    sentences = [s.strip() for s in _re.split(r'(?<=[.!?।])\s+', content) if s.strip()]
    if not sentences:
        sentences = [content]

    tmp_dir = out_path.parent / 'tts_parts'
    tmp_dir.mkdir(exist_ok=True)
    parts: list[Path] = []
    current_device_id = device_id

    for i, sentence in enumerate(sentences):
        part_path = tmp_dir / f'part_{i:04d}.mp3'
        for attempt in range(5):
            try:
                capcut_tts_sync(sentence, voice_type, resource_id, part_path, current_device_id)
                parts.append(part_path)
                break
            except Exception as e:
                current_device_id = str(random.randint(7000000000000000000, 7999999999999999999))
                print(f'  CapCut TTS câu {i+1} lỗi lần {attempt+1}, đổi device_id: {e}')
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


# ---------------------------------------------------------------------------
# STEP 5: Ghép video theo timeline
# ---------------------------------------------------------------------------

def build_content_video(
    scenes: list[dict],          # [{query, type, duration, clip_path}]
    tts_path: Path,
    output_path: Path,
    work_dir: Path,
):
    """Ghép tất cả clip theo thứ tự, mix TTS audio."""
    tts_dur_result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', str(tts_path)],
        capture_output=True, text=True
    )
    tts_duration = float(tts_dur_result.stdout.strip() or '0')

    # Tổng duration của clips
    total_clip_dur = sum(s['duration'] for s in scenes if s.get('clip_path'))

    # Nếu clips ngắn hơn TTS → lặp lại clips
    clips = [s for s in scenes if s.get('clip_path')]
    concat_clips: list[Path] = []
    accumulated = 0.0
    i = 0
    while accumulated < tts_duration + 1:
        s = clips[i % len(clips)]
        concat_clips.append(Path(s['clip_path']))
        accumulated += s['duration']
        i += 1

    # Tạo concat list
    concat_list = work_dir / 'content_concat.txt'
    concat_list.write_text(
        '\n'.join(f"file '{p.resolve()}'" for p in concat_clips),
        encoding='utf-8'
    )

    # Ghép clips + mix TTS
    subprocess.run([
        FFMPEG_BIN, '-y',
        '-f', 'concat', '-safe', '0', '-i', str(concat_list),
        '-i', str(tts_path),
        '-map', '0:v',
        '-map', '1:a',
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

        is_video = scene['type'] == 'video'
        duration = float(scene.get('duration', 4))

        # Search: Pexels → Pixabay → broad query → ảnh (last resort)
        broad_query = ' '.join(scene['query'].split()[:-1]) or scene['query']

        media_info = search_pexels_video(scene['query'], pexels_key, used_ids)
        if not media_info and pixabay_key:
            media_info = search_pixabay_video(scene['query'], pixabay_key, used_ids)
        if not media_info:
            media_info = search_pexels_video(broad_query, pexels_key, used_ids)
        if not media_info and pixabay_key:
            media_info = search_pixabay_video(broad_query, pixabay_key, used_ids)

        if media_info:
            is_video = True
        else:
            # Ảnh là last resort
            media_info = search_pexels_image(scene['query'], pexels_key, used_ids)
            is_video = False

        if not media_info:
            print(f'  ⚠ Không tìm thấy media cho: {scene["query"]}')
            scene['clip_path'] = None
            continue

        # Download
        ext = '.mp4' if is_video else '.jpg'
        raw_path  = media_dir / f'raw_{i:04d}{ext}'
        clip_path = clips_dir / f'clip_{i:04d}.mp4'

        download_file(media_info['url'], raw_path)

        # Xử lý → clip 1920x1080
        try:
            if is_video:
                actual_dur = min(duration, float(media_info.get('duration', duration)), 8.0)
                process_video_clip(raw_path, clip_path, actual_dur)
                scene['duration'] = actual_dur
            else:
                process_image_clip(raw_path, clip_path, duration)

            scene['clip_path'] = str(clip_path)
            print(f'  ✓ Clip {i+1}: {scene["query"]} ({scene["type"]}, {scene["duration"]}s)')
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors='ignore')[-300:] if e.stderr else ''
            print(f'  ⚠ Lỗi FFmpeg clip {i+1} [{scene["query"]}]: {stderr}')
            scene['clip_path'] = None
        except Exception as e:
            print(f'  ⚠ Lỗi clip {i+1} [{scene["query"]}]: {e}')
            scene['clip_path'] = None

    # 3. TTS
    _progress(0.55, 'Tạo TTS...')
    tts_path = work_dir / 'content_tts.mp3'
    if capcut_voice_type and capcut_resource_id and capcut_device_id:
        capcut_tts_content(content, tts_path, capcut_voice_type, capcut_resource_id,
                           capcut_device_id, rate=capcut_rate)
    else:
        asyncio.run(tts_content(content, tts_path, voice=edge_voice))

    # 4. Ghép video
    _progress(0.75, 'Ghép video...')
    valid_scenes = [s for s in scenes if s.get('clip_path')]
    if not valid_scenes:
        raise RuntimeError('Không có clip nào được tạo thành công.')

    output_path = work_dir / 'content_video.mp4'
    build_content_video(valid_scenes, tts_path, output_path, work_dir)

    _progress(1.0, 'Hoàn tất!')
    return output_path
