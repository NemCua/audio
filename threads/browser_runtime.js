/* Browser-first runtime: no Python services are required. */
(() => {
  'use strict';

  const localUrls = new Set();
  let desktopBgVideoPath = null;
  let desktopBgAudioPath = null;
  let recorder = null;
  let captureStream = null;
  let recordedChunks = [];

  function makeLocalUrl(fileOrBlob) {
    const url = URL.createObjectURL(fileOrBlob);
    localUrls.add(url);
    return url;
  }
  window.browserLocalUrl = makeLocalUrl;

  if (window.desktopApp?.getPathForFile) {
    const originalSetBgVideoFile = setBgVideoFile;
    window.setBgVideoFile = setBgVideoFile = function (file) {
      desktopBgVideoPath = window.desktopApp.getPathForFile(file);
      return originalSetBgVideoFile(file);
    };
    const originalSetBgAudioFile = setBgAudioFile;
    window.setBgAudioFile = setBgAudioFile = function (file) {
      desktopBgAudioPath = window.desktopApp.getPathForFile(file);
      return originalSetBgAudioFile(file);
    };
    const originalClearBgVideo = clearBgVideo;
    window.clearBgVideo = clearBgVideo = function () { desktopBgVideoPath = null; return originalClearBgVideo(); };
    const originalClearBgAudio = clearBgAudio;
    window.clearBgAudio = clearBgAudio = function () { desktopBgAudioPath = null; return originalClearBgAudio(); };
  }

  async function localUpload(file) {
    if (!file) throw new Error('Chưa chọn file');
    if (window.desktopApp?.saveLocalAsset) {
      const kind = /^video\//.test(file.type) ? 'video' : /^audio\//.test(file.type) ? 'audio' : 'image';
      const saved = await window.desktopApp.saveLocalAsset(new Uint8Array(await file.arrayBuffer()), kind, file.name || `asset.${kind === 'image' ? 'png' : 'bin'}`);
      return { url: saved.url, desktopPath: saved.path, name: file.name || saved.path.split(/[\\/]/).pop(), originalName: file.name };
    }
    return { url: makeLocalUrl(file), name: file.name, originalName: file.name };
  }

  function localMediaMetadata(file, url) {
    if (!/^video\//.test(file.type) && !/^audio\//.test(file.type)) return Promise.resolve(null);
    return new Promise(resolve => {
      const el = document.createElement(/^video\//.test(file.type) ? 'video' : 'audio');
      el.preload = 'metadata';
      el.onloadedmetadata = () => resolve(Number.isFinite(el.duration) ? el.duration : null);
      el.onerror = () => resolve(null);
      el.src = url;
    });
  }

  window.uploadMediaFile = uploadMediaFile = async function (file) {
    const data = await localUpload(file);
    const duration = await localMediaMetadata(file, data.url);
    modalUploadedMedia = { url: data.url, filename: data.name, isVideo: /^video\//.test(file.type), duration, desktopPath: data.desktopPath || window.desktopApp?.getPathForFile?.(file) || null };
    const nameEl = document.getElementById('m-dropzone-text');
    if (nameEl) nameEl.textContent = `✓ ${data.name}`;
    const status = document.getElementById('m-upload-status');
    if (status) { status.textContent = '✓ Dùng cục bộ — không upload'; status.style.color = '#4ade80'; }
    const path = document.getElementById('m-file-path');
    if (path) path.value = '';
    return data.url;
  };

  window.uploadMessengerImage = uploadMessengerImage = async function (file) {
    const data = await localUpload(file);
    messPendingImage = { url: data.url, name: data.name };
    document.getElementById('mess-image-url').value = '';
    renderMessengerImagePreview();
  };

  window.uploadMessengerAvatar = uploadMessengerAvatar = async function (file) {
    const data = await localUpload(file);
    messPartnerAvatar = data.url;
    renderMessengerDrafts();
    const status = document.getElementById('mess-tts-status');
    if (status) status.textContent = '✓ Đã đổi avatar cục bộ.';
  };

  window.loadMessengerQuickAudioLibrary = loadMessengerQuickAudioLibrary = async function () {};
  window.uploadMessengerQuickAudio = uploadMessengerQuickAudio = async function (file) {
    if (!file) return;
    const data = await localUpload(file);
    const duration = await localMediaMetadata(file, data.url);
    setMessengerQuickAudioMedia({ url: data.url, filename: data.name, duration: duration || DEFAULT_DUR.audio });
    const status = document.getElementById('mess-quick-audio-status');
    if (status) status.textContent = '✓ Dùng file cục bộ — không upload.';
  };

  window.refreshCacheSize = refreshCacheSize = async function () {
    const label = document.getElementById('cache-size-label');
    if (label) label.textContent = `${localUrls.size} media tạm trong phiên này`;
  };

  window.clearServerCache = clearServerCache = async function () {
    if (!confirm('Dọn các URL media tạm của phiên hiện tại? Preview của step đang dùng chúng có thể mất. File gốc trên máy không bị xoá.')) return;
    localUrls.forEach(url => URL.revokeObjectURL(url));
    localUrls.clear();
    refreshCacheSize();
  };

  function supportedRecorderMime() {
    return ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
      .find(type => MediaRecorder.isTypeSupported(type)) || '';
  }

  async function cropCaptureToPreview(stream) {
    const track = stream.getVideoTracks()[0];
    if (!track || !window.CropTarget || typeof track.cropTo !== 'function') return false;
    const target = await CropTarget.fromElement(document.getElementById('preview-frame'));
    await track.cropTo(target);
    return true;
  }

  function waitForPlaybackEnd() {
    return new Promise(resolve => {
      const timer = setInterval(() => {
        if (!playing || playheadT >= totalDuration() - 0.04) {
          clearInterval(timer);
          resolve();
        }
      }, 100);
    });
  }

  window.exportVideo = exportVideo = async function () {
    if (window.desktopApp?.exportProject) return exportVideoOfflineDesktop();
    if (!steps.length) { alert('Chưa có step nào trong kịch bản.'); return; }
    if (!bgVideoFile && !bgVideoLibraryItem) { alert('Cần chọn video nền trước.'); return; }
    if (!navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) {
      alert('Trình duyệt này chưa hỗ trợ ghi tab. Hãy dùng Chrome hoặc Edge mới nhất.');
      return;
    }

    const btn = document.getElementById('export-btn');
    const result = document.getElementById('export-result');
    btn.disabled = true;
    result.innerHTML = '';
    setExportProgress(2, 'Chọn tab hiện tại và bật Chia sẻ âm thanh tab…');
    try {
      captureStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        preferCurrentTab: true,
        selfBrowserSurface: 'include',
        surfaceSwitching: 'exclude',
      });
      if (!captureStream.getAudioTracks().length) {
        captureStream.getTracks().forEach(track => track.stop());
        throw new Error('Chưa bật Chia sẻ âm thanh tab. Hãy xuất lại và bật tùy chọn âm thanh.');
      }
      const cropped = await cropCaptureToPreview(captureStream).catch(() => false);
      if (!cropped) setExportProgress(8, 'Trình duyệt không hỗ trợ crop tab; bản ghi sẽ gồm toàn bộ tab.');

      recordedChunks = [];
      const mimeType = supportedRecorderMime();
      recorder = new MediaRecorder(captureStream, mimeType ? { mimeType, videoBitsPerSecond: 8_000_000 } : undefined);
      recorder.ondataavailable = event => { if (event.data?.size) recordedChunks.push(event.data); };
      recorder.start(1000);

      if (playing) stopPlayback();
      playheadT = 0;
      renderAtTime(0);
      await new Promise(resolve => setTimeout(resolve, 350));
      setExportProgress(15, `Đang ghi trên máy${cropped ? ' — khung 9:16' : ''}…`);
      togglePlay();
      await waitForPlaybackEnd();
      const stopped = new Promise(resolve => recorder.addEventListener('stop', resolve, { once: true }));
      if (recorder.state !== 'inactive') recorder.stop();
      await stopped;

      const blob = new Blob(recordedChunks, { type: recorder.mimeType || 'video/webm' });
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      if (window.desktopApp?.convertWebmToMp4) {
        setExportProgress(92, 'Đang chuyển sang MP4 bằng FFmpeg trên máy…');
        const converted = await window.desktopApp.convertWebmToMp4(
          new Uint8Array(await blob.arrayBuffer()),
          `threads-video-${stamp}.mp4`,
        );
        if (converted.canceled) {
          result.innerHTML = '<div style="color:#fbbf24;font-size:12px">Đã huỷ lưu file MP4.</div>';
        } else {
          const safePath = esc(converted.path);
          result.innerHTML = `<button class="btn btn-primary" onclick="desktopApp.revealFile('${safePath.replace(/'/g, "\\'")}')">✓ MP4 đã lưu — Hiện trong thư mục</button><div style="font-size:11px;color:#94a3b8;margin-top:6px;word-break:break-all">${safePath}</div>`;
        }
        setExportProgress(100, '✓ Đã xuất MP4 trên thiết bị');
      } else {
        const url = makeLocalUrl(blob);
        result.innerHTML = `<a href="${url}" download="threads-video-${stamp}.webm" class="btn btn-primary" style="display:block;text-align:center;text-decoration:none">⭳ Tải video WebM (${(blob.size / 1048576).toFixed(1)} MB)</a>`;
        setExportProgress(100, '✓ Ghi xong hoàn toàn trên thiết bị');
      }
    } catch (error) {
      document.getElementById('export-progress-wrap').style.display = 'none';
      result.innerHTML = `<div style="color:#ef4444;font-size:12px">✗ ${esc(error.message || String(error))}</div>`;
    } finally {
      captureStream?.getTracks().forEach(track => track.stop());
      captureStream = null;
      btn.disabled = false;
    }
  };

  async function exportVideoOfflineDesktop() {
    if (!steps.length) { alert('Chưa có step nào trong kịch bản.'); return; }
    if (!desktopBgVideoPath) { alert('Hãy chọn lại video nền từ máy trước khi xuất.'); return; }
    computeTimeline();
    const btn = document.getElementById('export-btn'), result = document.getElementById('export-result');
    btn.disabled = true; result.innerHTML = '';
    const unsubscribe = window.desktopApp.onExportProgress(progress => setExportProgress(progress.percent || 0, progress.text || ''));
    try {
      const exported = await window.desktopApp.exportProject({
        steps,
        bgVideoPath: desktopBgVideoPath,
        bgAudioPath: desktopBgAudioPath,
        bgAudioVolume: parseFloat(document.getElementById('bgaudio-volume').value),
        blurIdentity: document.getElementById('blur-identity-toggle').checked,
      });
      if (exported.canceled) { document.getElementById('export-progress-wrap').style.display = 'none'; return; }
      const safePath = esc(exported.path);
      result.innerHTML = `<button class="btn btn-primary" onclick="desktopApp.revealFile('${safePath.replace(/'/g, "\\'")}')">✓ MP4 đã lưu — Hiện trong thư mục</button><div style="font-size:11px;color:#94a3b8;margin-top:6px;word-break:break-all">${safePath}</div>`;
    } catch (error) {
      result.innerHTML = `<div style="color:#ef4444;font-size:12px">✗ ${esc(error.message || String(error))}</div>`;
    } finally {
      unsubscribe?.(); btn.disabled = false;
    }
  }

  // Server-backed reusable libraries are intentionally disabled in static mode.
  window.loadBgVideoLibrary = loadBgVideoLibrary = async () => {};
  window.loadBgAudioLibrary = loadBgAudioLibrary = async () => {};
  window.loadLibraryOptions = loadLibraryOptions = async function () {
    const grid = document.getElementById('library-grid');
    if (grid) grid.innerHTML = '<div class="lib-empty">Bản web tĩnh dùng file từ thiết bị.</div>';
  };
  window.loadMemereadyOptions = loadMemereadyOptions = async function () {
    const grid = document.getElementById('memeready-grid');
    if (grid) grid.innerHTML = '<div class="lib-empty">Meme sẵn trên server không có trong bản web tĩnh.</div>';
  };

  window.addEventListener('beforeunload', () => localUrls.forEach(url => URL.revokeObjectURL(url)));
  if (window.desktopApp?.isElectron) {
    const exportButton = document.getElementById('export-btn');
    if (exportButton) exportButton.textContent = '🎬 Xuất MP4 offline (không quay màn hình)';
  }
  refreshCacheSize();
})();
