const $ = (selector) => document.querySelector(selector);

const elements = {
  editors: $('#comparison-editors'),
  addComparison: $('#add-comparison'),
  stage: $('#video-stage'),
  previewTopicA: $('#preview-topic-a'),
  previewTopicB: $('#preview-topic-b'),
  previewImageA: $('#preview-image-a'),
  previewImageB: $('#preview-image-b'),
  previewTextA: $('#preview-text-a'),
  previewTextB: $('#preview-text-b'),
  characterImage: $('#character-image'),
  caption: $('#caption'),
  progress: $('#progress'),
  timeLabel: $('#time-label'),
  play: $('#play'),
  restart: $('#restart'),
  buildStatus: $('#build-status'),
  projectJson: $('#project-json'),
  jsonStatus: $('#json-status'),
  aiCount: $('#ai-count'),
  aiTheme: $('#ai-theme'),
  generateAi: $('#generate-ai'),
  aiStatus: $('#ai-status'),
  ttsServerUrl: $('#tts-server-url'),
  ttsVoice: $('#tts-voice'),
  ttsEnVoice: $('#tts-en-voice'),
  connectTts: $('#connect-tts'),
  generateTts: $('#generate-tts'),
  ttsStatus: $('#tts-status'),
  exportVideo: $('#export-video'),
  exportProgressWrap: $('#export-progress-wrap'),
  exportProgressBar: $('#export-progress-bar'),
  exportProgressText: $('#export-progress-text'),
  exportResult: $('#export-result'),
};

const SCENES = [
  { scene: 'point-a', duration: 3 },
  { scene: 'point-b', duration: 3 },
  { scene: 'question', duration: 3 },
  { scene: 'explain-a', duration: 3.5 },
  { scene: 'explain-b', duration: 3.5 },
];
const TTS_GAP = 0.2;
const CHARACTER_IMAGES = {
  'point-a': 'assets/character-point-up-left.png',
  'point-b': 'assets/character-point-up-right.png',
  question: 'assets/character-question.png',
  'explain-a': 'assets/character-point-up-left.png',
  'explain-b': 'assets/character-point-up-right.png',
};

let comparisons = [{
  topicA: 'Nhu cầu',
  topicB: 'Mong muốn',
  explanationA: 'Nhu cầu là điều thiết yếu con người cần có để sống và phát triển.',
  explanationB: 'Mong muốn là điều ta muốn có để cuộc sống tiện nghi hoặc thú vị hơn.',
  imageA: '',
  imageB: '',
  useTextA: false,
  useTextB: false,
  englishTopicA: [],
  englishTopicB: [],
  englishExplanationA: [],
  englishExplanationB: [],
}];
let playing = false;
let startedAt = 0;
let elapsedBeforePlay = 0;
let animationFrame = 0;
let ttsVoices = [];
let ttsEnVoices = [];
let ttsTracks = [];
let activeAudioIndex = -1;
let exportPollTimer = null;

function escapeHtml(text) {
  const node = document.createElement('div');
  node.textContent = String(text ?? '');
  return node.innerHTML;
}

function escapeAttr(text) {
  return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function defaultPair() {
  return {
    topicA: '',
    topicB: '',
    explanationA: '',
    explanationB: '',
    imageA: '',
    imageB: '',
    useTextA: false,
    useTextB: false,
    englishTopicA: [],
    englishTopicB: [],
    englishExplanationA: [],
    englishExplanationB: [],
  };
}

function asPhraseList(value) {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === 'string' && value.trim()) return [value.trim()];
  return [];
}

function wordsOf(text) {
  return String(text ?? '').match(/[\p{L}\p{N}'-]+|[^\s\p{L}\p{N}'-]+/gu) || [];
}

function phraseRanges(text, phrases) {
  const tokens = wordsOf(text);
  const lowered = tokens.map((token) => token.toLowerCase());
  const ranges = [];
  for (const phrase of asPhraseList(phrases)) {
    const parts = wordsOf(phrase).map((token) => token.toLowerCase());
    if (!parts.length) continue;
    for (let start = 0; start <= lowered.length - parts.length; start += 1) {
      if (parts.every((part, offset) => lowered[start + offset] === part)) {
        ranges.push({ start, end: start + parts.length - 1, phrase });
        break;
      }
    }
  }
  return ranges;
}

function phraseFromRange(text, start, end) {
  return wordsOf(text).slice(start, end + 1).join(' ').replace(/\s+([,.!?;:])/g, '$1').trim();
}

function englishTokenPicker(pair, field) {
  const key = `english${field[0].toUpperCase()}${field.slice(1)}`;
  const text = pair[field] || '';
  const ranges = phraseRanges(text, pair[key]);
  const tokens = wordsOf(text);
  if (!tokens.length) return '<div class="english-token-box muted">Nhập chữ rồi click từ tiếng Anh để tô xanh.</div>';
  return `<div class="english-token-box" data-english-field="${escapeAttr(field)}">
    ${tokens.map((token, index) => {
      const selected = ranges.some((range) => index >= range.start && index <= range.end);
      const isWord = /[\p{L}\p{N}]/u.test(token);
      return `<button type="button" class="english-token${selected ? ' selected' : ''}${isWord ? '' : ' punct'}" data-token-index="${index}">${escapeHtml(token)}</button>`;
    }).join('')}
  </div>`;
}

function pairEditor(pair, index) {
  const side = (key, label, colorSide) => `
    <div class="concept-card" data-side="${colorSide}">
      <div class="concept-heading"><span>${label}</span><strong>Khái niệm bên ${label === 'A' ? 'trái' : 'phải'}</strong></div>
      <label>Tên chủ đề
        <input type="text" data-field="topic${label}" value="${escapeAttr(pair[`topic${label}`])}" maxlength="38">
        ${englishTokenPicker(pair, `topic${label}`)}
      </label>
      <label>Hình minh họa
        <input type="url" data-field="image${label}" value="${escapeAttr(pair[`image${label}`])}" placeholder="Dán link ảnh trực tiếp, ví dụ https://...">
        <small class="field-help">Dùng link ảnh ngoài để không phải tải file về máy.</small>
      </label>
      <label class="inline-check">
        <input type="checkbox" data-field="useText${label}" ${pair[`useText${label}`] ? 'checked' : ''}>
        <span>Dùng chữ thay ảnh</span>
      </label>
      <label>Giải thích
        <textarea data-field="explanation${label}" rows="3" maxlength="180">${escapeHtml(pair[`explanation${label}`])}</textarea>
        ${englishTokenPicker(pair, `explanation${label}`)}
      </label>
    </div>`;

  return `<section class="pair-block" data-pair-index="${index}">
    <div class="pair-head">
      <strong>Cặp phân biệt ${index + 1}</strong>
      ${comparisons.length > 1 ? '<button class="remove-pair" type="button">Xóa cặp</button>' : ''}
    </div>
    ${side('A', 'A', 'left')}
    ${side('B', 'B', 'right')}
  </section>`;
}

function renderEditors() {
  elements.editors.innerHTML = comparisons.map(pairEditor).join('');
}

function invalidateTts() {
  if (ttsTracks.length) pauseAllAudio();
  ttsTracks = [];
  elements.ttsStatus.className = 'tts-status';
  elements.ttsStatus.textContent = 'Nội dung đã thay đổi — hãy tạo lại giọng đọc.';
  elements.buildStatus.innerHTML = '<i></i> Chưa tạo TTS';
  elements.exportVideo.disabled = true;
  elements.exportResult.innerHTML = '';
}

function stopPreview() {
  cancelAnimationFrame(animationFrame);
  pauseAllAudio();
  playing = false;
  elapsedBeforePlay = 0;
  elements.play.innerHTML = '<span>▶</span> Phát video mẫu';
}

function updatePair(index, field, value) {
  if (!comparisons[index]) return;
  comparisons[index][field] = value;
  invalidateTts();
  renderAt(Math.min(currentTime(), totalDuration()));
}

elements.editors.addEventListener('input', (event) => {
  const field = event.target.dataset.field;
  if (!field) return;
  const block = event.target.closest('.pair-block');
  const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
  updatePair(Number(block.dataset.pairIndex), field, value);
});

elements.editors.addEventListener('focusout', (event) => {
  const field = event.target.dataset.field;
  if (/^(topic|explanation)[AB]$/.test(field || '')) renderEditors();
});

elements.editors.addEventListener('mousedown', (event) => {
  if (event.target.closest('.english-token')) event.preventDefault();
});

function toggleEnglishToken(pairIndex, field, tokenIndex) {
  const pair = comparisons[pairIndex];
  if (!pair) return;
  const key = `english${field[0].toUpperCase()}${field.slice(1)}`;
  const text = pair[field] || '';
  const phrases = asPhraseList(pair[key]);
  const ranges = phraseRanges(text, phrases);
  const active = ranges.find((range) => tokenIndex >= range.start && tokenIndex <= range.end);

  if (active) {
    pair[key] = phrases.filter((phrase) => phrase.toLowerCase() !== active.phrase.toLowerCase());
  } else {
    const adjacent = ranges.find((range) => tokenIndex === range.start - 1 || tokenIndex === range.end + 1);
    if (adjacent) {
      const nextStart = Math.min(adjacent.start, tokenIndex);
      const nextEnd = Math.max(adjacent.end, tokenIndex);
      const nextPhrase = phraseFromRange(text, nextStart, nextEnd);
      pair[key] = phrases
        .filter((phrase) => phrase.toLowerCase() !== adjacent.phrase.toLowerCase())
        .concat(nextPhrase);
    } else {
      pair[key] = phrases.concat(phraseFromRange(text, tokenIndex, tokenIndex));
    }
  }

  invalidateTts();
  renderEditors();
}

elements.editors.addEventListener('click', (event) => {
  const tokenButton = event.target.closest('.english-token');
  if (tokenButton && !tokenButton.classList.contains('punct')) {
    const picker = tokenButton.closest('[data-english-field]');
    const block = tokenButton.closest('.pair-block');
    const index = Number(block?.dataset.pairIndex);
    const field = picker.dataset.englishField;
    toggleEnglishToken(index, field, Number(tokenButton.dataset.tokenIndex));
    return;
  }
  const button = event.target.closest('.remove-pair');
  if (!button) return;
  if (comparisons.length <= 1) return;
  const block = button.closest('.pair-block');
  const index = Number(block?.dataset.pairIndex);
  if (!Number.isInteger(index)) return;
  stopPreview();
  comparisons.splice(index, 1);
  invalidateTts();
  renderEditors();
  renderAt(0);
});

function narrationSegments() {
  return comparisons.flatMap((pair, comparisonIndex) => [
    { comparisonIndex, scene: 'point-a', text: `Đây là ${pair.topicA || 'chủ đề A'}.`, englishPhrases: pair.englishTopicA || [] },
    { comparisonIndex, scene: 'point-b', text: `Đây là ${pair.topicB || 'chủ đề B'}.`, englishPhrases: pair.englishTopicB || [] },
    {
      comparisonIndex,
      scene: 'question',
      text: `Vậy ${pair.topicA || 'chủ đề A'} và ${pair.topicB || 'chủ đề B'} khác nhau như thế nào?`,
      englishPhrases: [...(pair.englishTopicA || []), ...(pair.englishTopicB || [])],
    },
    { comparisonIndex, scene: 'explain-a', text: pair.explanationA || `${pair.topicA || 'Chủ đề A'} là gì?`, englishPhrases: pair.englishExplanationA || [] },
    { comparisonIndex, scene: 'explain-b', text: pair.explanationB || `${pair.topicB || 'Chủ đề B'} là gì?`, englishPhrases: pair.englishExplanationB || [] },
  ]);
}

function timeline() {
  const expectedTracks = comparisons.length * SCENES.length;
  const source = ttsTracks.length === expectedTracks
    ? ttsTracks.map((track) => ({ ...track, duration: track.duration + TTS_GAP }))
    : comparisons.flatMap((_, comparisonIndex) => SCENES.map((scene) => ({ ...scene, comparisonIndex })));
  let cursor = 0;
  return source.map((segment, index) => {
    const item = { ...segment, index, start: cursor, end: cursor + segment.duration };
    cursor = item.end;
    return item;
  });
}

function totalDuration() {
  const items = timeline();
  return items.length ? items[items.length - 1].end : 0;
}

function segmentAt(time) {
  const items = timeline();
  return items.find((item) => time < item.end) || items[items.length - 1];
}

function showPreviewImage(imageElement, textElement, source, useText, fallbackText) {
  textElement.textContent = fallbackText;
  textElement.classList.toggle('text-mode', Boolean(useText));
  if (source && !useText) {
    imageElement.src = source;
    imageElement.classList.add('has-image');
    textElement.classList.remove('show-text');
  } else {
    imageElement.removeAttribute('src');
    imageElement.classList.remove('has-image');
    textElement.classList.toggle('show-text', Boolean(useText));
  }
}

function splitSubtitle(text, maxWords = 16, maxLines = 2) {
  const parts = String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim()
    .split(/(?<=[.!?。！？])\s+/)
    .filter(Boolean);

  if (!parts.length) return [];

  const lines = [];
  let current = [];
  let currentCount = 0;
  const flush = () => {
    if (current.length) lines.push(current.join(' ').trim());
    current = [];
    currentCount = 0;
  };

  for (const part of parts) {
    const words = part.split(/\s+/).filter(Boolean);
    if (!words.length) continue;
    if (lines.length + 1 >= maxLines && current.length) {
      current.push(part.trim());
      currentCount += words.length;
      continue;
    }
    if (currentCount + words.length > maxWords && current.length) flush();
    current.push(part.trim());
    currentCount += words.length;
    if (currentCount >= maxWords) flush();
  }

  flush();
  return lines.length ? lines.slice(0, maxLines) : [String(text).trim()];
}

function explanationCaption(topic, explanation) {
  const title = `<b>${escapeHtml(topic)}</b>`;
  const lines = splitSubtitle(explanation, 16, 2).map((line) => escapeHtml(line));
  if (!lines.length) return title;
  return `${title} — ${lines.join(' ')}`;
}

function renderAt(time) {
  const segment = segmentAt(time);
  if (!segment) return;
  const pair = comparisons[segment.comparisonIndex] || comparisons[0];
  const topicA = pair.topicA || 'Chủ đề A';
  const topicB = pair.topicB || 'Chủ đề B';
  elements.stage.dataset.scene = segment.scene;
  elements.previewTopicA.textContent = topicA;
  elements.previewTopicB.textContent = topicB;
  showPreviewImage(elements.previewImageA, elements.previewTextA, pair.imageA, pair.useTextA, topicA);
  showPreviewImage(elements.previewImageB, elements.previewTextB, pair.imageB, pair.useTextB, topicB);

  const characterSrc = CHARACTER_IMAGES[segment.scene];
  if (!elements.characterImage.src.endsWith(characterSrc)) elements.characterImage.src = characterSrc;
  const copy = {
    'point-a': `Đây là <b>${escapeHtml(topicA)}</b>`,
    'point-b': `Đây là <b>${escapeHtml(topicB)}</b>`,
    question: `Vậy <b>${escapeHtml(topicA)}</b> và <b>${escapeHtml(topicB)}</b> khác nhau thế nào?`,
    'explain-a': explanationCaption(topicA, pair.explanationA),
    'explain-b': explanationCaption(topicB, pair.explanationB),
  };
  elements.caption.innerHTML = copy[segment.scene];

  const duration = totalDuration();
  const clamped = Math.max(0, Math.min(time, duration));
  elements.progress.style.width = `${duration ? (clamped / duration) * 100 : 0}%`;
  elements.timeLabel.textContent = `${formatTime(clamped)} / ${formatTime(duration)}`;
}

function formatTime(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
}

function ttsBaseUrl() {
  return elements.ttsServerUrl.value.trim().replace(/\/$/, '');
}

function setTtsStatus(message, type = '') {
  elements.ttsStatus.className = `tts-status${type ? ` ${type}` : ''}`;
  elements.ttsStatus.textContent = message;
}

function setJsonStatus(message, type = '') {
  elements.jsonStatus.className = `json-status show${type ? ` ${type}` : ''}`;
  elements.jsonStatus.textContent = message;
}

function setAiStatus(message, type = '') {
  elements.aiStatus.className = `tts-status${type ? ` ${type}` : ''}`;
  elements.aiStatus.textContent = message;
}

async function loadTtsVoices() {
  setTtsStatus('Đang kiểm tra kết nối...');
  elements.ttsVoice.disabled = true;
  elements.ttsEnVoice.disabled = true;
  elements.generateTts.disabled = true;
  try {
    const [response, enResponse] = await Promise.all([
      fetch(`${ttsBaseUrl()}/voices`),
      fetch(`${ttsBaseUrl()}/voices-en`),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (!enResponse.ok) throw new Error(`HTTP ${enResponse.status}`);
    ttsVoices = await response.json();
    ttsEnVoices = await enResponse.json();
    elements.ttsVoice.innerHTML = ttsVoices.map((voice, index) => `<option value="${index}">${escapeHtml(voice.label)}</option>`).join('');
    elements.ttsEnVoice.innerHTML = ttsEnVoices.map((voice, index) => `<option value="${index}">${escapeHtml(voice.label)}</option>`).join('');
    if (ttsVoices.length) elements.ttsVoice.value = String(Math.min(1, ttsVoices.length - 1));
    if (ttsEnVoices.length) elements.ttsEnVoice.value = '0';
    elements.ttsVoice.disabled = false;
    elements.ttsEnVoice.disabled = false;
    elements.generateTts.disabled = !ttsVoices.length || !ttsEnVoices.length;
    setTtsStatus(`Đã kết nối — có ${ttsVoices.length} giọng Việt và ${ttsEnVoices.length} giọng Anh.`, 'ok');
  } catch (error) {
    ttsVoices = [];
    ttsEnVoices = [];
    elements.ttsVoice.innerHTML = '<option>Không tải được giọng đọc</option>';
    elements.ttsEnVoice.innerHTML = '<option>Không tải được giọng Anh</option>';
    setTtsStatus(`Không kết nối được: ${error.message}. Hãy chạy python3 threads/tts_server.py`, 'error');
  }
}

async function fetchTtsBatch(segments, viVoice, enVoice) {
  const response = await fetch(`${ttsBaseUrl()}/tts-mixed-batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      items: segments.map((segment) => ({ text: segment.text, englishPhrases: segment.englishPhrases || [] })),
      vi_voice_type: viVoice?.voice_type,
      vi_resource_id: viVoice?.resource_id,
      en_voice_type: enVoice?.voice_type,
      en_resource_id: enVoice?.resource_id,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  const data = await response.json();
  if (!Array.isArray(data.items) || data.items.length !== segments.length) {
    throw new Error('TTS server trả thiếu số đoạn trong batch');
  }
  return data.items.map((item) => {
    const audioUrl = `${ttsBaseUrl()}${item.audio_url}`;
    return { audioUrl, duration: item.duration, audio: new Audio(audioUrl) };
  });
}

async function generateAllTts() {
  const voice = ttsVoices[Number(elements.ttsVoice.value)] || null;
  const enVoice = ttsEnVoices[Number(elements.ttsEnVoice.value)] || null;
  const segments = narrationSegments();
  stopPreview();
  ttsTracks = [];
  elements.generateTts.disabled = true;
  elements.connectTts.disabled = true;
  try {
    for (let start = 0; start < segments.length; start += 50) {
      const chunk = segments.slice(start, start + 50);
      const batchNumber = Math.floor(start / 50) + 1;
      const batchTotal = Math.ceil(segments.length / 50);
      setTtsStatus(`Đang tạo batch ${batchNumber}/${batchTotal} — ${chunk.length} đoạn trong một request...`);
      const results = await fetchTtsBatch(chunk, voice, enVoice);
      chunk.forEach((segment, index) => ttsTracks.push({ ...segment, ...results[index] }));
    }
    renderAt(0);
    setTtsStatus(`Đã tạo ${segments.length} đoạn cho ${comparisons.length} cặp — tổng ${totalDuration().toFixed(1)} giây.`, 'ok');
    elements.buildStatus.innerHTML = '<i class="ready"></i> TTS đã sẵn sàng';
    elements.exportVideo.disabled = false;
  } catch (error) {
    pauseAllAudio();
    ttsTracks = [];
    renderAt(0);
    setTtsStatus(`Tạo giọng đọc thất bại: ${error.message}`, 'error');
    elements.exportVideo.disabled = true;
  } finally {
    elements.generateTts.disabled = !ttsVoices.length;
    elements.connectTts.disabled = false;
  }
}

function setExportProgress(percent, text) {
  elements.exportProgressWrap.classList.add('show');
  elements.exportProgressBar.style.width = `${Math.max(0, Math.min(percent, 100))}%`;
  elements.exportProgressText.textContent = text;
}

function exportPayload() {
  return {
    comparisons,
    tracks: ttsTracks.map(({ comparisonIndex, scene, audioUrl, duration }) => ({
      comparisonIndex,
      scene,
      audioUrl,
      duration,
    })),
  };
}

async function exportVideo() {
  if (ttsTracks.length !== comparisons.length * SCENES.length) {
    elements.exportResult.innerHTML = '<div class="export-error">Hãy tạo TTS đầy đủ trước khi xuất video.</div>';
    return;
  }
  elements.exportVideo.disabled = true;
  elements.exportResult.innerHTML = '';
  setExportProgress(0, 'Đang gửi dữ liệu xuất video...');
  try {
    const response = await fetch(`${ttsBaseUrl()}/export-phanbiet`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(exportPayload()),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const { job_id: jobId } = await response.json();
    pollExportStatus(jobId);
  } catch (error) {
    elements.exportVideo.disabled = false;
    elements.exportProgressWrap.classList.remove('show');
    elements.exportResult.innerHTML = `<div class="export-error">Xuất video lỗi: ${escapeHtml(error.message)}</div>`;
  }
}

function pollExportStatus(jobId) {
  if (exportPollTimer) clearInterval(exportPollTimer);
  exportPollTimer = setInterval(async () => {
    try {
      const response = await fetch(`${ttsBaseUrl()}/export-phanbiet-status/${jobId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const job = await response.json();
      setExportProgress(job.progress || 0, job.message || '');
      if (job.status === 'done') {
        clearInterval(exportPollTimer);
        elements.exportVideo.disabled = false;
        const url = `${ttsBaseUrl()}${job.downloadUrl}`;
        elements.exportResult.innerHTML = `<a class="download-link" href="${escapeAttr(url)}" download>Tải video MP4 vừa xuất</a>`;
      } else if (job.status === 'error') {
        clearInterval(exportPollTimer);
        elements.exportVideo.disabled = false;
        elements.exportResult.innerHTML = `<div class="export-error">Xuất video lỗi: ${escapeHtml(job.message || '')}</div>`;
      }
    } catch (error) {
      clearInterval(exportPollTimer);
      elements.exportVideo.disabled = false;
      elements.exportResult.innerHTML = `<div class="export-error">Mất kết nối export: ${escapeHtml(error.message)}</div>`;
    }
  }, 1200);
}

async function generateComparisonsWithAi() {
  const count = Number(elements.aiCount.value);
  if (!Number.isInteger(count) || count < 1 || count > 50) {
    setAiStatus('Số cặp phải là số nguyên từ 1 đến 50.', 'error');
    return;
  }
  elements.generateAi.disabled = true;
  setAiStatus(`AI đang tạo ${count} cặp bằng google/gemini-2.5-flash-lite...`);
  try {
    const response = await fetch(`${ttsBaseUrl()}/generate-comparisons`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count, theme: elements.aiTheme.value.trim() }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    if (!Array.isArray(data.comparisons) || data.comparisons.length !== count) {
      throw new Error('AI trả về sai số lượng cặp');
    }
    stopPreview();
    invalidateTts();
    comparisons = data.comparisons;
    renderEditors();
    renderAt(0);
    setAiStatus(`Đã tạo ${count} cặp. Hãy thêm ảnh rồi tạo TTS.`, 'ok');
  } catch (error) {
    setAiStatus(`Không tạo được nội dung: ${error.message}`, 'error');
  } finally {
    elements.generateAi.disabled = false;
  }
}

function pauseAllAudio() {
  ttsTracks.forEach((track) => track.audio.pause());
  activeAudioIndex = -1;
}

function syncAudio(time) {
  if (!playing || ttsTracks.length !== comparisons.length * SCENES.length) return;
  const segment = segmentAt(time);
  const track = ttsTracks[segment.index];
  const offset = Math.max(0, time - segment.start);
  if (!track || offset >= track.duration) {
    if (activeAudioIndex !== -1) pauseAllAudio();
    return;
  }
  if (activeAudioIndex !== segment.index) {
    pauseAllAudio();
    activeAudioIndex = segment.index;
    track.audio.currentTime = Math.min(offset, Math.max(0, track.duration - 0.02));
    track.audio.play().catch(() => {});
  } else if (Math.abs(track.audio.currentTime - offset) > 0.35) {
    track.audio.currentTime = offset;
  }
}

function currentTime() {
  return playing ? elapsedBeforePlay + (performance.now() - startedAt) / 1000 : elapsedBeforePlay;
}

function tick() {
  const time = currentTime();
  const duration = totalDuration();
  if (time >= duration) {
    elapsedBeforePlay = duration;
    playing = false;
    pauseAllAudio();
    elements.play.innerHTML = '<span>▶</span> Phát lại';
    renderAt(duration);
    return;
  }
  renderAt(time);
  syncAudio(time);
  animationFrame = requestAnimationFrame(tick);
}

function togglePlay() {
  if (playing) {
    elapsedBeforePlay = currentTime();
    playing = false;
    pauseAllAudio();
    cancelAnimationFrame(animationFrame);
    elements.play.innerHTML = '<span>▶</span> Tiếp tục';
    return;
  }
  if (elapsedBeforePlay >= totalDuration()) elapsedBeforePlay = 0;
  playing = true;
  startedAt = performance.now();
  elements.play.innerHTML = '<span>Ⅱ</span> Tạm dừng';
  tick();
}

function restart() {
  cancelAnimationFrame(animationFrame);
  pauseAllAudio();
  elapsedBeforePlay = 0;
  playing = true;
  startedAt = performance.now();
  elements.play.innerHTML = '<span>Ⅱ</span> Tạm dừng';
  tick();
}

async function importProjectJson(file) {
  if (!file) return;
  try {
    const raw = JSON.parse(await file.text());
    const list = Array.isArray(raw) ? raw : raw.comparisons;
    if (!Array.isArray(list) || !list.length) throw new Error('JSON phải là một mảng có ít nhất một cặp.');
    comparisons = list.map((item, index) => {
      if (!item || typeof item !== 'object') throw new Error(`Phần tử ${index + 1} không hợp lệ.`);
      const pair = {
        topicA: String(item.topicA ?? item.a?.topic ?? ''),
        topicB: String(item.topicB ?? item.b?.topic ?? ''),
        explanationA: String(item.explanationA ?? item.explainA ?? item.a?.explanation ?? ''),
        explanationB: String(item.explanationB ?? item.explainB ?? item.b?.explanation ?? ''),
        imageA: String(item.imageA ?? item.a?.image ?? ''),
        imageB: String(item.imageB ?? item.b?.image ?? ''),
        useTextA: Boolean(item.useTextA ?? item.a?.useText ?? false),
        useTextB: Boolean(item.useTextB ?? item.b?.useText ?? false),
        englishTopicA: asPhraseList(item.englishTopicA ?? item.a?.englishTopic),
        englishTopicB: asPhraseList(item.englishTopicB ?? item.b?.englishTopic),
        englishExplanationA: asPhraseList(item.englishExplanationA ?? item.a?.englishExplanation),
        englishExplanationB: asPhraseList(item.englishExplanationB ?? item.b?.englishExplanation),
      };
      if (!pair.topicA || !pair.topicB || !pair.explanationA || !pair.explanationB) {
        throw new Error(`Cặp ${index + 1} thiếu chủ đề hoặc phần giải thích.`);
      }
      return pair;
    });
    stopPreview();
    invalidateTts();
    renderEditors();
    renderAt(0);
    setJsonStatus(`Đã nạp ${comparisons.length} cặp phân biệt.`, 'ok');
  } catch (error) {
    setJsonStatus(`Không nạp được JSON: ${error.message}`, 'error');
  } finally {
    elements.projectJson.value = '';
  }
}

elements.addComparison.addEventListener('click', () => {
  comparisons.push(defaultPair());
  invalidateTts();
  renderEditors();
});
elements.play.addEventListener('click', togglePlay);
elements.restart.addEventListener('click', restart);
elements.connectTts.addEventListener('click', loadTtsVoices);
elements.generateTts.addEventListener('click', generateAllTts);
elements.exportVideo.addEventListener('click', exportVideo);
elements.ttsServerUrl.addEventListener('change', loadTtsVoices);
elements.projectJson.addEventListener('change', () => importProjectJson(elements.projectJson.files[0]));
elements.generateAi.addEventListener('click', generateComparisonsWithAi);

renderEditors();
renderAt(0);
loadTtsVoices();
