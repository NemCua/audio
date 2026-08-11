const { app, BrowserWindow, ipcMain, shell, session, desktopCapturer, dialog, safeStorage } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { exportProject } = require('./exporter');

const isDev = !app.isPackaged;

function threadsRoot() {
  return isDev
    ? path.resolve(__dirname, '..', 'threads')
    : path.join(process.resourcesPath, 'threads');
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
  win.loadFile(path.join(threadsRoot(), 'index.html'));
  return win;
}

function ffmpegPath() {
  if (isDev) return require('ffmpeg-static');
  return path.join(process.resourcesPath, 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
}

function authStorePath() { return path.join(app.getPath('userData'), 'auth-session.json'); }

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
  const folder = folders[category];
  if (!folder) return [];
  const root = path.join(threadsRoot(), folder);
  if (!fs.existsSync(root)) return [];
  let metadata = {};
  try { metadata = JSON.parse(fs.readFileSync(path.join(root, '_meta.json'), 'utf8')); } catch (_error) {}
  return fs.readdirSync(root, { withFileTypes: true }).filter(entry => entry.isFile() && entry.name !== '_meta.json').map(entry => {
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
  fs.writeFileSync(output, Buffer.from(bytes));
  return stickerLibrary();
});

ipcMain.handle('desktop:get-media-stats', () => mediaDirectories().reduce((total, dir) => {
  const stats = directoryStats(dir);
  return { files: total.files + stats.files, bytes: total.bytes + stats.bytes };
}, { files: 0, bytes: 0 }));

ipcMain.handle('desktop:clear-local-media', () => {
  const before = mediaDirectories().reduce((total, dir) => {
    const stats = directoryStats(dir);
    for (const entry of fs.readdirSync(dir)) fs.rmSync(path.join(dir, entry), { recursive: true, force: true });
    return { files: total.files + stats.files, bytes: total.bytes + stats.bytes };
  }, { files: 0, bytes: 0 });
  return before;
});

ipcMain.handle('desktop:save-local-asset', (_event, bytes, kind, originalName) => {
  const output = path.join(assetDirectory(kind), safeAssetName(originalName, kind || 'asset'));
  fs.writeFileSync(output, Buffer.from(bytes));
  return localAssetResult(output);
});

ipcMain.handle('desktop:download-asset', async (_event, url, kind, originalName, headers = {}) => {
  const response = await fetch(url, { headers });
  if (!response.ok) throw new Error(`Không tải được file từ server: HTTP ${response.status}`);
  const contentType = response.headers.get('content-type') || '';
  const fallbackExt = contentType.includes('mpeg') ? '.mp3' : '';
  const output = path.join(assetDirectory(kind), safeAssetName(originalName || `${kind || 'asset'}${fallbackExt}`, kind || 'asset'));
  fs.writeFileSync(output, Buffer.from(await response.arrayBuffer()));
  return localAssetResult(output);
});

ipcMain.handle('desktop:save-project-file', async (_event, json, suggestedName) => {
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
  fs.writeFileSync(input, Buffer.from(bytes));
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

ipcMain.handle('desktop:reveal-file', (_event, filePath) => shell.showItemInFolder(filePath));

ipcMain.handle('desktop:auth-save-refresh', (_event, refreshToken) => {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('Hệ điều hành chưa sẵn sàng mã hóa phiên đăng nhập');
  const encrypted = safeStorage.encryptString(String(refreshToken));
  fs.writeFileSync(authStorePath(), JSON.stringify({ token: encrypted.toString('base64') }), { mode: 0o600 });
  return true;
});

ipcMain.handle('desktop:auth-load-refresh', () => {
  try {
    const payload = JSON.parse(fs.readFileSync(authStorePath(), 'utf8'));
    return safeStorage.decryptString(Buffer.from(payload.token, 'base64'));
  } catch (_error) { return null; }
});

ipcMain.handle('desktop:auth-clear', () => {
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
