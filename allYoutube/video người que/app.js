const $ = (selector) => document.querySelector(selector);

const elements = {
  jsonFile: $('#json-file'),
  jsonPaste: $('#json-paste'),
  loadPastedJson: $('#load-pasted-json'),
  jsonStatus: $('#json-status'),
  chainTexts: $('#chain-texts'),
  chainFontSize: $('#chain-font-size'),
  chainLayout: $('#chain-layout'),
  createChainScenes: $('#create-chain-scenes'),
  chainStatus: $('#chain-status'),
  sceneList: $('#scene-list'),
  addScene: $('#add-scene'),
  duplicateScene: $('#duplicate-scene'),
  deleteScene: $('#delete-scene'),
  activeTitle: $('#active-title'),
  bgColor: $('#bg-color'),
  fontColor: $('#font-color'),
  fontFamily: $('#font-family'),
  defaultFontSize: $('#default-font-size'),
  showGuides: $('#show-guides'),
  stage: $('#stage'),
  stickman: $('#stickman'),
  textLayer: $('#scene-text-layer'),
  arrowLayer: $('#scene-arrow-layer'),
  arrowPopover: $('#arrow-popover'),
  scenePrompt: $('#scene-prompt'),
  stickmanPose: $('#stickman-pose'),
  showStickman: $('#show-stickman'),
  addText: $('#add-text'),
  addArrow: $('#add-arrow'),
  addImage: $('#add-image'),
  layerEditor: $('#layer-editor'),
  stickyTitleInput: $('#sticky-title-input'),
  addTitleScene: $('#add-title-scene'),
  startStickyTitle: $('#start-sticky-title'),
  stopStickyTitle: $('#stop-sticky-title'),
  stickmanLibraryGroups: document.querySelectorAll('[data-library-group]'),
};

const stickmanLibrary = {
  expression: [
    { name: 'Vui', note: 'Biểu cảm vui vẻ', src: 'ảnh ng que/biểu cảm/vui.svg' },
    { name: 'Ngạc nhiên', note: 'Biểu cảm ngạc nhiên', src: 'ảnh ng que/biểu cảm/ngac-nhien.svg' },
  ],
  action: [
    { name: 'Chỉ vai', note: 'Hành động nhún vai', src: 'ảnh ng que/hành động/chie-vai.svg' },
    { name: 'Chỉ hướng', note: 'Hành động chỉ tay', src: 'ảnh ng que/hành động/chi-vao.svg' },
  ],
};

let state = {
  background: '#efe3bd',
  fontColor: '#090807',
  fontFamily: 'Trebuchet MS, Segoe UI, sans-serif',
  defaultFontSize: 58,
  showGuides: true,
  activeScene: 0,
  scenes: [
    {
      id: crypto.randomUUID(),
      prompt: 'Bạn học tiếng Anh từ hồi tiểu học.',
      pose: 'point',
      showStickman: true,
      stickman: { id: 'stickman', x: 50, y: 92, scale: 1 },
      texts: [{ id: crypto.randomUUID(), value: 'Tiểu học', x: 8, y: 62, size: 58 }],
      images: [],
      arrows: [],
    },
  ],
};

let dragState = null;
let selectedTextId = null;
let draftArrow = null;
let arrowPickStart = null;
let suppressNextClick = false;

function sceneTitle(scene, index) {
  const text = scene.prompt || scene.text || `Cảnh ${index + 1}`;
  return text.length > 42 ? `${text.slice(0, 42)}...` : text;
}

function normalizeScene(raw, index) {
  const prompt = typeof raw === 'string'
    ? raw
    : raw.prompt || raw.text || raw.voiceText || raw.caption || raw.title || raw.content || `Cảnh ${index + 1}`;
  return {
    id: crypto.randomUUID(),
    prompt: String(prompt || '').trim(),
    pose: 'point',
    showStickman: true,
    stickman: { id: 'stickman', x: 50, y: 92, scale: 1 },
    texts: [],
    images: [],
    arrows: [],
  };
}

function extractScenes(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data.scenes)) return data.scenes;
  if (Array.isArray(data.segments)) return data.segments;
  if (Array.isArray(data.items)) return data.items;
  if (typeof data.content === 'string') {
    return data.content
      .split(/(?<=[.!?。！？])\s+|\n+/u)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function importJson(data) {
  const rawScenes = extractScenes(data);
  if (!rawScenes.length) throw new Error('JSON chưa có scenes, segments, items hoặc content.');
  state.scenes = rawScenes.map(normalizeScene);
  state.activeScene = 0;
  render();
  elements.jsonStatus.textContent = `Đã nạp ${state.scenes.length} cảnh từ JSON.`;
}

function activeScene() {
  return state.scenes[state.activeScene];
}

function cloneScene(scene) {
  return {
    ...structuredClone(scene),
    id: crypto.randomUUID(),
    texts: scene.texts.map((item) => ({ ...item, id: crypto.randomUUID() })),
    images: (scene.images || []).map((item) => ({ ...item, id: crypto.randomUUID() })),
    arrows: scene.arrows.map((item) => ({ ...item, id: crypto.randomUUID() })),
  };
}

function textConnection(fromText, toText) {
  return {
    id: crypto.randomUUID(),
    mode: 'linked',
    fromTextId: fromText.id,
    toTextId: toText.id,
    curve: 0.38,
  };
}

function chainPositions(count, layout) {
  if (layout === 'line') {
    const start = count === 1 ? 50 : 10;
    const gap = count <= 1 ? 0 : 80 / (count - 1);
    return Array.from({ length: count }, (_, index) => ({
      x: start + gap * index,
      y: 18,
    }));
  }

  const presets = {
    1: [{ x: 42, y: 18 }],
    2: [{ x: 20, y: 20 }, { x: 60, y: 20 }],
    3: [{ x: 10, y: 56 }, { x: 38, y: 18 }, { x: 68, y: 56 }],
    4: [{ x: 8, y: 58 }, { x: 28, y: 18 }, { x: 58, y: 18 }, { x: 76, y: 58 }],
  };
  if (presets[count]) return presets[count];

  const center = { x: 50, y: 42 };
  const radius = { x: 36, y: 28 };
  return Array.from({ length: count }, (_, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index / count);
    return {
      x: Math.round(center.x + Math.cos(angle) * radius.x - 8),
      y: Math.round(center.y + Math.sin(angle) * radius.y),
    };
  });
}

function freeArrowBetween(from, to) {
  const rect = elements.stage?.getBoundingClientRect() || { width: 1120, height: 630 };
  const dxPercent = to.x - from.x;
  const dyPercent = to.y - from.y;
  const dxPx = dxPercent * rect.width / 100;
  const dyPx = dyPercent * rect.height / 100;
  const arrowHeightPercent = 42 / rect.height * 100;
  return {
    id: crypto.randomUUID(),
    x: from.x,
    y: from.y - arrowHeightPercent / 2,
    length: Math.max(42, Math.min(900, Math.hypot(dxPx, dyPx))),
    rotate: Math.round(Math.atan2(dyPx, dxPx) * 180 / Math.PI),
  };
}

function createChainScenes() {
  resetArrowPicking();
  elements.addArrow.textContent = 'Thêm mũi tên';
  const labels = elements.chainTexts.value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 8);
  if (!labels.length) {
    elements.chainStatus.textContent = 'Nhập ít nhất 1 text.';
    return;
  }

  const size = Number(elements.chainFontSize.value) || state.defaultFontSize;
  const positions = chainPositions(labels.length, elements.chainLayout.value);
  const nextScenes = labels.map((label, sceneIndex) => {
    const visibleLabels = labels.slice(0, sceneIndex + 1);
    const texts = visibleLabels.map((value, index) => ({
      id: crypto.randomUUID(),
      value,
      x: positions[index].x,
      y: positions[index].y,
      size,
    }));
    return {
      id: crypto.randomUUID(),
      prompt: label,
      pose: sceneIndex % 3 === 0 ? 'point' : 'explain',
      showStickman: true,
      stickman: { id: 'stickman', x: 50, y: 93, scale: 0.92 },
      texts,
      images: [],
      arrows: [],
    };
  });

  const insertAt = state.scenes.length;
  state.scenes = state.scenes.concat(nextScenes);
  state.activeScene = insertAt;
  elements.chainStatus.textContent = `Đã thêm ${nextScenes.length} cảnh mới từ ${labels.length} text.`;
  render();
}

function setActiveScene(index) {
  state.activeScene = Math.max(0, Math.min(index, state.scenes.length - 1));
  render();
}

function renderSceneList() {
  elements.sceneList.innerHTML = state.scenes.map((scene, index) => `
    <button class="scene-item${index === state.activeScene ? ' active' : ''}" type="button" data-scene-index="${index}">
      <strong>${index + 1}. ${escapeHtml(sceneTitle(scene, index))}</strong>
      <span>${scene.isTitleScene ? 'cảnh tiêu đề, ' : ''}${scene.stickyTitleStart ? 'bắt đầu ghim, ' : ''}${scene.stickyTitleStop ? 'tắt ghim, ' : ''}${scene.texts.length} text, ${scene.arrows.length} mũi tên</span>
    </button>
  `).join('');
}

function activeStickyTitle(index) {
  let title = '';
  for (let i = 0; i <= index; i += 1) {
    const scene = state.scenes[i];
    if (scene.stickyTitleStop) title = '';
    if (scene.stickyTitleStart) title = scene.stickyTitleStart;
  }
  return title;
}

function textAnchor(text, side) {
  const width = Math.min(34, Math.max(12, text.value.length * text.size * 0.045));
  const height = Math.max(5, text.size * 0.08);
  const centerX = text.x + width / 2;
  const centerY = text.y + height / 2;
  if (side === 'left') return { x: text.x, y: centerY };
  if (side === 'right') return { x: text.x + width, y: centerY };
  if (side === 'top') return { x: centerX, y: text.y };
  if (side === 'bottom') return { x: centerX, y: text.y + height };
  return { x: centerX, y: centerY };
}

function linkedArrowPath(arrow, scene) {
  const fromText = scene.texts.find((text) => text.id === arrow.fromTextId);
  const toText = scene.texts.find((text) => text.id === arrow.toTextId);
  if (!fromText || !toText) return '';

  const start = textAnchor(fromText, 'right');
  const end = textAnchor(toText, 'left');
  const dx = end.x - start.x;
  const arch = Math.min(34, Math.max(10, Math.abs(dx) * Number(arrow.curve ?? 0.38)));
  const c = {
    x: start.x + dx * 0.5,
    y: Math.min(start.y, end.y) - arch,
  };
  return `<path class="arrow-path" d="M ${start.x.toFixed(2)} ${start.y.toFixed(2)} Q ${c.x.toFixed(2)} ${c.y.toFixed(2)} ${end.x.toFixed(2)} ${end.y.toFixed(2)}" />`;
}

function pointArrowPath(arrow, className = 'arrow-path') {
  const start = arrow.start;
  const end = arrow.end;
  return `<path class="${className}" d="M ${start.x.toFixed(2)} ${start.y.toFixed(2)} L ${end.x.toFixed(2)} ${end.y.toFixed(2)}" />`;
}

function freeArrowImage(arrow) {
  return `<img class="stage-arrow draggable-layer" data-kind="arrows" data-id="${arrow.id}" src="asscess/sharp/arrow-red.svg" alt="" style="--x:${arrow.x}%;--y:${arrow.y}%;--len:${arrow.length}px;--rot:${arrow.rotate}deg">`;
}

function renderStage(scene) {
  elements.stage.style.background = state.background;
  elements.stage.style.setProperty('--font-color', state.fontColor);
  elements.stage.style.setProperty('--text-font', state.fontFamily);
  elements.stage.style.setProperty('--default-text-size', `${state.defaultFontSize}px`);
  elements.stage.classList.toggle('show-guides', state.showGuides);
  elements.stickman.className = `stickman draggable-layer pose-${scene.pose}`;
  elements.stickman.classList.toggle('hidden-layer', scene.showStickman === false);
  elements.stickman.dataset.kind = 'stickman';
  elements.stickman.dataset.id = 'stickman';
  elements.stickman.style.setProperty('--x', `${scene.stickman.x}%`);
  elements.stickman.style.setProperty('--y', `${scene.stickman.y}%`);
  elements.stickman.style.setProperty('--scale', scene.stickman.scale);

  const stickyTitle = !scene.isTitleScene ? activeStickyTitle(state.activeScene) : '';
  const stickyTitleLayer = stickyTitle ? `
    <div class="sticky-title">${escapeHtml(stickyTitle)}</div>
  ` : '';

  const imageLayers = (scene.images || []).map((image) => `
    <img class="stage-image draggable-layer" data-kind="images" data-id="${image.id}" src="${escapeAttr(image.src)}" alt="" style="--x:${image.x}%;--y:${image.y}%;--w:${image.width}%">
  `).join('');

  elements.textLayer.innerHTML = `${stickyTitleLayer}${imageLayers}${scene.texts.map((text) => `
    <div class="stage-text draggable-layer" data-kind="texts" data-id="${text.id}" style="--x:${text.x}%;--y:${text.y}%;--size:${text.size || state.defaultFontSize}px;--item-font:${escapeAttr(text.fontFamily || state.fontFamily)}">${escapeHtml(text.value)}</div>
  `).join('')}`;

  const draftArrowImage = draftArrow ? freeArrowImage(draftArrow) : '';
  const freeArrows = scene.arrows.filter((arrow) => arrow.mode !== 'point').map((arrow) => (
    arrow.mode === 'linked' ? null : freeArrowImage(arrow)
  )).filter(Boolean).join('');
  elements.arrowLayer.innerHTML = `
    <svg class="linked-arrow-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="arrow-head" markerWidth="8" markerHeight="8" refX="6.2" refY="4" orient="auto" markerUnits="strokeWidth">
          <path d="M 0 0 L 8 4 L 0 8 Z" fill="#d71920"></path>
        </marker>
      </defs>
    </svg>
    ${draftArrowImage}
    ${freeArrows}
  `;
  syncArrowPopover();
}

function layerEditor(scene) {
  const stickmanRows = `
    <div class="layer-card" data-kind="stickman" data-layer-index="0">
      <div class="layer-row">
        <span class="linked-note">Người que</span>
        <label class="layer-field small">X
          <input type="number" data-field="x" value="${scene.stickman.x}" min="0" max="100">
        </label>
        <label class="layer-field small">Y
          <input type="number" data-field="y" value="${scene.stickman.y}" min="0" max="100">
        </label>
        <label class="layer-field small">Tỉ lệ
          <input type="number" data-field="scale" value="${scene.stickman.scale}" min="0.2" max="2.2" step="0.05">
        </label>
      </div>
    </div>
  `;

  const imageRows = (scene.images || []).map((image, index) => `
    <div class="layer-card" data-kind="images" data-layer-index="${index}">
      <div class="layer-row">
        <label class="layer-field">Ảnh
          <input type="text" data-field="src" value="${escapeAttr(image.src)}" placeholder="Dán URL ảnh hoặc dùng nút Thêm ảnh">
        </label>
        <label class="layer-field small">X
          <input type="number" data-field="x" value="${image.x}" min="0" max="100">
        </label>
        <label class="layer-field small">Y
          <input type="number" data-field="y" value="${image.y}" min="0" max="100">
        </label>
        <label class="layer-field small">Rộng
          <input type="number" data-field="width" value="${image.width}" min="5" max="100">
        </label>
        <button type="button" data-remove-layer>Xóa</button>
      </div>
    </div>
  `).join('');

  const textRows = scene.texts.map((text, index) => `
    <div class="layer-card" data-kind="texts" data-layer-index="${index}">
      <div class="layer-row">
        <label class="layer-field">Text
          <input type="text" data-field="value" value="${escapeAttr(text.value)}">
        </label>
        <label class="layer-field small">X
          <input type="number" data-field="x" value="${text.x}" min="0" max="100">
        </label>
        <label class="layer-field small">Y
          <input type="number" data-field="y" value="${text.y}" min="0" max="100">
        </label>
        <label class="layer-field small">Cỡ
          <input type="number" data-field="size" value="${text.size || ''}" min="16" max="160" placeholder="${state.defaultFontSize}">
        </label>
        <label class="layer-field">Font riêng
          <select data-field="fontFamily">
            <option value="" ${!text.fontFamily ? 'selected' : ''}>Theo mặc định</option>
            <option value="Trebuchet MS, Segoe UI, sans-serif" ${text.fontFamily === 'Trebuchet MS, Segoe UI, sans-serif' ? 'selected' : ''}>Đậm rõ</option>
            <option value="Comic Sans MS, Comic Sans, cursive" ${text.fontFamily === 'Comic Sans MS, Comic Sans, cursive' ? 'selected' : ''}>Viết tay tròn</option>
            <option value="Chalkboard SE, Comic Sans MS, cursive" ${text.fontFamily === 'Chalkboard SE, Comic Sans MS, cursive' ? 'selected' : ''}>Bảng phấn</option>
            <option value="Marker Felt, Comic Sans MS, cursive" ${text.fontFamily === 'Marker Felt, Comic Sans MS, cursive' ? 'selected' : ''}>Bút marker</option>
            <option value="Bradley Hand, Comic Sans MS, cursive" ${text.fontFamily === 'Bradley Hand, Comic Sans MS, cursive' ? 'selected' : ''}>Viết tay mềm</option>
          </select>
        </label>
        <button type="button" data-remove-layer>Xóa</button>
      </div>
    </div>
  `).join('');

  const arrowRows = scene.arrows.map((arrow, index) => arrow.mode === 'linked' ? `
    <div class="layer-card" data-kind="arrows" data-layer-index="${index}">
      <div class="layer-row">
        <label class="layer-field small">Cong
          <input type="number" data-field="curve" value="${arrow.curve ?? 0.22}" min="-0.8" max="0.8" step="0.05">
        </label>
        <span class="linked-note">Mũi tên bám theo text nguồn và text đích.</span>
        <button type="button" data-remove-layer>Xóa</button>
      </div>
    </div>
  ` : arrow.mode === 'point' ? `
    <div class="layer-card" data-kind="arrows" data-layer-index="${index}">
      <div class="layer-row">
        <span class="linked-note">Mũi tên vẽ tay từ text ra vị trí chuột.</span>
        <button type="button" data-remove-layer>Xóa</button>
      </div>
    </div>
  ` : `
    <div class="layer-card" data-kind="arrows" data-layer-index="${index}">
      <div class="layer-row">
        <label class="layer-field small">X
          <input type="number" data-field="x" value="${arrow.x}" min="0" max="100">
        </label>
        <label class="layer-field small">Y
          <input type="number" data-field="y" value="${arrow.y}" min="0" max="100">
        </label>
        <label class="layer-field small">Dài
          <input type="number" data-field="length" value="${arrow.length}" min="40" max="420">
        </label>
        <label class="layer-field small">Xoay
          <input type="number" data-field="rotate" value="${arrow.rotate}" min="-180" max="180">
        </label>
        <button type="button" data-remove-layer>Xóa</button>
      </div>
    </div>
  `).join('');

  elements.layerEditor.innerHTML = `${stickmanRows}${imageRows}${textRows}${arrowRows}` || '<p class="hint">Cảnh này chưa có layer.</p>';
}

function renderStickmanLibrary() {
  document.querySelectorAll('[data-library-group]').forEach((container) => {
    const group = container.dataset.libraryGroup;
    const items = stickmanLibrary[group] || [];
    container.innerHTML = items.map((item) => `
      <button class="stickman-preview" type="button" data-stickman-src="${escapeAttr(item.src)}" data-stickman-name="${escapeAttr(item.name)}">
        <img src="${escapeAttr(item.src)}" alt="${escapeAttr(item.name)}">
        <strong>${escapeHtml(item.name)}</strong>
        <span>${escapeHtml(item.note)}</span>
      </button>
    `).join('');
  });
}

function render() {
  const scene = activeScene();
  elements.bgColor.value = state.background;
  elements.fontColor.value = state.fontColor;
  elements.fontFamily.value = state.fontFamily;
  elements.defaultFontSize.value = state.defaultFontSize;
  elements.activeTitle.textContent = `Cảnh ${state.activeScene + 1}`;
  elements.scenePrompt.value = scene.prompt;
  elements.stickmanPose.value = scene.pose;
  elements.showStickman.checked = scene.showStickman !== false;
  elements.stickyTitleInput.value = scene.stickyTitleStart || activeStickyTitle(state.activeScene) || '';
  renderSceneList();
  renderStage(scene);
  layerEditor(scene);
  renderStickmanLibrary();
}

function syncArrowPopover() {
  elements.arrowPopover.classList.remove('show');
}

function resetArrowPicking() {
  draftArrow = null;
  arrowPickStart = null;
  elements.stage.classList.remove('picking-arrow-target');
}

function createLinkedArrow(fromTextId, toTextId) {
  if (!fromTextId || !toTextId || fromTextId === toTextId) return;
  const scene = activeScene();
  const fromText = scene.texts.find((text) => text.id === fromTextId);
  const toText = scene.texts.find((text) => text.id === toTextId);
  if (!fromText || !toText) return;
  scene.arrows.push(textConnection(fromText, toText));
  selectedTextId = null;
  resetArrowPicking();
  render();
}

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, '&quot;');
}

async function handleFile(file) {
  try {
    importJson(JSON.parse(await file.text()));
  } catch (error) {
    elements.jsonStatus.textContent = error.message;
  }
}

elements.jsonFile.addEventListener('change', (event) => {
  const [file] = event.target.files;
  if (file) handleFile(file);
});

elements.loadPastedJson.addEventListener('click', () => {
  try {
    importJson(JSON.parse(elements.jsonPaste.value));
  } catch (error) {
    elements.jsonStatus.textContent = error.message;
  }
});

elements.createChainScenes.addEventListener('click', createChainScenes);

elements.sceneList.addEventListener('click', (event) => {
  const item = event.target.closest('[data-scene-index]');
  if (item) setActiveScene(Number(item.dataset.sceneIndex));
});

elements.addScene.addEventListener('click', () => {
  state.scenes.push(normalizeScene('', state.scenes.length));
  setActiveScene(state.scenes.length - 1);
});

elements.duplicateScene.addEventListener('click', () => {
  state.scenes.splice(state.activeScene + 1, 0, cloneScene(activeScene()));
  setActiveScene(state.activeScene + 1);
});

elements.deleteScene.addEventListener('click', () => {
  if (state.scenes.length === 1) return;
  state.scenes.splice(state.activeScene, 1);
  setActiveScene(state.activeScene);
});

elements.bgColor.addEventListener('input', (event) => {
  state.background = event.target.value;
  renderStage(activeScene());
});

elements.fontColor.addEventListener('input', (event) => {
  state.fontColor = event.target.value;
  renderStage(activeScene());
});

elements.fontFamily.addEventListener('change', (event) => {
  state.fontFamily = event.target.value;
  renderStage(activeScene());
});

elements.defaultFontSize.addEventListener('input', (event) => {
  state.defaultFontSize = Number(event.target.value) || 58;
  render();
});

elements.showGuides.addEventListener('change', (event) => {
  state.showGuides = event.target.checked;
  renderStage(activeScene());
});

elements.scenePrompt.addEventListener('input', (event) => {
  activeScene().prompt = event.target.value;
  renderSceneList();
});

elements.stickmanPose.addEventListener('change', (event) => {
  activeScene().pose = event.target.value;
  renderStage(activeScene());
});

elements.showStickman.addEventListener('change', (event) => {
  activeScene().showStickman = event.target.checked;
  render();
});

elements.addTitleScene.addEventListener('click', () => {
  const title = elements.stickyTitleInput.value.trim();
  if (!title) return;
  const titleScene = {
    id: crypto.randomUUID(),
    prompt: title,
    pose: 'point',
    isTitleScene: true,
    stickyTitleStart: title,
    showStickman: false,
    stickman: { id: 'stickman', x: 50, y: 92, scale: 1 },
    texts: [{ id: crypto.randomUUID(), value: title, x: 24, y: 20, size: 56, fontFamily: state.fontFamily }],
    images: [],
    arrows: [],
  };
  state.scenes.splice(state.activeScene + 1, 0, titleScene);
  setActiveScene(state.activeScene + 1);
});

elements.startStickyTitle.addEventListener('click', () => {
  const title = elements.stickyTitleInput.value.trim();
  if (!title) return;
  const scene = activeScene();
  scene.stickyTitleStart = title;
  scene.stickyTitleStop = false;
  render();
});

elements.stopStickyTitle.addEventListener('click', () => {
  const scene = activeScene();
  scene.stickyTitleStop = true;
  delete scene.stickyTitleStart;
  render();
});

elements.addText.addEventListener('click', () => {
  activeScene().texts.push({ id: crypto.randomUUID(), value: 'Text mới', x: 12, y: 18 });
  render();
});

elements.addImage.addEventListener('click', () => {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.addEventListener('change', () => {
    const [file] = input.files;
    if (!file) return;
    activeScene().images = activeScene().images || [];
    activeScene().images.push({
      id: crypto.randomUUID(),
      src: URL.createObjectURL(file),
      x: 50,
      y: 50,
      width: 34,
    });
    render();
  }, { once: true });
  input.click();
});

document.querySelectorAll('[data-library-group]').forEach((container) => {
  container.addEventListener('click', (event) => {
    const button = event.target.closest('[data-stickman-src]');
    if (!button) return;
    const scene = activeScene();
    scene.images = scene.images || [];
    scene.images.push({
      id: crypto.randomUUID(),
      src: button.dataset.stickmanSrc,
      x: 50,
      y: 52,
      width: 30,
    });
    render();
  });
});

elements.addArrow.addEventListener('click', () => {
  selectedTextId = null;
  arrowPickStart = null;
  draftArrow = null;
  elements.arrowPopover.classList.remove('show');
  elements.stage.classList.add('picking-arrow-target');
  elements.addArrow.textContent = 'Click gốc mũi tên...';
});

elements.arrowPopover.addEventListener('click', (event) => {
  event.stopPropagation();
  elements.arrowPopover.classList.remove('show');
});

elements.arrowPopover.addEventListener('pointerdown', (event) => {
  event.stopPropagation();
});

elements.layerEditor.addEventListener('input', (event) => {
  const card = event.target.closest('.layer-card');
  if (!card) return;
  const scene = activeScene();
  const layer = card.dataset.kind === 'stickman'
    ? scene.stickman
    : scene[card.dataset.kind][Number(card.dataset.layerIndex)];
  const field = event.target.dataset.field;
  if (field === 'value' || field === 'fontFamily') {
    layer[field] = event.target.value;
  } else if (field === 'size' && event.target.value === '') {
    delete layer.size;
  } else {
    layer[field] = Number(event.target.value);
  }
  renderStage(scene);
});

elements.layerEditor.addEventListener('click', (event) => {
  const card = event.target.closest('.layer-card');
  if (!event.target.matches('[data-remove-layer]') || !card) return;
  if (card.dataset.kind === 'stickman') return;
  activeScene()[card.dataset.kind].splice(Number(card.dataset.layerIndex), 1);
  render();
});

function stagePoint(event) {
  const rect = elements.stage.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * 100,
    y: ((event.clientY - rect.top) / rect.height) * 100,
  };
}

function findLayer(kind, id) {
  if (kind === 'stickman') return activeScene().stickman;
  return activeScene()[kind].find((layer) => layer.id === id);
}

elements.stage.addEventListener('pointerdown', (event) => {
  if (elements.stage.classList.contains('picking-arrow-target')) return;
  if (event.button === 2) return;
  const target = event.target.closest('.draggable-layer');
  if (!target) return;
  const layer = findLayer(target.dataset.kind, target.dataset.id);
  if (!layer) return;

  event.preventDefault();
  target.setPointerCapture(event.pointerId);
  const point = stagePoint(event);
  dragState = {
    pointerId: event.pointerId,
    kind: target.dataset.kind,
    id: target.dataset.id,
    offsetX: point.x - layer.x,
    offsetY: point.y - layer.y,
    startX: point.x,
    startY: point.y,
  };
  target.classList.add('is-dragging');
});

elements.stage.addEventListener('pointermove', (event) => {
  if (arrowPickStart) {
    draftArrow = freeArrowBetween(arrowPickStart, stagePoint(event));
    renderStage(activeScene());
    return;
  }
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  const layer = findLayer(dragState.kind, dragState.id);
  if (!layer) return;
  const point = stagePoint(event);
  if (Math.hypot(point.x - dragState.startX, point.y - dragState.startY) > 1) {
    suppressNextClick = true;
  }
  layer.x = Math.max(0, Math.min(100, Math.round(point.x - dragState.offsetX)));
  layer.y = Math.max(0, Math.min(100, Math.round(point.y - dragState.offsetY)));
  renderStage(activeScene());
});

elements.stage.addEventListener('pointerup', (event) => {
  if (elements.stage.classList.contains('picking-arrow-target')) {
    const point = stagePoint(event);
    if (!arrowPickStart) {
      arrowPickStart = point;
      elements.addArrow.textContent = 'Click đích mũi tên...';
      return;
    }
    activeScene().arrows.push(freeArrowBetween(arrowPickStart, point));
    resetArrowPicking();
    elements.addArrow.textContent = 'Thêm mũi tên';
    render();
    return;
  }
  if (suppressNextClick) {
    suppressNextClick = false;
    return;
  }
  if (event.button === 2) return;
  const textNode = event.target.closest('.stage-text');
  if (!textNode) {
    selectedTextId = null;
    resetArrowPicking();
    syncArrowPopover();
    return;
  }
  selectedTextId = textNode.dataset.id;
  syncArrowPopover();
});

elements.stage.addEventListener('contextmenu', (event) => {
  event.preventDefault();
});

function endDrag(event) {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  dragState = null;
  layerEditor(activeScene());
}

elements.stage.addEventListener('pointerup', endDrag);
elements.stage.addEventListener('pointercancel', endDrag);

render();
