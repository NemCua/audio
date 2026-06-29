const { contextBridge } = require('electron')

// Chỉ expose những gì cần thiết — không expose nodeIntegration
contextBridge.exposeInMainWorld('electron', {
  platform: process.platform,
})
