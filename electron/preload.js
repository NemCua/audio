const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', Object.freeze({
  isElectron: true,
  getRuntimeInfo: () => ipcRenderer.invoke('desktop:get-runtime-info'),
  checkAssets: () => ipcRenderer.invoke('desktop:check-assets'),
  getWorkspaceInfo: () => ipcRenderer.invoke('desktop:get-workspace-info'),
  getMediaStats: () => ipcRenderer.invoke('desktop:get-media-stats'),
  clearLocalMedia: () => ipcRenderer.invoke('desktop:clear-local-media'),
  getPathForFile: file => webUtils.getPathForFile(file),
  saveLocalAsset: (bytes, kind, originalName) => ipcRenderer.invoke('desktop:save-local-asset', bytes, kind, originalName),
  downloadAsset: (url, kind, originalName, headers) => ipcRenderer.invoke('desktop:download-asset', url, kind, originalName, headers),
  saveProjectFile: (json, suggestedName) => ipcRenderer.invoke('desktop:save-project-file', json, suggestedName),
  exportProject: payload => ipcRenderer.invoke('desktop:export-project', payload),
  onExportProgress: callback => {
    const listener = (_event, progress) => callback(progress);
    ipcRenderer.on('desktop:export-progress', listener);
    return () => ipcRenderer.removeListener('desktop:export-progress', listener);
  },
  convertWebmToMp4: (bytes, suggestedName) => ipcRenderer.invoke('desktop:convert-webm-to-mp4', bytes, suggestedName),
  revealFile: filePath => ipcRenderer.invoke('desktop:reveal-file', filePath),
  saveRefreshToken: token => ipcRenderer.invoke('desktop:auth-save-refresh', token),
  loadRefreshToken: () => ipcRenderer.invoke('desktop:auth-load-refresh'),
  clearAuth: () => ipcRenderer.invoke('desktop:auth-clear'),
}));
