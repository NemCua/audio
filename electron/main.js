const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')

let mainWindow = null
let serverProcess = null

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

  const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' }

  let serverCmd, serverArgs
  if (app.isPackaged) {
    const ext = process.platform === 'win32' ? '.exe' : ''
    serverCmd = path.join(scriptsDir, `translate_server${ext}`)
    serverArgs = []
  } else {
    const python = process.platform === 'win32' ? 'python' : 'python3'
    serverCmd = python
    serverArgs = [path.join(scriptsDir, 'translate_server.py')]
  }

  // Auth chạy trên Render, không cần spawn auth_server local
  serverProcess = spawn(serverCmd, serverArgs, { cwd: scriptsDir, env })
  let serverLog = ''
  serverProcess.stdout.on('data', d => { serverLog += d.toString(); console.log('[server]', d.toString()) })
  serverProcess.stderr.on('data', d => { serverLog += d.toString(); console.log('[server-err]', d.toString()) })
  serverProcess.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      dialog.showErrorBox('Server bị tắt', `translate_server thoát với code ${code}\n\nLog:\n${serverLog.slice(-2000)}`)
    }
  })
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
})
