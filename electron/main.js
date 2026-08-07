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
  for (const child of ['Projects', path.join('Media', 'TTS'), path.join('Media', 'Images'), path.join('Media', 'Audio'), path.join('Media', 'Video'), 'Exports']) {
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
