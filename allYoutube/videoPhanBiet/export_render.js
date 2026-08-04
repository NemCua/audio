const CHARACTER_IMAGES = {
  'point-a': 'assets/character-point-up-left.png',
  'point-b': 'assets/character-point-up-right.png',
  question: 'assets/character-question.png',
  'explain-a': 'assets/character-point-up-left.png',
  'explain-b': 'assets/character-point-up-right.png',
};

let comparisons = [];
let segments = [];

function escapeHtml(text) {
  const node = document.createElement('div');
  node.textContent = String(text ?? '');
  return node.innerHTML;
}

function splitSubtitle(text, maxWords = 16, maxLines = 2) {
  const parts = String(text ?? '').replace(/\s+/g, ' ').trim().split(/(?<=[.!?。！？])\s+/).filter(Boolean);
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
  return lines.length ? `${title} — ${lines.join(' ')}` : title;
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

function waitImagesLoaded(container) {
  const imgs = Array.from(container.querySelectorAll('img'));
  return Promise.all(imgs.map((img) => {
    if (img.complete && img.naturalWidth > 0) return Promise.resolve();
    return (img.decode ? img.decode() : new Promise((resolve) => {
      img.onload = resolve;
      img.onerror = resolve;
    })).catch(() => {});
  }));
}

window.setExportProject = (payload) => {
  comparisons = payload.comparisons || [];
  segments = payload.segments || [];
};

window.renderExportSegment = async (index) => {
  const segment = segments[index];
  if (!segment) return false;
  const pair = comparisons[segment.comparisonIndex] || comparisons[0] || {};
  const topicA = pair.topicA || 'Chủ đề A';
  const topicB = pair.topicB || 'Chủ đề B';
  const stage = document.getElementById('video-stage');

  stage.dataset.scene = segment.scene;
  document.getElementById('preview-topic-a').textContent = topicA;
  document.getElementById('preview-topic-b').textContent = topicB;
  showPreviewImage(document.getElementById('preview-image-a'), document.getElementById('preview-text-a'), pair.imageA, pair.useTextA, topicA);
  showPreviewImage(document.getElementById('preview-image-b'), document.getElementById('preview-text-b'), pair.imageB, pair.useTextB, topicB);

  const characterSrc = CHARACTER_IMAGES[segment.scene];
  document.getElementById('character-image').src = characterSrc;
  const copy = {
    'point-a': `Đây là <b>${escapeHtml(topicA)}</b>`,
    'point-b': `Đây là <b>${escapeHtml(topicB)}</b>`,
    question: `Vậy <b>${escapeHtml(topicA)}</b> và <b>${escapeHtml(topicB)}</b> khác nhau thế nào?`,
    'explain-a': explanationCaption(topicA, pair.explanationA),
    'explain-b': explanationCaption(topicB, pair.explanationB),
  };
  document.getElementById('caption').innerHTML = copy[segment.scene] || '';
  await waitImagesLoaded(stage);
  return true;
};
