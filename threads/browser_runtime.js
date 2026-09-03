/* Browser-first runtime: no Python services are required. */
(() => {
  'use strict';

  // Khi chạy bản web local, giữ nguyên các hàm trong index.html để chúng gọi
  // media_server/export_server local (8010/8011). Browser runtime trước đây
  // ghi đè các hàm thư viện bằng bản chỉ dành cho desktop/browser độc lập,
  // khiến thư viện âm thanh local bị trả thành mảng rỗng dù file vẫn còn.
  const isLocalWeb = !window.desktopApp?.isElectron
    && (location.protocol === 'file:' || ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname));
  if (isLocalWeb) return;

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
    window.onBackgroundLibrarySelected = (kind,item) => {
      if(kind==='video')desktopBgVideoPath=item?.desktopPath||null;
      else desktopBgAudioPath=item?.desktopPath||null;
    };
    window.onProjectBackgroundsRestored = (videoItem, audioItem) => {
      desktopBgVideoPath = videoItem?.desktopPath || null;
      desktopBgAudioPath = audioItem?.desktopPath || null;
    };
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
    const originalSelectBgVideoLibraryItem = selectBgVideoLibraryItem;
    window.selectBgVideoLibraryItem = selectBgVideoLibraryItem = function (el) {
      const result = originalSelectBgVideoLibraryItem(el); desktopBgVideoPath = bgVideoLibraryItem?.desktopPath || null; return result;
    };
    const originalSelectBgAudioLibraryItem = selectBgAudioLibraryItem;
    window.selectBgAudioLibraryItem = selectBgAudioLibraryItem = function (el) {
      const result = originalSelectBgAudioLibraryItem(el); desktopBgAudioPath = bgAudioLibraryItem?.desktopPath || null; return result;
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
    if (typeof refreshMediaModalPreviewSoon === 'function') refreshMediaModalPreviewSoon();
    else if (typeof updateMediaModalPreview === 'function') updateMediaModalPreview();
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

  // App desktop dùng trực tiếp bộ âm thanh đóng sẵn. Trước đây hàm rỗng này
  // làm modal "Thêm âm thanh" luôn trống dù asset vẫn nằm trong app.
  window.loadMessengerQuickAudioLibrary = loadMessengerQuickAudioLibrary = async function () {
    const grid = document.getElementById('mess-quick-audio-library');
    if (!grid) return;
    grid.style.display = 'grid';
    grid.innerHTML = '<div class="lib-empty">Đang tải...</div>';
    try {
      const items = await window.desktopApp?.getBundledLibrary?.('audio') || [];
      if (!items.length) { grid.innerHTML = '<div class="lib-empty">Thư viện âm thanh đóng sẵn đang trống.</div>'; return; }
      grid.innerHTML = items.map(item => {
        const media = esc(JSON.stringify({ url:item.url, filename:item.filename, duration:item.duration || DEFAULT_DUR.audio, desktopPath:item.desktopPath || null }));
        return `<div class="lib-item lib-item-audio" data-item='${media}' onclick="setMessengerQuickAudioMedia(JSON.parse(this.dataset.item))" title="${esc(item.description || item.label || item.filename)}"><button type="button" class="lib-play-btn" onclick="event.stopPropagation();setMessengerQuickAudioMedia(JSON.parse(this.closest('.lib-item').dataset.item));toggleLibraryPreviewAudio('${item.url}',this)">▶</button><span class="lib-item-name">${esc(item.label || item.filename)}</span></div>`;
      }).join('');
    } catch (error) {
      grid.innerHTML = `<div class="lib-empty" style="color:#ef4444">⚠ Không tải được thư viện: ${esc(error.message)}</div>`;
    }
  };
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
    if (!label) return;
    if (window.desktopApp?.getMediaStats) {
      const stats = await window.desktopApp.getMediaStats();
      label.textContent = `${stats.files} file media cục bộ — ${fmtBytes(stats.bytes)}`;
    } else {
      label.textContent = `${localUrls.size} media tạm trong phiên này`;
    }
  };

  window.clearDesktopMedia = async function () {
    if (!window.desktopApp?.clearLocalMedia) {
      alert('Tính năng này chỉ có trong ứng dụng desktop.');
      return;
    }
    const warning = 'Xóa TOÀN BỘ MP3 TTS, ảnh, audio và video đã chép vào thư mục Media của app?\n\nDự án đang mở hoặc dự án cũ tham chiếu các file này sẽ mất media và không thể xuất đúng. Projects, Exports và file gốc bên ngoài app không bị xóa. Hành động này không thể hoàn tác.';
    if (!confirm(warning)) return;
    const deleted = await window.desktopApp.clearLocalMedia();
    if (deleted.canceled) return;
    localUrls.forEach(url => URL.revokeObjectURL(url));
    localUrls.clear();
    await refreshCacheSize();
    alert(`✓ Đã xóa ${deleted.files} file (${fmtBytes(deleted.bytes)}).`);
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
    setExportLoading(true);
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
      setExportLoading(false);
    }
  };

async function exportVideoOfflineDesktop() {
    if (!steps.length) { alert('Chưa có step nào trong kịch bản.'); return; }
    if (!desktopBgVideoPath) { alert('Hãy chọn lại video nền từ máy trước khi xuất.'); return; }
    computeTimeline();
    const btn = document.getElementById('export-btn'), result = document.getElementById('export-result');
    const creditInfo = document.getElementById('export-credit-info');
    setExportLoading(true); result.innerHTML = '';
    const unsubscribe = window.desktopApp.onExportProgress(progress => setExportProgress(progress.percent || 0, progress.text || ''));
    let reservation = null;
    let exportCompleted = false;
    try {
      if (!window.creditClient) throw new Error('Chưa kết nối được hệ thống credit. Hãy đăng nhập lại.');
      const pricingInput = { type_counts: exportTypeCounts() };
      const quote = await window.creditClient.quoteExport(pricingInput);
      if (creditInfo) creditInfo.textContent = `Video này: ${quote.cost} credit · Số dư: ${quote.balance} credit`;
      if (!quote.can_export) throw new Error(`Không đủ credit: cần ${quote.cost}, hiện có ${quote.balance}.`);
      reservation = await window.creditClient.reserveExport({
        ...pricingInput,
        idempotency_key: `desktop-export:${crypto.randomUUID()}`,
      });
      const exported = await window.desktopApp.exportProject({
        steps,
        bgVideoPath: desktopBgVideoPath,
        bgAudioPath: desktopBgAudioPath,
        bgAudioVolume: parseFloat(document.getElementById('bgaudio-volume').value),
        blurIdentity: document.getElementById('blur-identity-toggle').checked,
      });
      if (exported.canceled) {
        await window.creditClient.refundExport(reservation.id);
        reservation = null;
        document.getElementById('export-progress-wrap').style.display = 'none';
        if (creditInfo) creditInfo.textContent = 'Đã hủy xuất — credit đã được hoàn lại.';
        return;
      }
      exportCompleted = true;
      await window.creditClient.consumeExport(reservation.id);
      reservation = null;
      const cloudSaved = await window.autoSaveProjectAfterExport?.();
      const safePath = esc(exported.path);
      result.innerHTML = `<button class="btn btn-primary" onclick="desktopApp.revealFile('${safePath.replace(/'/g, "\\'")}')">✓ MP4 đã lưu — Hiện trong thư mục</button><div style="font-size:11px;color:#94a3b8;margin-top:6px;word-break:break-all">${safePath}</div>${cloudSaved?`<div style="font-size:11px;color:#86efac;margin-top:6px">✓ Đã tự lưu project cloud: ${esc(cloudSaved.title)}</div>`:''}`;
      if (creditInfo) creditInfo.textContent = `✓ Đã thanh toán ${quote.cost} credit · Còn ${window.creditClient.balance()} credit`;
    } catch (error) {
      let refundNote = '';
      if (reservation && !exportCompleted) {
        try {
          await window.creditClient.refundExport(reservation.id);
          refundNote = ' Credit đã được hoàn lại.';
        } catch (_refundError) {
          refundNote = ' Chưa thể xác nhận hoàn credit; server sẽ tự hoàn reservation hết hạn.';
        }
      }
      const prefix = exportCompleted ? 'MP4 đã tạo nhưng chưa xác nhận được giao dịch credit: ' : '';
      result.innerHTML = `<div style="color:#ef4444;font-size:12px">✗ ${esc(prefix + (error.message || String(error)) + refundNote)}</div>`;
    } finally {
      unsubscribe?.(); setExportLoading(false);
    }
  }

  async function refreshExportCreditInfo() {
    const el = document.getElementById('export-credit-info');
    if (!el || !window.creditClient || !steps.length) return;
    try {
      computeTimeline();
      const quote = await window.creditClient.quoteExport({ type_counts: exportTypeCounts() });
      el.textContent = `Dự kiến ${quote.cost} credit · Số dư ${quote.balance} credit`;
      el.style.color = quote.can_export ? '#94a3b8' : '#fca5a5';
    } catch (_error) {}
  }
  window.addEventListener('credit-balance-changed', refreshExportCreditInfo);

  // Video/Audio nền dùng thư viện cục bộ trong Documents của người dùng.
  // Modal thư viện mới không còn dùng #m-library-grid; riêng "image" là
  // asset đóng cùng app trong threads/image nên đọc qua media_server local.
  window.loadLibraryOptions = loadLibraryOptions = async function () {
    const category = arguments[0];
    const grid = document.getElementById('modal-library-overlay')?.classList.contains('open')
      ? document.getElementById('modal-library-grid')
      : document.getElementById('m-library-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="lib-empty">Đang tải...</div>';
    try {
      let items = null;
      if (category === 'image') {
        const res = await fetch(MEDIA_SERVER_URL + '/library/image');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        items = await res.json();
      } else {
        items = await window.desktopApp?.getBundledLibrary?.(category) || [];
      }
      document.querySelectorAll('.lib-toggle-btn').forEach(toggle => {
        toggle.textContent = `📂 Chọn từ thư viện có sẵn (${items.length})`;
      });
      grid.innerHTML = items.length
        ? items.map((item, i) => libraryItemHtml(category, item, i)).join('')
        : '<div class="lib-empty">Thư viện đóng sẵn đang trống.</div>';
    } catch (error) {
      grid.innerHTML = `<div class="lib-empty" style="color:#ef4444">⚠ Không tải được thư viện: ${esc(error.message || String(error))}</div>`;
    }
  };
  window.loadMemereadyOptions = loadMemereadyOptions = async function () {
    const grid = document.getElementById('memeready-modal-grid');
    if (!grid) return;
    const items = await window.desktopApp?.getBundledMemeready?.() || [];
    grid.innerHTML = items.length ? items.map(memereadyItemHtml).join('') : '<div class="lib-empty">Chưa có meme đóng sẵn trong app.</div>';
  };

  window.addEventListener('beforeunload', () => localUrls.forEach(url => URL.revokeObjectURL(url)));
  if (window.desktopApp?.isElectron) {
    const exportButton = document.getElementById('export-btn');
    if (exportButton) exportButton.textContent = '🎬 Xuất MP4';
  }
  refreshCacheSize();
})();
