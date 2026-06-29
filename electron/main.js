const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')

let mainWindow = null
let serverProcess = null
let authProcess = null

const PORT = 8005
const AUTH_PORT = 8006

// Tìm thư mục chứa server binaries / scripts
function getScriptsDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'python')
  }
  return path.join(__dirname, '..')
}

function startServers() {
  const scriptsDir = getScriptsDir()
  console.log('Scripts dir:', scriptsDir)

  let authCmd, authArgs, serverCmd, serverArgs
  const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' }

  if (app.isPackaged) {
    // Dùng file .exe đã được PyInstaller bundle
    const ext = process.platform === 'win32' ? '.exe' : ''
    authCmd = path.join(scriptsDir, `auth_server${ext}`)
    authArgs = []
    serverCmd = path.join(scriptsDir, `translate_server${ext}`)
    serverArgs = []
  } else {
    // Dev mode: gọi python trực tiếp
    const python = process.platform === 'win32' ? 'python' : 'python3'
    authCmd = python
    authArgs = [path.join(scriptsDir, 'auth_server.py')]
    serverCmd = python
    serverArgs = [path.join(scriptsDir, 'translate_server.py')]
  }

  authProcess = spawn(authCmd, authArgs, { cwd: scriptsDir, env })
  authProcess.stdout.on('data', d => console.log('[auth]', d.toString()))
  authProcess.stderr.on('data', d => console.log('[auth-err]', d.toString()))

  serverProcess = spawn(serverCmd, serverArgs, { cwd: scriptsDir, env })
  serverProcess.stdout.on('data', d => console.log('[server]', d.toString()))
  serverProcess.stderr.on('data', d => console.log('[server-err]', d.toString()))
}

function waitForServer(retries = 30) {
  return new Promise((resolve, reject) => {
    const check = (n) => {
      http.get(`http://localhost:${PORT}/`, res => {
        if (res.statusCode < 500) resolve()
        else if (n > 0) setTimeout(() => check(n - 1), 1000)
        else reject(new Error('Server không khởi động được'))
      }).on('error', () => {
        if (n > 0) setTimeout(() => check(n - 1), 1000)
        else reject(new Error('Server không khởi động được'))
      })
    }
    check(retries)
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'Dịch Video',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(`http://localhost:${PORT}`)
  mainWindow.on('closed', () => { mainWindow = null })
}

app.whenReady().then(async () => {
  startServers()

  // Hiện splash trong khi chờ server
  const splash = new BrowserWindow({
    width: 400, height: 300,
    frame: false, alwaysOnTop: true,
    webPreferences: { nodeIntegration: false }
  })
  splash.loadURL(`data:text/html,<html><body style="background:#0a0c14;color:#a78bfa;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column"><h2>Dịch Video</h2><p style="color:#4b5563">Đang khởi động server...</p></body></html>`)

  try {
    await waitForServer(30)
    splash.close()
    createWindow()
  } catch (e) {
    splash.close()
    dialog.showErrorBox('Lỗi khởi động', 'Không thể khởi động server Python.\nKiểm tra Python đã được cài đặt chưa.')
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
  if (authProcess) authProcess.kill()
})
