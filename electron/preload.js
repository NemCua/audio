const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('desktopApp', Object.freeze({
  isElectron: true,
  getRuntimeInfo: () => ipcRenderer.invoke('desktop:get-runtime-info'),
  checkAssets: () => ipcRenderer.invoke('desktop:check-assets'),
  getWorkspaceInfo: () => ipcRenderer.invoke('desktop:get-workspace-info'),
  getBundledMemeready: () => ipcRenderer.invoke('desktop:get-bundled-memeready'),
  getBundledLibrary: category => ipcRenderer.invoke('desktop:get-bundled-library', category),
  getStickerLibrary: () => ipcRenderer.invoke('desktop:get-sticker-library'),
  createStickerPack: name => ipcRenderer.invoke('desktop:create-sticker-pack', name),
  deleteStickerPack: packId => ipcRenderer.invoke('desktop:delete-sticker-pack', packId),
  renameStickerPack: (packId, name) => ipcRenderer.invoke('desktop:rename-sticker-pack', packId, name),
  addSticker: (packId, bytes, originalName) => ipcRenderer.invoke('desktop:add-sticker', packId, bytes, originalName),
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
  refreshSession: apiBase => ipcRenderer.invoke('desktop:auth-refresh', apiBase),
  clearAuth: () => ipcRenderer.invoke('desktop:auth-clear'),
}));
