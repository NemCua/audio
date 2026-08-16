const { app, BrowserWindow, ipcMain: rawIpcMain, shell, session, desktopCapturer, dialog, safeStorage } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { pathToFileURL, fileURLToPath } = require('node:url');
const { exportProject } = require('./exporter');

const isDev = !app.isPackaged;

function threadsRoot() {
  return isDev
    ? path.resolve(__dirname, '..', 'threads')
    : path.join(process.resourcesPath, 'threads');
}

function trustedRenderer(event) {
  try {
    const framePath = fileURLToPath(event.senderFrame.url);
    return path.resolve(framePath) === path.resolve(threadsRoot(), 'index.html');
  } catch (_error) { return false; }
}

// Mọi handler bên dưới đều từ chối frame phụ/trang đã điều hướng. Điều này
// giữ lớp bảo vệ ngay cả khi renderer vô tình dính XSS trong tương lai.
const ipcMain = Object.freeze({
  handle(channel, listener) {
    rawIpcMain.handle(channel, (event, ...args) => {
      if (!trustedRenderer(event)) throw new Error('IPC bị từ chối từ renderer không tin cậy');
      return listener(event, ...args);
    });
  },
});

function allowedApiBase(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if (!local && url.origin !== 'https://threads-api-s13z.onrender.com') return null;
    return url.origin;
  } catch (_error) { return null; }
}

function bytesBuffer(bytes, maxBytes, label) {
  const buffer = Buffer.from(bytes || []);
  if (!buffer.length || buffer.length > maxBytes) throw new Error(`${label} vượt giới hạn cho phép`);
  return buffer;
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    backgroundColor: '#0a0c14',
    title: 'Threads Video Maker',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', event => event.preventDefault());
  // Dev app dùng API/TTS local để sửa và test sát Electron production nhưng
  // không gọi dịch vụ online. Bản đóng gói không có query này.
  win.loadFile(path.join(threadsRoot(), 'index.html'), isDev ? { query: { localApi: '1' } } : undefined);
  return win;
}

function ffmpegPath() {
  if (isDev) return require('ffmpeg-static');
  return path.join(process.resourcesPath, 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
}

function authStorePath() { return path.join(app.getPath('userData'), 'auth-session.json'); }
let volatileRefreshToken = null;

function storeRefreshToken(refreshToken) {
  const token = String(refreshToken || '');
  if (!token) throw new Error('Refresh token không hợp lệ');
  volatileRefreshToken = token;
  if (!safeStorage.isEncryptionAvailable()) {
    fs.rmSync(authStorePath(), { force: true });
    return { persistent: false };
  }
  try {
    const encrypted = safeStorage.encryptString(token);
    fs.writeFileSync(authStorePath(), JSON.stringify({ token: encrypted.toString('base64') }), { mode: 0o600 });
    return { persistent: true };
  } catch (_error) {
    fs.rmSync(authStorePath(), { force: true });
    return { persistent: false };
  }
}

function readRefreshToken() {
  if (volatileRefreshToken) return volatileRefreshToken;
  if (!safeStorage.isEncryptionAvailable()) return null;
  try {
    const payload = JSON.parse(fs.readFileSync(authStorePath(), 'utf8'));
    return safeStorage.decryptString(Buffer.from(payload.token, 'base64'));
  } catch (_error) {
    fs.rmSync(authStorePath(), { force: true });
    return null;
  }
}

function workspaceRoot() {
  const root = path.join(app.getPath('documents'), 'Threads Video Maker');
  for (const child of ['Projects', path.join('Media', 'TTS'), path.join('Media', 'Images'), path.join('Media', 'Audio'), path.join('Media', 'Video'), path.join('Media', 'Stickers'), 'Exports']) {
    fs.mkdirSync(path.join(root, child), { recursive: true });
  }
  return root;
}

function safeAssetName(name, fallback) {
  const parsed = path.parse(String(name || fallback));
  const base = (parsed.name || fallback).replace(/[^\p{L}\p{N}._-]+/gu, '-').slice(0, 80);
  const ext = parsed.ext.replace(/[^a-zA-Z0-9.]/g, '').slice(0, 10);
  return `${base || fallback}-${Date.now()}-${randomUUID().slice(0, 8)}${ext}`;
}

function assetDirectory(kind) {
  const folders = { tts: path.join('Media', 'TTS'), image: path.join('Media', 'Images'), audio: path.join('Media', 'Audio'), video: path.join('Media', 'Video') };
  return path.join(workspaceRoot(), folders[kind] || path.join('Media', 'Images'));
}

function localAssetResult(filePath) {
  return { path: filePath, url: pathToFileURL(filePath).href };
}

function stickerUserRoot() { return path.join(workspaceRoot(), 'Media', 'Stickers'); }
function stickerOverridesPath() { return path.join(stickerUserRoot(), '_packs.json'); }
function hiddenStickerPacksPath() { return path.join(stickerUserRoot(), '_hidden-packs.json'); }
function readStickerOverrides() {
  try { return JSON.parse(fs.readFileSync(stickerOverridesPath(), 'utf8')); } catch (_error) { return {}; }
}
function writeStickerOverrides(value) { fs.writeFileSync(stickerOverridesPath(), JSON.stringify(value, null, 2), 'utf8'); }
function readHiddenStickerPacks() { try { return new Set(JSON.parse(fs.readFileSync(hiddenStickerPacksPath(), 'utf8'))); } catch (_error) { return new Set(); } }
function writeHiddenStickerPacks(value) { fs.writeFileSync(hiddenStickerPacksPath(), JSON.stringify([...value], null, 2), 'utf8'); }
function safePackId(value) { return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 60); }
function stickerFiles(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isFile() && !entry.name.startsWith('_') && /\.(png|jpe?g|webp|gif)$/i.test(entry.name))
    .map(entry => {
      const filePath = path.join(root, entry.name);
      return { filename: entry.name, url: pathToFileURL(filePath).href, desktopPath: filePath, isVideo: false };
    });
}
function stickerLibrary() {
  const bundledRoot = path.join(threadsRoot(), 'stickers'), userRoot = stickerUserRoot(), overrides = readStickerOverrides(), hidden=readHiddenStickerPacks();
  const ids = new Set();
  for (const root of [bundledRoot, userRoot]) if (fs.existsSync(root)) {
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) if (entry.isDirectory()) ids.add(entry.name);
  }
  return [...ids].filter(id=>!hidden.has(id)).sort().map(id => {
    const bundledDir = path.join(bundledRoot, id), userDir = path.join(userRoot, id);
    let meta = {};
    try { meta = JSON.parse(fs.readFileSync(path.join(bundledDir, '_pack.json'), 'utf8')); } catch (_error) {}
    const byName = new Map([...stickerFiles(bundledDir), ...stickerFiles(userDir)].map(item => [item.filename, item]));
    return { id, name: overrides[id] || meta.name || id, userOwned:fs.existsSync(userDir)&&!fs.existsSync(bundledDir), stickers: [...byName.values()] };
  });
}

function mediaDirectories() {
  return ['TTS', 'Images', 'Audio', 'Video'].map(name => path.join(workspaceRoot(), 'Media', name));
}

function directoryStats(root) {
  let files = 0, bytes = 0;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const nested = directoryStats(target);
      files += nested.files; bytes += nested.bytes;
    } else if (entry.isFile()) {
      files++; bytes += fs.statSync(target).size;
    }
  }
  return { files, bytes };
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegPath(), args, { windowsHide: true });
    let stderr = '';
    child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-12000); });
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve() : reject(new Error(`FFmpeg thoát với mã ${code}: ${stderr}`)));
  });
}

ipcMain.handle('desktop:get-runtime-info', () => ({
  platform: process.platform,
  arch: process.arch,
  version: app.getVersion(),
  packaged: app.isPackaged,
}));

ipcMain.handle('desktop:check-assets', () => {
  const root = threadsRoot();
  const required = ['index.html', 'render_shared.js', 'browser_runtime.js', 'export_render.html'];
  return {
    root,
    files: Object.fromEntries(required.map(name => [name, fs.existsSync(path.join(root, name))])),
  };
});

ipcMain.handle('desktop:get-workspace-info', () => ({ root: workspaceRoot() }));

ipcMain.handle('desktop:get-bundled-memeready', () => {
  const root = path.join(threadsRoot(), 'memeready');
  if (!fs.existsSync(root)) return [];
  const items = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const itemRoot = path.join(root, entry.name), metaPath = path.join(itemRoot, 'meta.json');
    if (!fs.existsSync(metaPath)) continue;
    try {
      const item = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      item.readOnly = true;
      item.steps = (item.steps || []).map(step => {
        const result = { ...step };
        for (const field of ['mediaUrl', 'audioUrl', 'audio2Url']) {
          if (!result[field] || /^(?:[a-z]+:|\/)/i.test(result[field])) continue;
          const assetPath = path.join(itemRoot, result[field]);
          result[field] = pathToFileURL(assetPath).href;
          if (field === 'mediaUrl') result.mediaDesktopPath = assetPath;
        }
        return result;
      });
      items.push(item);
    } catch (_error) {}
  }
  return items;
});

ipcMain.handle('desktop:get-bundled-library', (_event, category) => {
  const folders = { audio: 'audio', meme: 'gif', background: 'background', bgaudio: 'audiobackground' };
  const allowedFiles = {
    audio: /\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i,
    bgaudio: /\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i,
    background: /\.(mp4|mov|m4v|webm|avi|mkv)$/i,
    meme: /\.(gif|png|jpe?g|webp|mp4|webm)$/i,
  };
  const folder = folders[category];
  if (!folder) return [];
  const root = path.join(threadsRoot(), folder);
  if (!fs.existsSync(root)) return [];
  let metadata = {};
  try { metadata = JSON.parse(fs.readFileSync(path.join(root, '_meta.json'), 'utf8')); } catch (_error) {}
  return fs.readdirSync(root, { withFileTypes: true }).filter(entry => entry.isFile() && allowedFiles[category].test(entry.name)).map(entry => {
    const filePath = path.join(root, entry.name), meta = metadata[entry.name] || {};
    return {
      filename: entry.name,
      label: meta.label || entry.name,
      description: meta.description || '',
      url: pathToFileURL(filePath).href,
      desktopPath: filePath,
      isVideo: /\.(mp4|mov|webm|m4v)$/i.test(entry.name),
      local: true,
      readOnly: true,
    };
  });
});

ipcMain.handle('desktop:get-sticker-library', () => stickerLibrary());

ipcMain.handle('desktop:create-sticker-pack', (_event, name) => {
  const cleanName = String(name || '').trim().slice(0, 80);
  if (!cleanName) throw new Error('Tên bộ sticker không hợp lệ');
  const id = safePackId(`${cleanName}-${Date.now()}`);
  if (!id) throw new Error('Không tạo được mã bộ sticker');
  fs.mkdirSync(path.join(stickerUserRoot(), id), { recursive: false });
  const overrides = readStickerOverrides(); overrides[id] = cleanName; writeStickerOverrides(overrides);
  return stickerLibrary();
});

ipcMain.handle('desktop:delete-sticker-pack', (_event, packId) => {
  const id = safePackId(packId);
  if (!id) throw new Error('Bộ sticker không hợp lệ');
  const bundledDir = path.join(threadsRoot(), 'stickers', id), userDir = path.join(stickerUserRoot(), id);
  if (!fs.existsSync(userDir)&&!fs.existsSync(bundledDir)) throw new Error('Không tìm thấy bộ sticker');
  if (fs.existsSync(userDir)) fs.rmSync(userDir, { recursive: true, force: false });
  if (fs.existsSync(bundledDir)) { const hidden=readHiddenStickerPacks();hidden.add(id);writeHiddenStickerPacks(hidden); }
  const overrides = readStickerOverrides(); delete overrides[id]; writeStickerOverrides(overrides);
  return stickerLibrary();
});

ipcMain.handle('desktop:rename-sticker-pack', (_event, packId, name) => {
  const id = safePackId(packId), cleanName = String(name || '').trim().slice(0, 80);
  if (!id || !cleanName) throw new Error('Tên bộ sticker không hợp lệ');
  const overrides = readStickerOverrides(); overrides[id] = cleanName; writeStickerOverrides(overrides);
  return stickerLibrary();
});

ipcMain.handle('desktop:add-sticker', (_event, packId, bytes, originalName) => {
  const id = safePackId(packId);
  if (!id) throw new Error('Bộ sticker không hợp lệ');
  const ext = path.extname(String(originalName || '')).toLowerCase();
  if (!['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(ext)) throw new Error('Sticker phải là PNG, JPG, WebP hoặc GIF');
  const dir = path.join(stickerUserRoot(), id); fs.mkdirSync(dir, { recursive: true });
  const output = path.join(dir, safeAssetName(originalName, 'sticker'));
  fs.writeFileSync(output, bytesBuffer(bytes, 20 * 1024 * 1024, 'Sticker'));
  return stickerLibrary();
});

ipcMain.handle('desktop:get-media-stats', () => mediaDirectories().reduce((total, dir) => {
  const stats = directoryStats(dir);
  return { files: total.files + stats.files, bytes: total.bytes + stats.bytes };
}, { files: 0, bytes: 0 }));

ipcMain.handle('desktop:clear-local-media', async () => {
  const choice = await dialog.showMessageBox({
    type: 'warning', title: 'Xóa toàn bộ media cục bộ?',
    message: 'Các dự án cũ có thể mất ảnh, âm thanh hoặc video đã lưu.',
    detail: 'Projects, Exports và file gốc bên ngoài ứng dụng không bị xóa. Hành động này không thể hoàn tác.',
    buttons: ['Hủy', 'Xóa media'], defaultId: 0, cancelId: 0, noLink: true,
  });
  if (choice.response !== 1) return { files: 0, bytes: 0, canceled: true };
  const before = mediaDirectories().reduce((total, dir) => {
    const stats = directoryStats(dir);
    for (const entry of fs.readdirSync(dir)) fs.rmSync(path.join(dir, entry), { recursive: true, force: true });
    return { files: total.files + stats.files, bytes: total.bytes + stats.bytes };
  }, { files: 0, bytes: 0 });
  return { ...before, canceled: false };
});

ipcMain.handle('desktop:save-local-asset', (_event, bytes, kind, originalName) => {
  if (!['tts', 'image', 'audio', 'video'].includes(kind)) throw new Error('Loại media không hợp lệ');
  const output = path.join(assetDirectory(kind), safeAssetName(originalName, kind || 'asset'));
  fs.writeFileSync(output, bytesBuffer(bytes, 1024 * 1024 * 1024, 'Media'));
  return localAssetResult(output);
});

ipcMain.handle('desktop:download-asset', async (_event, url, kind, originalName, headers = {}) => {
  const parsed = new URL(String(url));
  if (kind !== 'tts' || !allowedApiBase(parsed.origin) || !/^\/audio\/[a-f0-9]{32}\.mp3$/i.test(parsed.pathname)) {
    throw new Error('Chỉ được tải file TTS từ API đã cấu hình');
  }
  const authorization = typeof headers?.Authorization === 'string' ? headers.Authorization : '';
  const response = await fetch(parsed.href, { headers: authorization ? { Authorization: authorization } : {} });
  if (!response.ok) throw new Error(`Không tải được file từ server: HTTP ${response.status}`);
  const declaredSize = Number(response.headers.get('content-length') || 0);
  if (declaredSize > 20 * 1024 * 1024) throw new Error('File TTS từ server quá lớn');
  const contentType = response.headers.get('content-type') || '';
  const fallbackExt = contentType.includes('mpeg') ? '.mp3' : '';
  const output = path.join(assetDirectory(kind), safeAssetName(originalName || `${kind || 'asset'}${fallbackExt}`, kind || 'asset'));
  fs.writeFileSync(output, bytesBuffer(await response.arrayBuffer(), 20 * 1024 * 1024, 'File TTS'));
  return localAssetResult(output);
});

ipcMain.handle('desktop:save-project-file', async (_event, json, suggestedName) => {
  if (typeof json !== 'string' || Buffer.byteLength(json, 'utf8') > 10 * 1024 * 1024) throw new Error('File dự án vượt giới hạn 10 MB');
  const name = String(suggestedName || 'threads-project.json').replace(/[^\p{L}\p{N}._-]+/gu, '-');
  const choice = await dialog.showSaveDialog({
    title: 'Lưu dự án Threads',
    defaultPath: path.join(workspaceRoot(), 'Projects', name.endsWith('.json') ? name : `${name}.json`),
    filters: [{ name: 'Threads Project', extensions: ['json'] }],
  });
  if (choice.canceled || !choice.filePath) return { canceled: true };
  fs.writeFileSync(choice.filePath, String(json), 'utf8');
  return { canceled: false, path: choice.filePath };
});

ipcMain.handle('desktop:convert-webm-to-mp4', async (_event, bytes, suggestedName) => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'threads-export-'));
  const input = path.join(tempRoot, 'capture.webm');
  fs.writeFileSync(input, bytesBuffer(bytes, 2 * 1024 * 1024 * 1024, 'Video'));
  const defaultName = String(suggestedName || 'threads-video.mp4').replace(/[^\p{L}\p{N}._-]+/gu, '-');
  const choice = await dialog.showSaveDialog({
    title: 'Lưu video MP4',
    defaultPath: path.join(app.getPath('videos'), defaultName.endsWith('.mp4') ? defaultName : `${defaultName}.mp4`),
    filters: [{ name: 'MP4 Video', extensions: ['mp4'] }],
  });
  if (choice.canceled || !choice.filePath) {
    fs.rmSync(tempRoot, { recursive: true, force: true });
    return { canceled: true };
  }
  try {
    await runFfmpeg([
      '-y', '-i', input,
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
      '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
      choice.filePath,
    ]);
    return { canceled: false, path: choice.filePath };
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

ipcMain.handle('desktop:reveal-file', (_event, filePath) => {
  const target = path.resolve(String(filePath || ''));
  const root = path.resolve(workspaceRoot()) + path.sep;
  if (!target.startsWith(root)) throw new Error('Chỉ được mở file trong thư mục dự án');
  return shell.showItemInFolder(target);
});

ipcMain.handle('desktop:auth-save-refresh', (_event, refreshToken) => {
  return storeRefreshToken(refreshToken);
});

ipcMain.handle('desktop:auth-refresh', async (_event, apiBase) => {
  const base = allowedApiBase(apiBase);
  if (!base) throw new Error('Auth server không được phép');
  try {
    const refreshToken = readRefreshToken();
    if (!refreshToken) return null;
    const response = await fetch(`${base}/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.refresh_token) throw new Error('Server không trả refresh token mới');
    storeRefreshToken(data.refresh_token);
    delete data.refresh_token;
    return data;
  } catch (_error) {
    volatileRefreshToken = null;
    fs.rmSync(authStorePath(), { force: true });
    return null;
  }
});

ipcMain.handle('desktop:auth-clear', () => {
  volatileRefreshToken = null;
  fs.rmSync(authStorePath(), { force: true });
  return true;
});

ipcMain.handle('desktop:export-project', async (event, payload) => {
  const suggestedName = `threads-video-${new Date().toISOString().replace(/[:.]/g, '-')}.mp4`;
  const choice = await dialog.showSaveDialog({
    title: 'Lưu video MP4',
    defaultPath: path.join(workspaceRoot(), 'Exports', suggestedName),
    filters: [{ name: 'MP4 Video', extensions: ['mp4'] }],
  });
  if (choice.canceled || !choice.filePath) return { canceled: true };
  const sendProgress = progress => event.sender.send('desktop:export-progress', progress);
  await exportProject({
    payload,
    outputPath: choice.filePath,
    threadsRoot: threadsRoot(),
    ffmpegPath: ffmpegPath(),
    sendProgress,
  });
  return { canceled: false, path: choice.filePath };
});

app.whenReady().then(() => {
  workspaceRoot();
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  session.defaultSession.setDisplayMediaRequestHandler(async (_request, callback) => {
    try {
      const sources = await desktopCapturer.getSources({ types: ['window', 'screen'] });
      const appSource = sources.find(source => /Threads Video Maker/i.test(source.name));
      callback({ video: appSource || sources[0], audio: 'loopback' });
    } catch (_error) {
      callback({});
    }
  }, { useSystemPicker: true });
  createMainWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
