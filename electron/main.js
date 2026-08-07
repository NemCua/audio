const { app, BrowserWindow, ipcMain, shell, session, desktopCapturer, dialog } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');

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

app.whenReady().then(() => {
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
