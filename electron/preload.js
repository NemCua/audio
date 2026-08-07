const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', Object.freeze({
  isElectron: true,
  getRuntimeInfo: () => ipcRenderer.invoke('desktop:get-runtime-info'),
  checkAssets: () => ipcRenderer.invoke('desktop:check-assets'),
}));
