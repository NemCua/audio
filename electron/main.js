const { app, BrowserWindow, dialog, globalShortcut } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const fs = require('fs')

let mainWindow = null
let serverProcess = null

const PORT = 8005
const LOG_FILE = path.join(__dirname, '..', 'electron-main.log')

process.on('uncaughtException', err => {
  if (err && err.code === 'EPIPE') return
  throw err
})

function safeLog(...args) {
  // Do not write server logs to stdout. If the launching terminal closes,
  // Electron can receive EPIPE from stdout and show a main-process popup.
  try {
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${args.join(' ')}\n`)
  } catch {}
}

// TĂ¬m thÆ° má»¥c chá»©a server binaries / scripts
function getScriptsDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'python')
  }
  return path.join(__dirname, '..')
}

function isPortInUse(port) {
  return new Promise(resolve => {
    http.get(`http://127.0.0.1:${port}/`, res => resolve(true)).on('error', () => resolve(false))
  })
}

async function startServers() {
  safeLog('startServers')
  // Náº¿u port Ä‘Ă£ cĂ³ server cháº¡y rá»“i thĂ¬ dĂ¹ng luĂ´n, khĂ´ng spawn thĂªm
  if (await isPortInUse(PORT)) {
    safeLog(`Port ${PORT} already in use, skipping server spawn`)
    return
  }

  const scriptsDir = getScriptsDir()
  safeLog('Scripts dir:', scriptsDir)

  const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' }

  let serverCmd, serverArgs
  if (app.isPackaged) {
    const ext = process.platform === 'win32' ? '.exe' : ''
    const bundledPython = process.platform === 'win32'
      ? path.join(scriptsDir, 'python_runtime', 'python.exe')
      : path.join(scriptsDir, 'python_env', 'bin', 'python')
    const serverScript = path.join(scriptsDir, 'translate_server.py')
    if (fs.existsSync(bundledPython) && fs.existsSync(serverScript)) {
      serverCmd = bundledPython
      serverArgs = [serverScript]
    } else {
      serverCmd = path.join(scriptsDir, `translate_server${ext}`)
      serverArgs = []
    }
    env.FFMPEG_BIN = path.join(scriptsDir, process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg')
    env.FFPROBE_BIN = path.join(scriptsDir, process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe')
  } else {
    const venvPython = process.platform === 'win32'
      ? path.join(scriptsDir, '.venv', 'Scripts', 'python.exe')
      : path.join(scriptsDir, '.venv', 'bin', 'python')
    const python = require('fs').existsSync(venvPython)
      ? venvPython
      : (process.platform === 'win32' ? 'python' : 'python3')
    serverCmd = python
    serverArgs = [path.join(scriptsDir, 'translate_server.py')]
  }

  safeLog('Server command:', serverCmd, serverArgs.join(' '))
  serverProcess = spawn(serverCmd, serverArgs, { cwd: scriptsDir, env })
  let serverLog = ''
  serverProcess.stdout.on('data', d => { serverLog += d.toString(); safeLog('[server]', d.toString()) })
  serverProcess.stderr.on('data', d => { serverLog += d.toString(); safeLog('[server-err]', d.toString()) })
  serverProcess.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      if (serverLog.includes('10048') || serverLog.includes('address already in use')) {
        safeLog(`Server port ${PORT} already in use, keeping existing server`)
        return
      }
      dialog.showErrorBox('Server bi tat', `translate_server thoat voi code ${code}` + "`n`nLog:`n" + serverLog.slice(-2000))
    }
  })
}

function waitForServer(retries = 180) {
  return new Promise((resolve, reject) => {
    const check = (n) => {
      http.get(`http://127.0.0.1:${PORT}/`, res => {
        if (res.statusCode < 500) resolve()
        else if (n > 0) setTimeout(() => check(n - 1), 1000)
        else reject(new Error('Server khong khoi dong duoc'))
      }).on('error', () => {
        if (n > 0) setTimeout(() => check(n - 1), 1000)
        else reject(new Error('Server khong khoi dong duoc'))
      })
    }
    check(retries)
  })
}

function createWindow() {
  safeLog('createWindow')
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'Dich Video',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(`http://localhost:${PORT}`)
  mainWindow.on('closed', () => { safeLog('mainWindow closed'); mainWindow = null })

  // F12 opens DevTools for debugging.
  globalShortcut.register('F12', () => {
    if (mainWindow) mainWindow.webContents.toggleDevTools()
  })
}

app.whenReady().then(async () => {
  safeLog('app ready')
  await startServers()

  // Show splash while waiting for the server.
  const splash = new BrowserWindow({
    width: 400, height: 300,
    frame: false, alwaysOnTop: true,
    webPreferences: { nodeIntegration: false }
  })
  splash.loadURL(`data:text/html,<html><body style="background:#0a0c14;color:#a78bfa;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column"><h2>Dich Video</h2><p style="color:#4b5563">Dang khoi dong server...</p></body></html>`)

  try {
    await waitForServer(30)
    safeLog('server ready')
    splash.close()
    createWindow()
  } catch (e) {
    safeLog('startup error', e && e.stack ? e.stack : e)
    splash.close()
    dialog.showErrorBox('Loi khoi dong', 'Khong the khoi dong server Python.\nKiem tra Python da duoc cai dat chua.')
    app.quit()
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (mainWindow === null) createWindow()
})

app.on('before-quit', () => {
  if (serverProcess) serverProcess.kill()
})
