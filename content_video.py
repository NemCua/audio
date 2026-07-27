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
import shutil
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

    prompt = f'''You are an expert video director creating a B-roll footage plan for a Vietnamese science narration video.

CORE PRINCIPLE
For every scene, answer:
1. What is the viewer HEARING?
2. What do they need to SEE to understand?
3. What FEELING must this moment create?
These override all rules below.

PROCESS

1. Read the entire content first to grasp theme, tone, emotional arc.

2. Split by IDEA, not sentence. One scene = one visual idea, 4 to 10 seconds spoken.
   If one sentence has two ideas, split into two scenes. If three sentences share one idea, merge into one scene.
   Boundaries: topic shift, transition words, tone shift (explain to conclude, describe to question).
   Include the EXACT spoken Vietnamese text in the "text" field.

3. Classify each scene:
   - HOOK: shocking opener, needs strong surprise visual
   - CONCRETE_OBJECT: real filmable thing, direct footage
   - ABSTRACT_CONCEPT: idea or force, visual metaphor
   - HISTORICAL_EVENT: past event, CGI or archive
   - MECHANISM: how it works, animation or close-up sequence
   - DATA: numbers or comparisons, data viz or scale comparison
   - EMOTIONAL_MOMENT: reflective closing, slow wide symbolic shot
   - TRANSITION: connector, light visual

4. Pick visual strategy:
   - DIRECT: show the thing (concrete objects)
   - METAPHOR: represent abstract idea via another image
   - SIMULATION: CGI for too small, too large, too old, or invisible
   - REFLECTION: show effects or traces (wind to grass, gravity to compass)

5. Build keyword tiers using formula:
   [OBJECT] + [STATE or ACTION] + [ENVIRONMENT] + [SHOT STYLE]
   - tier1: ideal, specific, may be hard to find
   - tier2: fallback, still good
   - tier3: rescue, always findable
   Shot styles: close up, aerial, macro, timelapse, slow motion, cgi, cinematic, drone, microscope, 3d animation, wide shot, silhouette.
   Query fields MUST be English. Never Vietnamese.

ABSTRACT TO VISUAL MAPPINGS
- time passing: hourglass, clock hands, calendar pages, timelapse sunrise
- history compressed: clock countdown final seconds, timeline fast scroll
- infinity or vastness: galaxy panorama, ocean horizon, cosmic scale
- insignificance: pale blue dot, tiny human vast landscape, dust in sunlight
- connection: neuron network, forest roots, lightning between clouds
- chaos: boiling water, fire, storm, crowd
- order: crystal growing, snowflake macro, geometric pattern
- new life: seedling sprouting, egg hatching, sunrise
- death or loss: falling leaves, sunset, candle extinguishing, star fading
- civilization: ancient ruins aerial, city timelapse rise fall
- war or conflict: battlefield aerial cinematic, explosion slow motion
- discovery: lightbulb moment, laboratory experiment

QUALITY RULES
1. Motion beats static
2. Specific beats generic (polar bear swimming Arctic beats animal in water)
3. Match visual energy to narration energy
4. Use contrast for opposites
5. Show scale whenever size is mentioned
6. Save strongest visuals for biggest reveals
7. Vary shot types, avoid stacking similar shots

OUTPUT
Return ONLY a valid JSON array, no markdown, no explanation:
[
  {{
    "text": "exact spoken Vietnamese text for this scene",
    "type": "SCENE_TYPE",
    "query": "tier1 english stock footage keywords",
    "query2": "tier2 fallback keywords",
    "query3": "tier3 rescue keywords",
    "query_vi": "nghĩa tiếng Việt của query chính, 3-6 từ",
    "vi_note": "1 câu tiếng Việt giải thích lý do chọn hình",
    "strategy": "DIRECT|METAPHOR|SIMULATION|REFLECTION",
    "duration": 6
  }}
]

Content to analyze:
{content}'''

    # Dùng Claude Sonnet qua Beeknoee nếu có key — hiểu ngữ cảnh tốt hơn
    if beeknoee_key:
        try:
            r = requests.post(
                'https://platform.beeknoee.com/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {beeknoee_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'claude-sonnet-4-6',
                    'max_tokens': 8000,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=90,
            )
            if r.status_code >= 500:
                print(f'  ⚠ Beeknoee lỗi {r.status_code} — fallback Groq...')
                beeknoee_key = None
            else:
                r.raise_for_status()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f'  ⚠ Beeknoee lỗi kết nối ({e.__class__.__name__}) — fallback Groq...')
            beeknoee_key = None
    if not beeknoee_key:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {groq_key}'},
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
                'max_tokens': 8000,
            },
            timeout=120,
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
                'max_tokens': 8000,
            },
            timeout=120,
        )
        r2.raise_for_status()
        text = r2.json()['choices'][0]['message']['content'].strip()
        start = text.find('[')
        end   = text.rfind(']') + 1
        if start == -1 or end == 0:
            raise RuntimeError(f'Groq cũng không trả JSON: {text[:200]}')

    raw = text[start:end] if (start != -1 and end > 0) else text[start:] if start != -1 else text
    # Nếu JSON bị cắt giữa chừng, thử parse phần hợp lệ
    try:
        scenes = json.loads(raw)
    except json.JSONDecodeError:
        # Cắt đến object cuối cùng hoàn chỉnh
        last_brace = raw.rfind('}')
        if last_brace == -1:
            raise RuntimeError(f'Không parse được JSON: {raw[:300]}')
        fixed = raw[:last_brace+1] + ']'
        scenes = json.loads(fixed)
    return _split_long_scenes(scenes)


# Tốc độ đọc trung bình tiếng Việt (từ/giây) — dùng để ước lượng thời lượng thật
# của 1 scene từ số từ trong "text", vì AI đôi khi báo "duration" sai với độ dài text.
WPS_VI = 2.3
MAX_SCENE_SECS = 10.0  # scene dài hơn ngưỡng này sẽ bị tách nhỏ để có đủ hình ảnh


def _split_long_scenes(scenes: list[dict]) -> list[dict]:
    """Nếu 1 scene có text ước lượng đọc quá lâu (AI gộp quá nhiều ý vào 1 scene),
    tách nó thành nhiều scene nhỏ hơn theo dấu câu/dấu phẩy — mỗi phần có ảnh riêng
    thay vì phải đứng hình/lặp lại 1 clip trong lúc đọc quá dài."""
    import re as _re
    result = []
    for scene in scenes:
        text = scene.get('text', '').strip()
        n_words = len(text.split())
        est_secs = n_words / WPS_VI if n_words else 0

        if est_secs <= MAX_SCENE_SECS or n_words < 6:
            result.append(scene)
            continue

        # Tách theo dấu câu trước (., !, ?, ;), nếu vẫn còn phần quá dài thì tách tiếp theo dấu phẩy
        parts = [p.strip() for p in _re.split(r'(?<=[.!?;])\s+', text) if p.strip()]
        chunks: list[str] = []
        for p in parts:
            if len(p.split()) / WPS_VI > MAX_SCENE_SECS:
                sub = [s.strip() for s in _re.split(r'(?<=[,])\s+', p) if s.strip()]
                chunks.extend(sub)
            else:
                chunks.append(p)

        if len(chunks) <= 1:
            result.append(scene)
            continue

        # Gộp các chunk quá ngắn (dự kiến < 3s đọc) vào chunk liền trước, tránh
        # scene vụn vặt kiểu "tạo ra các thiên hà," đứng riêng 1 mình.
        MIN_MERGE_WORDS = int(3.0 * WPS_VI)  # ~3 giây đọc
        merged: list[str] = []
        for c in chunks:
            if merged and len(c.split()) < MIN_MERGE_WORDS:
                merged[-1] = merged[-1] + ' ' + c
            else:
                merged.append(c)

        base_query = scene.get('query', '')
        base_query2 = scene.get('query2', base_query)
        base_query3 = scene.get('query3', base_query2)
        shot_variants = ['wide shot', 'close up', 'cinematic', 'slow motion', 'aerial view']

        for i, part_text in enumerate(merged):
            new_scene = dict(scene)
            new_scene['text'] = part_text
            new_scene['duration'] = max(2.0, round(len(part_text.split()) / WPS_VI, 1))
            # Biến thể query nhẹ cho các phần sau để không tải trùng y hệt 1 ảnh/clip
            if i > 0:
                variant = shot_variants[i % len(shot_variants)]
                new_scene['query'] = f'{base_query} {variant}'.strip()
                new_scene['query2'] = base_query2
                new_scene['query3'] = base_query3
            result.append(new_scene)

    return result


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
    # UA trình duyệt — Wikimedia/nhiều host web chặn UA mặc định của python-requests
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, timeout=60, headers=headers)
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

def process_video_clip(src: Path, dst: Path, duration: float, start: float = 0.0):
    """Cắt video từ giây `start` (mặc định từ đầu), đúng duration, scale về 1920x1080."""
    seek = ['-ss', f'{start:.2f}'] if start and start > 0 else []
    subprocess.run([
        FFMPEG_BIN, '-y',
        *seek,
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
    """Ảnh tĩnh đứng im trong duration giây."""
    frames = int(duration * FPS)
    subprocess.run([
        FFMPEG_BIN, '-y',
        '-loop', '1', '-i', str(src),
        '-vf', f'scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,'
               f'pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,'
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
    """Edge TTS từng câu → ghép lại, trả về [(sentence, duration), ...].
    Chỉ dùng để có duration thô cho fallback — timing chính xác dùng whisper_align_scenes()."""
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


# ---------------------------------------------------------------------------
# Whisper forced alignment: tìm chính xác giây bắt đầu của từng scene
# ---------------------------------------------------------------------------

def _normalize_word(w: str) -> str:
    """Chuẩn hoá để so khớp: bỏ dấu câu, lowercase."""
    return re.sub(r'[^\w\sÀ-ỹ]', '', w).strip().lower()


def whisper_words(audio_path: Path, groq_key: str) -> list[dict]:
    """Chạy Groq Whisper với timestamp_granularities=word trên audio TTS tiếng Việt.
    Trả về [{"word": str, "start": float, "end": float}, ...]."""
    from groq import Groq
    client = Groq(api_key=groq_key)

    with open(audio_path, 'rb') as f:
        resp = client.audio.transcriptions.create(
            file=('audio.mp3', f, 'audio/mpeg'),
            model='whisper-large-v3',
            language='vi',
            response_format='verbose_json',
            timestamp_granularities=['word'],
        )
    words = getattr(resp, 'words', None) or []
    out = []
    for w in words:
        word = w.word if hasattr(w, 'word') else w['word']
        start = w.start if hasattr(w, 'start') else w['start']
        end = w.end if hasattr(w, 'end') else w['end']
        out.append({'word': word, 'start': float(start), 'end': float(end)})
    return out


def _fuzzy_eq(a: str, b: str) -> bool:
    """So khớp lỏng 2 từ đã chuẩn hoá — Whisper hay nhận sai/dính vài ký tự."""
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def _seq_score(scene_words: list[str], norm_words: list[str], pos: int, k: int) -> int:
    """Chấm điểm: đi thử k từ đầu của scene từ vị trí pos trong whisper words,
    đếm được bao nhiêu từ khớp (chịu được Whisper chèn 1 từ thừa)."""
    score, j, n = 0, pos, len(norm_words)
    for i in range(min(k, len(scene_words))):
        if j >= n:
            break
        w = scene_words[i]
        if _fuzzy_eq(w, norm_words[j]):
            score += 1
            j += 1
        elif j + 1 < n and _fuzzy_eq(w, norm_words[j + 1]):
            score += 1
            j += 2
        else:
            j += 1
    return score


def _walk_scene(scene_words: list[str], norm_words: list[str], start_pos: int) -> tuple[list[int], int]:
    """Đi song song từ của script (scene_words) và từ Whisper (norm_words) từ
    start_pos, chịu lỗi Whisper chèn thừa / bỏ / gộp từ (vd 'ba mươi' → '30').
    Trả về (vị trí whisper của TỪNG từ script trong scene,
             vị trí whisper ngay SAU từ cuối của scene — dùng làm cursor)."""
    j, n = start_pos, len(norm_words)
    pos_list: list[int] = []
    for i, w in enumerate(scene_words):
        pos_list.append(min(j, n - 1))
        if j >= n:
            continue
        if _fuzzy_eq(w, norm_words[j]):
            j += 1
        elif j + 1 < n and _fuzzy_eq(w, norm_words[j + 1]):
            j += 2   # Whisper chèn 1 từ thừa — nhảy qua
        elif i + 1 < len(scene_words) and _fuzzy_eq(scene_words[i + 1], norm_words[j]):
            pass     # Whisper bỏ/gộp từ này — whisper đứng yên, script tiến
        else:
            j += 1   # cả hai lệch — tiến 1 để không kẹt, các từ sau sẽ tự bám lại
    return pos_list, j


def whisper_align_scenes(scenes: list[dict], word_timestamps: list[dict],
                         collect_word_times: bool = False):
    """Khớp text của từng scene vào chuỗi word_timestamps để tìm giây bắt đầu thật.
    collect_word_times=True → trả về (starts, word_times) trong đó word_times[i] là
    [(từ gốc script, giây bắt đầu, giây kết thúc), ...] của scene i — dùng cho vietsub.

    Chống trôi (drift): Whisper không phiên âm y hệt script (gộp 'ba mươi' thành '30',
    chèn/bỏ/nghe sai từ) nên KHÔNG được tiến con trỏ theo số từ của script. Thay vào đó:
    - Định vị đầu scene bằng chấm điểm CHUỖI 6 từ đầu (không phải 1 từ), quét trong
      cửa sổ quanh con trỏ, cho phép lùi nhẹ nếu con trỏ đã trôi quá.
    - Đi song song script↔whisper hết scene (_walk_scene) để lấy cursor CHÍNH XÁC
      cho scene kế, thay vì cộng số từ script.
    - Mốc thời gian không được lùi so với scene trước (timeline đơn điệu tăng).

    Scene có 'trigger_idx' (chỉ số từ trong text, tách theo khoảng trắng — cùng cách
    tách với UI): mốc bắt đầu dời tới lúc TTS đọc TỚI từ đó thay vì đầu câu; cảnh
    trước tự động giữ lâu hơn. Vị trí trigger lấy từ chính bước walk trong phạm vi
    scene nên từ trùng ở scene khác không gây nhầm."""
    norm_words = [_normalize_word(w['word']) for w in word_timestamps]
    n = len(norm_words)
    cursor = 0    # vị trí whisper words đã dùng tới đâu
    last_pos = -1 # vị trí đầu của scene trước — không cho scene sau lùi qua
    starts: list[float] = []
    word_times: list[list[tuple[str, float, float]]] = []

    for si, scene in enumerate(scenes):
        raw_tokens = scene.get('text', '').split()
        scene_words = [_normalize_word(w) for w in raw_tokens if _normalize_word(w)]
        if not scene_words or cursor >= n:
            starts.append(word_timestamps[cursor]['start'] if cursor < n
                         else (word_timestamps[-1]['end'] if word_timestamps else 0.0))
            word_times.append([])
            continue

        # Định vị đầu scene: chấm điểm chuỗi K từ đầu tại từng vị trí trong cửa sổ.
        # Cho phép lùi tối đa 8 từ so với cursor (nếu scene trước walk quá tay).
        K = min(6, len(scene_words))
        win_start = max(last_pos + 1, cursor - 8, 0)
        win_end = min(n, cursor + max(30, len(scene_words) * 2))
        # Ưu tiên: điểm chuỗi cao hơn > từ đầu tiên khớp trực tiếp > vị trí sớm hơn
        best_pos, best_key = min(cursor, n - 1), (-1, False)
        for pos in range(win_start, win_end):
            key = (_seq_score(scene_words, norm_words, pos, K),
                   _fuzzy_eq(scene_words[0], norm_words[pos]))
            if key > best_key:
                best_pos, best_key = pos, key
                if key == (K, True):  # khớp trọn bộ ngay từ đầu — dừng luôn
                    break
        best_score = best_key[0]

        # Điểm khớp quá thấp (thường do từ chuyên ngành/tên riêng bị Whisper
        # phiên âm sai hàng loạt) → kết quả tìm kiếm không đáng tin, và vì
        # win_start luôn ưu tiên vị trí GẦN cursor cũ nhất, best_pos có xu
        # hướng bị "hút" về sát vị trí của scene TRƯỚC, làm 2 scene trùng mốc
        # giờ (chồng cảnh / im lặng rồi phát dồn). Khi đó tin cursor (đã đi
        # đúng hết scene trước qua _walk_scene) hơn là tin việc định vị lại.
        min_score = max(1, K // 3)
        if best_score < min_score and cursor < n:
            best_pos = min(cursor, n - 1)

        start_time = word_timestamps[best_pos]['start']

        # Trigger word: lấy vị trí từ bước walk (chính xác kể cả khi Whisper
        # chèn/bỏ từ ở giữa scene), rồi bám vào từ khớp đúng gần đó nhất
        pos_list, end_j = _walk_scene(scene_words, norm_words, best_pos)

        trig_pos = None
        trig = scene.get('trigger_idx')
        if trig is not None and 0 <= trig < len(raw_tokens) and _normalize_word(raw_tokens[trig]):
            k_norm = sum(1 for t in raw_tokens[:trig] if _normalize_word(t))
            trig_pos = pos_list[k_norm] if k_norm < len(pos_list) else None
        if trig_pos is not None:
            tw = _normalize_word(raw_tokens[trig])
            cand = [p for p in range(max(best_pos, trig_pos - 3), min(n, trig_pos + 4))
                    if _fuzzy_eq(tw, norm_words[p])]
            pos = min(cand, key=lambda p: abs(p - trig_pos)) if cand else trig_pos
            start_time = word_timestamps[pos]['start']

        # Timeline chỉ tiến, không lùi
        if starts:
            start_time = max(start_time, starts[-1])
        starts.append(start_time)

        if collect_word_times:
            raw_valid = [t for t in raw_tokens if _normalize_word(t)]
            word_times.append([
                (raw_valid[i], word_timestamps[p]['start'], word_timestamps[p]['end'])
                for i, p in enumerate(pos_list)
            ])

        last_pos = best_pos
        cursor = min(n, end_j)
        w_at = word_timestamps[best_pos]['word'].strip()
        print(f'    scene {si+1}: whisper từ #{best_pos} ({w_at!r}) @ {start_time:.2f}s, '
              f'khớp {best_score}/{K}' + (f', trigger @ từ #{trig_pos}' if trig_pos is not None else ''))

    return (starts, word_times) if collect_word_times else starts


def make_ass_subtitles(scenes: list[dict], word_timestamps: list[dict], out_path: Path) -> bool:
    """Sinh vietsub .ass bám đúng giọng đọc: CHỮ lấy từ script (đúng chính tả),
    THỜI GIAN lấy từ timestamp từng từ của Whisper trên audio TTS thật.
    Trả về True nếu sinh được ít nhất 1 dòng sub."""
    _, word_times = whisper_align_scenes(scenes, word_timestamps, collect_word_times=True)

    # Gom từ thành dòng sub: ngắt ở dấu câu mạnh, dấu phẩy khi đủ dài, hoặc ~40 ký tự
    events: list[list] = []  # [start, end, text]
    for wt in word_times:
        line, t0, t1 = [], None, None
        for tok, ws, we in wt:
            if t0 is None:
                t0 = ws
            line.append(tok)
            t1 = we
            joined = ' '.join(line)
            hard = tok[-1:] in '.!?…'
            soft = tok[-1:] in ',;:' and len(joined) >= 24
            if hard or soft or len(joined) >= 40:
                events.append([t0, t1, joined])
                line, t0, t1 = [], None, None
        if line:
            events.append([t0, t1, ' '.join(line)])

    if not events:
        return False

    # Kéo end tới sát dòng kế cho đỡ nhấp nháy (tối đa +0.4s), đảm bảo end > start
    for k in range(len(events)):
        nxt = events[k + 1][0] if k + 1 < len(events) else events[k][1] + 0.6
        events[k][1] = max(events[k][1], min(nxt, events[k][1] + 0.4))
        if events[k][1] <= events[k][0]:
            events[k][1] = events[k][0] + 0.3

    def _ts(t: float) -> str:
        return f'{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}'

    font_size = int(TARGET_H * 0.052)
    margin_v = int(TARGET_H * 0.045)
    header = (
        '[Script Info]\n'
        'ScriptType: v4.00+\n'
        f'PlayResX: {TARGET_W}\n'
        f'PlayResY: {TARGET_H}\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Viet,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,'
        f'-1,0,0,0,100,100,0,0,1,2.5,1,2,80,80,{margin_v},163\n\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(header)
        for t0, t1, text in events:
            f.write(f'Dialogue: 0,{_ts(t0)},{_ts(t1)},Viet,,0,0,0,,{text}\n')
    print(f'  ✓ Vietsub: {len(events)} dòng → {out_path.name}')
    return True


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
    scene_starts: list[float] | None = None,
    subtitle_path: Path | None = None,
):
    """Ghép clips theo timestamp TTS thật. Ưu tiên scene_starts (từ Whisper word-level
    alignment — chính xác đến từng chữ, không phụ thuộc cách AI chia scene)."""
    tts_duration = _probe_duration(tts_path)

    clips = [s for s in scenes if s.get('clip_path')]
    if not clips:
        raise RuntimeError('Không có clip nào hợp lệ')

    if scene_starts and len(scene_starts) >= len(clips):
        # Timestamp thật từ Whisper — chính xác đến từng chữ.
        # Cảnh đầu luôn phủ từ giây 0 (kể cả khi có trigger dời mốc của nó),
        # nếu không tổng video < audio và -shortest sẽ cắt mất cuối audio.
        starts = [0.0] + scene_starts[1:len(clips)]
        scene_durations = []
        for i in range(len(clips)):
            start = starts[i]
            end = starts[i + 1] if i + 1 < len(starts) else tts_duration
            scene_durations.append(max(end - start, 0.5))
    elif sentence_durations and len(sentence_durations) >= len(clips):
        # Fallback cũ: map theo thứ tự câu TTS, chỉ đúng khi 1 scene = 1 câu
        timestamps: list[float] = []
        t = 0.0
        for _, dur in sentence_durations[:len(clips)]:
            timestamps.append(t)
            t += dur
        scene_durations = []
        for i, clip in enumerate(clips):
            start = timestamps[i]
            end = timestamps[i + 1] if i + 1 < len(timestamps) else tts_duration
            scene_durations.append(max(end - start, 1.0))
    else:
        # Fallback cuối: chia đều theo TTS duration
        per = tts_duration / len(clips)
        scene_durations = [per] * len(clips)

    _build_with_transitions(clips, scene_durations, tts_path, output_path, work_dir,
                            subtitle_path=subtitle_path)


# Các hiệu ứng chuyển cảnh của FFmpeg xfade — chọn ngẫu nhiên mỗi ranh giới cảnh
XFADE_TRANSITIONS = [
    'fade', 'dissolve', 'fadeblack',
    'slideleft', 'slideright', 'slideup', 'slidedown',
    'wipeleft', 'wiperight', 'wipeup', 'wipedown',
    'smoothleft', 'smoothright', 'circleopen', 'circleclose',
    'radial', 'diagtl', 'diagbr',
]


def _render_segment(clip_path: Path, target_dur: float, out_path: Path):
    """Dựng 1 segment video dài ĐÚNG target_dur giây, cùng chuẩn (kích thước, fps,
    pixel format) để xfade nối được. Clip dài hơn → cắt; ngắn hơn → đứng hình
    (tpad clone frame cuối), KHÔNG lặp lại clip."""
    vf = (f'scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,'
          f'pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:black,'
          f'setsar=1,fps={FPS},'
          f'tpad=stop_mode=clone:stop_duration={target_dur:.3f},'
          f'format=yuv420p')
    subprocess.run([
        FFMPEG_BIN, '-y', '-i', str(clip_path),
        '-vf', vf, '-t', f'{target_dur:.3f}',
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        str(out_path),
    ], check=True, capture_output=True)


def _build_with_transitions(clips, scene_durations, tts_path, output_path, work_dir,
                            subtitle_path: Path | None = None):
    """Ghép các clip với chuyển cảnh xfade ngẫu nhiên, giữ ĐÚNG đồng bộ audio.

    Nguyên lý giữ sync: mỗi cảnh (trừ cảnh cuối) được kéo dài thêm XF giây (bằng
    freeze frame cuối) để làm 'vật liệu' cho crossfade; xfade chồng lấn XF giây,
    nên tổng = Σ scene_durations = đúng độ dài audio.
    """
    import random
    seg_dir = work_dir / 'segments'
    seg_dir.mkdir(exist_ok=True)

    n = len(clips)
    # Độ dài chuyển cảnh: ngắn gọn (~0.25s) để không che mất nội dung cảnh,
    # vẫn co lại nếu có cảnh quá ngắn để cảnh nào cũng đủ chỗ crossfade
    min_dur = min(scene_durations)
    XF = min(0.25, max(0.12, min_dur * 0.3))

    # 1 clip duy nhất → không cần chuyển cảnh, mux thẳng
    if n == 1:
        seg = seg_dir / 'seg_0000.mp4'
        _render_segment(Path(clips[0]['clip_path']), scene_durations[0], seg)
        sub_args = ['-vf', f"ass='{subtitle_path}'"] if subtitle_path else []
        subprocess.run([
            FFMPEG_BIN, '-y', '-i', str(seg), '-i', str(tts_path),
            '-map', '0:v', '-map', '1:a', *sub_args,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k', '-shortest', str(output_path),
        ], check=True, capture_output=True)
        return

    # Dựng từng segment: cảnh i (i < n-1) dài scene_dur_i + XF, cảnh cuối = scene_dur
    seg_paths = []
    for i, (clip, dur) in enumerate(zip(clips, scene_durations)):
        seg = seg_dir / f'seg_{i:04d}.mp4'
        target = dur + XF if i < n - 1 else dur
        _render_segment(Path(clip['clip_path']), target, seg)
        seg_paths.append(seg)

    # Xây chuỗi xfade: offset cảnh k = tổng scene_durations trước đó (bám đúng mốc thời gian)
    inputs = []
    for p in seg_paths:
        inputs += ['-i', str(p)]

    filters = []
    prev_label = '0:v'
    cum = 0.0
    for i in range(1, n):
        cum += scene_durations[i - 1]
        trans = random.choice(XFADE_TRANSITIONS)
        out_label = f'vx{i}' if i < n - 1 else 'vout'
        filters.append(
            f'[{prev_label}][{i}:v]xfade=transition={trans}:'
            f'duration={XF:.3f}:offset={cum:.3f}[{out_label}]'
        )
        prev_label = out_label

    filter_complex = ';'.join(filters)
    out_label = '[vout]'
    if subtitle_path:
        filter_complex += f";[vout]ass='{subtitle_path}'[vsub]"
        out_label = '[vsub]'
    subprocess.run([
        FFMPEG_BIN, '-y', *inputs, '-i', str(tts_path),
        '-filter_complex', filter_complex,
        '-map', out_label, '-map', f'{n}:a',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k', '-shortest',
        str(output_path),
    ], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# MAIN pipeline
# ---------------------------------------------------------------------------

def run_content_video_pipeline(
    content: str,
    work_dir: Path,
    groq_key: str,
    pexels_key: str = '',
    pixabay_key: str = '',
    beeknoee_key: str = '',
    wan2_server_url: str = '',
    capcut_voice_type: str | None = None,
    capcut_resource_id: str | None = None,
    capcut_device_id: str | None = None,
    capcut_rate: str = '1.0',
    edge_voice: str = 'vi-VN-HoaiMyNeural',
    local_images: list | None = None,
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
    local_imgs = list(local_images) if local_images else []
    local_idx = 0

    for i, scene in enumerate(scenes):
        _progress(0.10 + 0.40 * i / len(scenes),
                  f'Xử lý cảnh {i+1}/{len(scenes)}: {scene["query"]}')

        duration = float(scene.get('duration', 6))
        clip_path = clips_dir / f'clip_{i:04d}.mp4'
        raw_path = media_dir / f'raw_{i:04d}'

        # Ưu tiên ảnh local nếu có
        if local_imgs:
            src_img = Path(local_imgs[local_idx % len(local_imgs)])
            local_idx += 1
            dst_img = raw_path.with_suffix(src_img.suffix)
            shutil.copy2(src_img, dst_img)
            try:
                process_image_clip(dst_img, clip_path, duration)
            except subprocess.CalledProcessError:
                scene['clip_path'] = None
                continue
        else:
            # Tìm video: Pexels → Pixabay → broad query
            broad_query = ' '.join(scene['query'].split()[:-1]) or scene['query']
            media_info = search_pexels_video(scene['query'], pexels_key, used_ids) if pexels_key else None
            if not media_info and pixabay_key:
                media_info = search_pixabay_video(scene['query'], pixabay_key, used_ids)
            if not media_info and pexels_key:
                media_info = search_pexels_video(broad_query, pexels_key, used_ids)
            if not media_info and pixabay_key:
                media_info = search_pixabay_video(broad_query, pixabay_key, used_ids)

            if media_info:
                raw_path = raw_path.with_suffix('.mp4')
                download_file(media_info['url'], raw_path)
                actual_dur = min(duration, float(media_info.get('duration', duration)), MAX_SCENE_SECS)
                try:
                    process_video_clip(raw_path, clip_path, actual_dur)
                    scene['duration'] = actual_dur
                except subprocess.CalledProcessError:
                    scene['clip_path'] = None
                    continue
            else:
                # Ảnh last resort từ Pexels
                img_info = search_pexels_image(scene['query'], pexels_key, used_ids) if pexels_key else None
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

    # 3b. Whisper word-level alignment — PHẢI chạy trên TOÀN BỘ scenes gốc (kể cả
    # scene không tải được clip), vì audio TTS đọc đủ hết nội dung content, không
    # bỏ sót đoạn nào. Nếu align chỉ trên valid_scenes, con trỏ trượt sẽ nhảy cóc
    # qua đúng đoạn audio của scene bị loại, làm lệch timestamp mọi scene sau đó.
    scene_starts_all = None
    if groq_key:
        try:
            _progress(0.68, 'Căn chỉnh thời gian bằng Whisper...')
            words = whisper_words(tts_path, groq_key)
            if words:
                scene_starts_all = whisper_align_scenes(scenes, words)
                print(f'  ✓ Whisper align: {len(words)} từ, {len(scene_starts_all)} mốc thời gian (toàn bộ {len(scenes)} scene)')
        except Exception as e:
            print(f'  ⚠ Whisper align lỗi, dùng fallback theo câu TTS: {e}')

    # Lọc valid_scenes + scene_starts song song, giữ đúng tương ứng vị trí
    valid_scenes: list[dict] = []
    scene_starts: list[float] | None = [] if scene_starts_all else None
    for i, s in enumerate(scenes):
        if s.get('clip_path'):
            valid_scenes.append(s)
            if scene_starts_all is not None:
                scene_starts.append(scene_starts_all[i])
    if not valid_scenes:
        raise RuntimeError('Không có clip nào được tạo thành công.')

    # 4. Ghép video
    _progress(0.75, 'Ghép video...')
    output_path = work_dir / 'content_video.mp4'
    build_content_video(valid_scenes, tts_path, output_path, work_dir,
                        sentence_durations=sentence_durations,
                        scene_starts=scene_starts)

    _progress(1.0, 'Hoàn tất!')
    return output_path
