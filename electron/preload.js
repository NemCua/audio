const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', Object.freeze({
  isElectron: true,
  getRuntimeInfo: () => ipcRenderer.invoke('desktop:get-runtime-info'),
  checkAssets: () => ipcRenderer.invoke('desktop:check-assets'),
  convertWebmToMp4: (bytes, suggestedName) => ipcRenderer.invoke('desktop:convert-webm-to-mp4', bytes, suggestedName),
  revealFile: filePath => ipcRenderer.invoke('desktop:reveal-file', filePath),
}));
