const { BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { fileURLToPath } = require('node:url');

const WIDTH = 1080;
const HEIGHT = 1920;

function visualBreakpoints(steps, total) {
  const points = new Set([0, total]);
  for (const step of steps) {
    if (['post', 'comment', 'reply', 'image', 'sticker', 'mess'].includes(step.type)) {
      points.add(Number(step._start)); points.add(Number(step._end));
      if (step.progressiveReveal) {
        const clips = [{ duration: step.ttsDuration || 0 }, ...(step.extraTtsSegments || [])];
        const pause = Math.max(0, Number(step.progressivePause ?? .3));
        let offset = 0;
        for (let i = 0; i < clips.length - 1; i++) {
          offset += Number(clips[i].duration || 0) + pause;
          if (offset > 0) points.add(Number(step._start) + offset);
        }
      }
    }
  }
  return [...points].filter(Number.isFinite).sort((a, b) => a - b);
}

function totalDuration(steps) {
  return Math.max(0, ...steps
    .filter(step => !(step.type === 'audio' && step.parallel))
    .map(step => Number(step._end || 0) + Number(step.gap || 0)));
}

async function renderSegments({ steps, total, threadsRoot, workDir, blurIdentity, sendProgress }) {
  const win = new BrowserWindow({
    show: false,
    width: WIDTH,
    height: HEIGHT,
    useContentSize: true,
    frame: false,
    transparent: true,
    webPreferences: { offscreen: true, backgroundThrottling: false, contextIsolation: true, sandbox: true },
  });
  try {
    const url = new URL(`file://${path.join(threadsRoot, 'export_render.html')}`);
    url.searchParams.set('w', String(WIDTH));
    url.searchParams.set('blur', blurIdentity ? '1' : '0');
    await win.loadURL(url.toString());
    await win.webContents.executeJavaScript(`setSteps(${JSON.stringify(steps)})`);
    const points = visualBreakpoints(steps, total), segments = [];
    for (let i = 0; i < points.length - 1; i++) {
      const t0 = points[i], t1 = points[i + 1];
      if (t1 - t0 < 1e-6) continue;
      const state = await win.webContents.executeJavaScript(`renderStackAt(${(t0 + t1) / 2})`);
      let imagePath = null;
      if ((state.stackIds || []).length || (state.hasMeme && !state.dynamicMeme)) {
        const rect = await win.webContents.executeJavaScript(`(() => { const r=document.getElementById('preview-frame').getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)} })()`);
        const captured = await win.webContents.capturePage(rect);
        // capturePage trả pixel vật lý trên màn hình Retina (thường 2x), dù
        // rect và BrowserWindow dùng pixel CSS. Nếu đưa PNG 2160x3840 đó vào
        // canvas video 1080x1920, FFmpeg sẽ giữ nguyên kích thước và chỉ hiện
        // góc trên-trái: toàn bộ UI trông bị phóng 2 lần, mất nửa bên phải.
        // Chuẩn hóa ảnh scene về đúng kích thước xuất, độc lập tỉ lệ màn hình.
        const capturedSize = captured.getSize();
        const image = capturedSize.width === WIDTH && capturedSize.height === HEIGHT
          ? captured
          : captured.resize({ width: WIDTH, height: HEIGHT, quality: 'best' });
        imagePath = path.join(workDir, `scene-${String(i).padStart(4, '0')}.png`);
        fs.writeFileSync(imagePath, image.toPNG());
      }
      const dynamicStep = state.dynamicMeme ? steps.find(step => step.id === state.memeStepId) : null;
      segments.push({
        t0, t1, imagePath,
        dynamicMeme: state.dynamicMeme ? {
          ...state.memeBox,
          mediaUrl: state.memeMediaUrl,
          path: dynamicStep?.mediaDesktopPath || dynamicStep?.mediaPath || null,
          stepStart: Number(dynamicStep?._start || 0),
        } : null,
      });
      sendProgress({ percent: Math.round((i + 1) / Math.max(1, points.length - 1) * 55), text: `Đang dựng cảnh ${i + 1}/${points.length - 1}…` });
    }
    return segments;
  } finally {
    win.destroy();
  }
}

async function download(url, output) {
  if (String(url).startsWith('file:')) {
    fs.copyFileSync(fileURLToPath(url), output);
    return output;
  }
  if (path.isAbsolute(String(url)) && fs.existsSync(String(url))) {
    fs.copyFileSync(String(url), output);
    return output;
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Không tải được audio TTS: HTTP ${response.status}`);
  fs.writeFileSync(output, Buffer.from(await response.arrayBuffer()));
  return output;
}

async function collectAudio(steps, workDir) {
  const tracks = []; let index = 0;
  for (const step of steps) {
    if (['post', 'comment', 'reply', 'image', 'mess'].includes(step.type) && step.audioUrl) {
      const clips = [{ audioUrl: step.audioUrl, duration: step.ttsDuration || 0 }, ...(step.extraTtsSegments || [])];
      const pause = step.progressiveReveal ? Math.max(0, Number(step.progressivePause ?? .3)) : 0;
      let offset = 0;
      for (let clipIndex = 0; clipIndex < clips.length; clipIndex++) {
        const clip = clips[clipIndex];
        if (clip.audioUrl) {
          const file = await download(clip.audioUrl, path.join(workDir, `tts-${index++}.mp3`));
          const remaining = Math.max(0, Number(step.duration || 0) - offset);
          const clipDuration = Number(clip.duration || 0);
          const maxDuration = Math.min(clipDuration, remaining);
          if (maxDuration > 0) tracks.push({ path: file, start: Number(step._start) + offset, volume: 1, maxDuration });
        }
        offset += Number(clip.duration || 0) + (clipIndex < clips.length - 1 ? pause : 0);
      }
    }
    const mediaPath = step.mediaDesktopPath || step.mediaPath || (String(step.mediaUrl || '').startsWith('file:') ? fileURLToPath(step.mediaUrl) : null);
    if (step.type === 'audio' && mediaPath && fs.existsSync(mediaPath)) {
      tracks.push({ path: mediaPath, start: Number(step._start), volume: Number(step.volume || 1), maxDuration: Number(step.duration || 0) || null });
    }
    // Tiếng gốc của video-card là một track độc lập với TTS. Trước đây đây
    // là nhánh else-if nên card video có TTS bị bỏ mất tiếng video hoàn toàn.
    if (step.type === 'image' && step.mediaIsVideo && mediaPath && fs.existsSync(mediaPath)) {
      tracks.push({ path: mediaPath, start: Number(step._start), volume: 1, maxDuration: Number(step.duration || 0) || null });
    }
  }
  return tracks;
}

function ffmpegArgs({ bgVideoPath, bgAudioPath, bgAudioVolume, segments, total, audioTracks, outputPath }) {
  const inputs = ['-stream_loop', '-1', '-i', bgVideoPath];
  const filters = [`[0:v]scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1,trim=duration=${total},setpts=PTS-STARTPTS[bg]`];
  let last = 'bg', inputIndex = 1;
  for (const segment of segments) {
    if (!segment.imagePath) continue;
    inputs.push('-loop', '1', '-t', (segment.t1 - segment.t0).toFixed(3), '-i', segment.imagePath);
    const next = `v${inputIndex}`;
    filters.push(`[${last}][${inputIndex}:v]overlay=0:0:enable='between(t,${segment.t0.toFixed(3)},${segment.t1.toFixed(3)})'[${next}]`);
    last = next; inputIndex++;
  }
  for (const segment of segments) {
    const meme = segment.dynamicMeme;
    if (!meme?.path || !fs.existsSync(meme.path)) continue;
    const duration = segment.t1 - segment.t0;
    const x = Math.round(Number(meme.xRatio) * WIDTH), y = Math.round(Number(meme.yRatio) * HEIGHT);
    let width = Math.max(2, Math.round(Number(meme.wRatio) * WIDTH));
    let height = Math.max(2, Math.round(Number(meme.hRatio) * HEIGHT));
    width += width % 2; height += height % 2;
    const isGif = /\.gif$/i.test(meme.path);
    if (isGif) {
      inputs.push('-ignore_loop', '0', '-itsoffset', segment.t0.toFixed(3), '-t', duration.toFixed(3), '-i', meme.path);
    } else {
      // Mọi segment của cùng video đều giữ mốc PTS từ lúc CARD bắt đầu.
      // Không dùng stream_loop và không đặt input bắt đầu tại segment.t0:
      // hai cách đó làm card âm thanh tạo breakpoint rồi video chạy lại từ 0.
      const stepStart = Number(meme.stepStart || 0);
      inputs.push('-itsoffset', stepStart.toFixed(3), '-t', Math.max(0.001, segment.t1 - stepStart).toFixed(3), '-i', meme.path);
    }
    const scaled = `m${inputIndex}`, next = `v${inputIndex}`;
    const holdFinalFrame = isGif ? '' : `,tpad=stop_mode=clone:stop_duration=${total.toFixed(3)}`;
    filters.push(`[${inputIndex}:v]scale=${width}:${height}${holdFinalFrame}[${scaled}]`);
    filters.push(`[${last}][${scaled}]overlay=${x}:${y}:enable='between(t,${segment.t0.toFixed(3)},${segment.t1.toFixed(3)})'[${next}]`);
    last = next; inputIndex++;
  }
  const audioLabels = [];
  for (const track of audioTracks) {
    inputs.push('-i', track.path);
    const label = `a${inputIndex}`, delay = Math.round(track.start * 1000);
    const trim = track.maxDuration ? `atrim=duration=${Number(track.maxDuration).toFixed(3)},asetpts=PTS-STARTPTS,` : '';
    filters.push(`[${inputIndex}:a]${trim}adelay=${delay}|${delay},volume=${track.volume}[${label}]`);
    audioLabels.push(label); inputIndex++;
  }
  if (bgAudioPath) {
    inputs.push('-stream_loop', '-1', '-i', bgAudioPath);
    filters.push(`[${inputIndex}:a]atrim=duration=${total},volume=${Number(bgAudioVolume || 0.5)}[abg]`);
    audioLabels.push('abg'); inputIndex++;
  }
  if (audioLabels.length) {
    filters.push(`${audioLabels.map(label => `[${label}]`).join('')}amix=inputs=${audioLabels.length}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.97[aout]`);
  } else {
    filters.push('anullsrc=channel_layout=stereo:sample_rate=44100[aout]');
  }
  return ['-y', ...inputs, '-filter_complex', filters.join(';'), '-map', `[${last}]`, '-map', '[aout]', '-t', total.toFixed(3), '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-progress', 'pipe:1', outputPath];
}

function runFfmpeg(binary, args, total, sendProgress) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, { windowsHide: true }); let stderr = '', stdout = '';
    child.stdout.on('data', chunk => {
      stdout += chunk.toString(); const lines = stdout.split(/\r?\n/); stdout = lines.pop() || '';
      for (const line of lines) { const match = /^out_time_ms=(\d+)/.exec(line); if (match) { const seconds = Number(match[1]) / 1e6; sendProgress({ percent: 60 + Math.round(Math.min(seconds / total, 1) * 40), text: `Đang ghép MP4 ${seconds.toFixed(1)}s/${total.toFixed(1)}s…` }); } }
    });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-16000); });
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve() : reject(new Error(`FFmpeg lỗi (${code}): ${stderr}`)));
  });
}

async function exportProject({ payload, outputPath, threadsRoot, ffmpegPath, sendProgress }) {
  const steps = payload.steps || [], total = totalDuration(steps);
  if (!steps.length || total <= 0) throw new Error('Kịch bản rỗng.');
  if (!payload.bgVideoPath || !fs.existsSync(payload.bgVideoPath)) throw new Error('Không tìm thấy video nền trên máy. Hãy chọn lại video nền.');
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'threads-offline-export-'));
  try {
    sendProgress({ percent: 1, text: 'Đang mở renderer ẩn 1080×1920…' });
    const segments = await renderSegments({ steps, total, threadsRoot, workDir, blurIdentity: payload.blurIdentity, sendProgress });
    sendProgress({ percent: 57, text: 'Đang chuẩn bị CapCut TTS…' });
    const audioTracks = await collectAudio(steps, workDir);
    const args = ffmpegArgs({ bgVideoPath: payload.bgVideoPath, bgAudioPath: payload.bgAudioPath, bgAudioVolume: payload.bgAudioVolume, segments, total, audioTracks, outputPath });
    await runFfmpeg(ffmpegPath, args, total, sendProgress);
    sendProgress({ percent: 100, text: '✓ Đã xuất MP4.' });
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
}

module.exports = { exportProject };
