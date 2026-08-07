const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');

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

app.whenReady().then(() => {
  createMainWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
