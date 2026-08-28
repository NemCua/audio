// ============================================================
// render_shared.js — Engine tính timeline + build HTML thẻ Threads, DÙNG
// CHUNG giữa threads/index.html (app soạn kịch bản) và
// threads/export_render.html (trang Playwright chụp ảnh để xuất video —
// mục 5.17 CONTEXT.md).
//
// LÝ DO TÁCH FILE NÀY: xuất video cần chụp ảnh PNG đúng pixel-perfect từng
// thẻ/khung stack Threads bằng Playwright. Nếu viết lại markup/style riêng
// cho trang chụp ảnh, chắc chắn sẽ lệch so với preview thật (CONTEXT.md
// mục 7.1 đã cảnh báo "không tự vẽ lại UI Threads"). Giải pháp: tách các
// hàm THUẦN (pure — chỉ build chuỗi HTML hoặc tính số, không có side-effect
// ngoài DOM build/không tự fetch mạng) ra 1 nguồn duy nhất, cả 2 trang
// cùng include qua <script src="render_shared.js">.
//
// YÊU CẦU KHI DÙNG FILE NÀY: trang gọi phải tự khai báo biến global
// `let steps = []` TRƯỚC KHI include file này (không export/import module,
// giữ đúng tinh thần "không build step" của dự án — xem CONTEXT.md mục
// 7.6) — các hàm computeTimeline()/computeSceneAt() đọc thẳng biến global
// `steps`, không nhận qua tham số, y hệt cách index.html vốn đã viết.
// export_render.html gán steps = [...] (dữ liệu step nhận từ Playwright)
// rồi gọi lại đúng các hàm này.
//
// blurIdentityOn() đọc `document.getElementById('blur-identity-toggle')`
// — phần tử này CHỈ tồn tại trong index.html. Ở export_render.html sẽ luôn
// trả về `false` (không blur) vì el là null — ĐÂY LÀ HÀNH VI ĐÚNG Ý: video
// xuất ra cuối cùng nên hiện tên/avatar THẬT, không mờ, giống như người
// dùng tắt blur trước khi xuất bản chính thức.
// ============================================================

function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function safeMediaUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  // Bundled random avatars intentionally use a relative URL so the same
  // project works in the browser, the Electron app and the hidden export
  // renderer. Resolve only this tightly scoped asset path; the old code
  // rejected every relative URL, causing avatars to fall back to initials.
  if (raw === 'messenger-default-avatar.png' || /^avatarthreads\/[\p{L}\p{N}._-]+\.(?:png|jpe?g|gif|webp)$/iu.test(raw)) {
    try { return new URL(raw, document.baseURI).href; } catch (_error) { return ''; }
  }
  if (/^(?:file:|blob:)/i.test(raw)) return raw;
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(raw)) return raw;
  try {
    const url = new URL(raw);
    if (url.protocol === 'https:') return url.href;
    if (url.protocol === 'http:' && ['127.0.0.1','localhost','[::1]'].includes(url.hostname)) return url.href;
  } catch (_error) {}
  return '';
}
function fmt(t) { return t.toFixed(1) + 's'; }

// ---------- Tính timeline tương đối ----------
function appearanceDelay(s) {
  if (!s || !['post', 'comment', 'reply', 'text', 'note'].includes(s.type)) return 0;
  if (['drop-bounce', 'bounce-up', 'bounce-left', 'bounce-right'].includes(s.appearance)) return .175;
  return ['fly-left', 'fly-right', 'fly-top', 'fly-bottom'].includes(s.appearance) ? .253 : 0;
}
function effectiveDuration(s) {
  return (s.duration || 0) + appearanceDelay(s);
} // duration = phần nội dung; hiệu ứng xuất hiện được cộng trước TTS

// Step `audio` (hiệu ứng âm thanh) chạy SONG SONG — không chiếm chỗ trên
// track chính (post/comment/reply/meme). Nó tự bắt đầu cùng lúc với step
// đứng ngay TRƯỚC nó trong danh sách (chồng lên, phát đè), không phải đợi
// step trước kết thúc + gap như track chính. Do đó tính timeline 2 lượt:
// lượt 1 gán _start/_end cho track chính (biến t chạy tuần tự, bỏ qua hoàn
// toàn step audio); lượt 2 gán _start cho từng step audio = _start của step
// track-chính gần nhất đứng trước nó trong mảng (0 nếu audio là step đầu).
// Step `audio` mặc định chạy TUẦN TỰ (chiếm chỗ riêng trên track chính,
// giống post/comment) — chỉ chạy SONG SONG (đè lên step trước, không chiếm
// thời gian riêng) khi có cờ `s.parallel === true`, bật qua checkbox trong
// modal soạn. Mặc định tắt vì hầu hết trường hợp âm thanh vẫn nên có
// khoảng thời gian riêng trong kịch bản, song song chỉ dùng khi cố ý.
function isParallelAudio(s) { return s.type === 'audio' && s.parallel; }
function isParallelVisual(s) { return (s.type === 'image' || s.type === 'sticker') && s.parallel; }
function isParallelText(s) { return s.type === 'text' && s.parallel; }
function isParallelMedia(s) { return isParallelAudio(s) || isParallelVisual(s) || isParallelText(s); }

function computeTimeline() {
  let t = 0;
  let mainEnd = 0; // mốc cuối cùng của track chính (_end + gap của step chính cuối)
  for (const s of steps) {
    if (isParallelMedia(s)) continue; // xử lý ở lượt 2 bên dưới
    s._start = t;
    s._end = t + effectiveDuration(s);
    t = s._end + (s.gap || 0);
    mainEnd = t;
  }

  let lastMainStep = null;
  let parallelEnd = 0;
  for (const s of steps) {
    if (!isParallelMedia(s)) { lastMainStep = s; continue; }
    const contentAnchor = s.parallelAnchorStepId != null ? steps.find(step => step.id === s.parallelAnchorStepId) : null;
    s._start = contentAnchor ? contentAnchor._start + progressiveContentOffset(contentAnchor, s.parallelAnchorPartIndex || 0) : (lastMainStep ? lastMainStep._start : 0);
    // Media song song có thời lượng riêng và không đẩy lùi track chính.
    // Ví dụ card chính 0–3s, media 0–5s thì card chính kế vẫn bắt đầu ở 3s;
    // media tiếp tục phủ/phát chồng cho tới giây 5.
    s._end = s._start + effectiveDuration(s);
    parallelEnd = Math.max(parallelEnd, s._end);
  }

  // Nếu media song song cuối cùng dài hơn track chính thì vẫn phải xuất hết
  // phần đuôi của media, nhưng nó không được ảnh hưởng mốc bắt đầu card kế.
  return Math.max(mainEnd, parallelEnd);
}
function totalDuration() { return computeTimeline(); }

// Một card kể chuyện có thể hiện chữ tích luỹ theo các dòng người dùng chủ
// động xuống hàng: đoạn 1 -> đoạn 1+2 -> ... -> toàn bài. Đây chỉ là trạng
// thái render theo thời gian, không tách/sinh thêm step trong timeline.
function progressiveTextParts(s) {
  if (!s || !s.progressiveReveal || !['post', 'comment', 'reply'].includes(s.type)) return [];
  return String(s.text || '').split(/\r?\n+/).map(part => part.trim()).filter(Boolean);
}
function progressiveRevealStage(s, t) {
  const parts = progressiveTextParts(s);
  if (parts.length < 2) return 0;
  const clipDurations = [Number(s.ttsDuration || 0), ...(s.extraTtsSegments || []).map(seg => Number(seg.duration || 0))].slice(0, parts.length);
  if (clipDurations.length === parts.length && clipDurations.every(duration => duration > 0)) {
    const localT = Math.max(0, Number(t) - Number(s._start) - appearanceDelay(s));
    let stage = 1, nextStart = clipDurations[0] + progressiveGapAfter(s, 0);
    while (stage < parts.length && localT >= nextStart - 1e-6) {
      stage++;
      nextStart += clipDurations[stage - 1] + (stage < parts.length ? progressiveGapAfter(s, stage - 1) : 0);
    }
    return stage;
  }
  const weights = parts.map(part => Math.max(1, [...part].length));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  const delay = appearanceDelay(s);
  const duration = Math.max(.001, Number(s._end) - Number(s._start) - delay);
  const elapsedWeight = Math.max(0, Math.min(1, (Number(t) - Number(s._start) - delay) / duration)) * totalWeight;
  let stage = 1, boundary = weights[0];
  while (stage < parts.length && elapsedWeight >= boundary - 1e-6) {
    stage++;
    boundary += weights[stage - 1];
  }
  return stage;
}
function stepForRenderAtTime(s, t) {
  const stage = progressiveRevealStage(s, t);
  if (!stage) return s;
  const parts = progressiveTextParts(s);
  return { ...s, text: parts.slice(0, stage).join('\n'), _progressiveFinal: stage === parts.length };
}

function progressiveGapAfter(s, clipIndex) {
  if (!s || !s.progressiveReveal) return 0;
  if (clipIndex === 0 && s.progressiveGapAfterFirst != null) return Math.max(0, Number(s.progressiveGapAfterFirst) || 0);
  const seg = (s.extraTtsSegments || [])[clipIndex - 1];
  if (seg && seg.gapAfter != null) return Math.max(0, Number(seg.gapAfter) || 0);
  return Math.max(0, Number(s.progressivePause ?? .3));
}

function progressiveContentOffset(s, partIndex) {
  if (!s || !s.progressiveReveal) return 0;
  const index = Math.max(0, Number(partIndex) || 0);
  const durations = [Number(s.ttsDuration || 0), ...(s.extraTtsSegments || []).map(seg => Number(seg.duration || 0))];
  let offset = appearanceDelay(s);
  for (let i = 0; i < index; i++) offset += (durations[i] || 0) + progressiveGapAfter(s, i);
  return offset;
}

function noteSubtitleTextAt(t, stack) {
  const notes=(stack||[]).filter(step=>step.type==='note'&&step.noteSubtitles!==false);
  for(let n=notes.length-1;n>=0;n--){
    const step=notes[n],local=Math.max(0,Number(t)-Number(step._start)-appearanceDelay(step));
    const clips=[{words:step.subtitleWords||[],duration:Number(step.ttsDuration||0)},...(step.extraTtsSegments||[]).map(seg=>({words:seg.subtitleWords||[],duration:Number(seg.duration||0),gapAfter:Number(seg.gapAfter||0)}))];
    let offset=0;
    for(let clipIndex=0;clipIndex<clips.length;clipIndex++){
      const clip=clips[clipIndex],words=Array.isArray(clip.words)?clip.words:[],duration=Number(clip.duration||0);
      if(words.length&&local>=offset-1e-6&&local<offset+duration-1e-6){
        const clipLocal=local-offset;
        for(let start=0;start<words.length;start+=14){
          const group=words.slice(start,start+14),groupStart=Number(group[0]?.start||0),nextStart=start+14<words.length?Number(words[start+14]?.start||0):duration;
          if(clipLocal>=groupStart-1e-6&&clipLocal<nextStart-1e-6)return group.map(item=>String(item.word||'').trim()).filter(Boolean).join(' ');
        }
      }
      offset+=duration+(step.progressiveReveal&&clipIndex<clips.length-1?Number(clip.gapAfter||step.progressivePause||.3):0);
    }
  }
  return '';
}

// ---------- Build 1 thẻ post/comment/reply cho stack ----------
// ---------- Build HTML thẻ post/comment/reply — BÊ NGUYÊN SI cấu trúc từ
// threads_ui_mock.html (mẫu 1/2/3): .thr-row > .avatar-wrap + .thr-main >
// .thr-head(.who>.uname-row, .side-actions) rồi .thr-body/.thr-stats.
// ----------------------------------------------------------------------
function statsRow(stats) {
  stats = stats || {};
  const liked = stats.likes ?? '1,2K', c = stats.comments ?? '34', r = stats.reposts ?? '8', sh = stats.shares ?? '150';
  return `<div class="thr-stats">
    <div class="stat liked"><svg viewBox="-0.5 0 25 24"><use href="#ic-heart-fill"/></svg>${esc(liked)}</div>
    <div class="stat"><svg viewBox="0 0 24 24"><use href="#ic-comment"/></svg>${esc(c)}</div>
    <div class="stat"><svg viewBox="0 0 24 24"><use href="#ic-repost"/></svg>${esc(r)}</div>
    <div class="stat"><svg viewBox="0 0 24 24"><use href="#ic-share"/></svg>${esc(sh)}</div>
  </div>`;
}
function imgWrapHtml(imgUrl) {
  imgUrl = safeMediaUrl(imgUrl);
  if (!imgUrl) return '';
  return `<div class="thr-img-wrap">
    <img src="${esc(imgUrl)}" alt="" referrerpolicy="no-referrer"/>
    <div class="img-share-btn"><svg viewBox="0 0 24 24"><use href="#ic-share"/></svg></div>
  </div>`;
}
// avatarUrl (tuỳ chọn) — nếu có, hiện ảnh thật (object-fit:cover phủ kín
// hình tròn); nếu không/URL lỗi, fallback về chữ cái đầu tên tô màu như cũ.
// referrerpolicy="no-referrer": nhiều CDN (Instagram, Facebook...) chặn
// hotlink dựa trên Referer header — không gửi Referer thì qua được (xem mục 5.9).
function avatarHtml(uname, hue, sm, avatarUrl) {
  avatarUrl = safeMediaUrl(avatarUrl);
  const inner = avatarUrl
    ? `<img src="${esc(avatarUrl)}" alt="" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover;border-radius:50%" onerror="this.replaceWith(Object.assign(document.createElement('div'),{textContent:${JSON.stringify((uname[0]||'?').toUpperCase())},style:'width:100%;height:100%;display:flex;align-items:center;justify-content:center'}))"/>`
    : esc((uname[0]||'?').toUpperCase());
  return `<div class="avatar-wrap">
    <div class="avatar${sm?' sm':''}" style="background:hsl(${hue},65%,55%)">${inner}</div>
    <span class="follow-badge"></span>
  </div>`;
}

// Avatar riêng cho TikTok (mục 5.39 CONTEXT.md) — tròn thuần, KHÔNG có
// follow-badge dấu "+" (đặc trưng riêng Threads, mock TikTok không có).
function tiktokAvatarHtml(uname, hue, avatarUrl) {
  avatarUrl = safeMediaUrl(avatarUrl);
  const inner = avatarUrl
    ? `<img src="${esc(avatarUrl)}" alt="" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover" onerror="this.replaceWith(Object.assign(document.createElement('div'),{textContent:${JSON.stringify((uname[0]||'?').toUpperCase())},style:'width:100%;height:100%;display:flex;align-items:center;justify-content:center'}))"/>`
    : esc((uname[0]||'?').toUpperCase());
  return `<div class="tk-avatar" style="background:hsl(${hue},65%,55%)">${inner}</div>`;
}

// Post gốc — đúng mẫu 1: .thr-row.post-head-row (chỉ avatar+tên/giờ+⋯),
// .thr-body/.thr-img-wrap/.thr-stats tràn full-width NGOÀI .thr-row (không
// thụt theo avatar).
function buildPostHtml(uname, text, timeLabel, imgUrl, statsHtml, avatarUrl) {
  return `<div class="thr-row post-head-row" style="margin-bottom:14px">
      ${avatarHtml(uname, hueOf(uname), false, avatarUrl)}
      <div class="thr-main"><div class="thr-head">
        <div class="who"><div class="uname-row">
          <span class="uname">${esc(uname)}</span><span class="time">${esc(timeLabel)}</span>
        </div></div>
        <div class="side-actions"><div class="menu-dots">⋯</div></div>
      </div></div>
    </div>
    <div class="thr-body">${esc(text)}</div>
    ${imgWrapHtml(imgUrl)}
    ${statsHtml}`;
}

// Comment/reply — đúng mẫu 2/3: .thr-row (avatar sm) > .thr-main chứa cả
// .thr-head + .thr-body + .thr-img-wrap + .thr-stats (nội dung THỤT theo avatar).
function buildCommentRow(uname, text, hue, isAuthor, timeLabel, imgUrl, statsHtml, extraCls, avatarUrl) {
  return `<div class="thr-row${extraCls?' '+extraCls:''}">
      ${avatarHtml(uname, hue, true, avatarUrl)}
      <div class="thr-main">
        <div class="thr-head">
          <div class="who"><div class="uname-row">
            <span class="uname">${esc(uname)}</span>
            ${isAuthor?'<span class="badge-author">Tác giả</span>':''}
            <span class="time">${esc(timeLabel)}</span>
          </div></div>
          <div class="side-actions"><div class="menu-dots">⋯</div></div>
        </div>
        <div class="thr-body">${esc(text)}</div>
        ${imgWrapHtml(imgUrl)}
        ${statsHtml}
      </div>
    </div>`;
}

function hueOf(seed) {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

// ---------- Che danh tính khi bật blur (mục 5.10) ----------
// Chỉ blur thôi KHÔNG đủ: avatar tô màu hsl() sinh từ hash(uname) nên cùng
// tên luôn ra cùng màu — lặp lại giữa các step vẫn nhận diện được qua màu.
// Tên dù blur vẫn lộ ĐỘ DÀI chữ (số ký tự/từ) — đoán được gần đúng tên thật.
// Giải pháp: khi bật blur, THAY HẲN tên/avatar hiển thị bằng 1 cặp random
// từ 2 danh sách cố định (không liên quan gì tới data thật), rồi mới blur
// nhẹ lên trên — vậy nên không còn kênh rò rỉ nào (màu/độ dài) nữa.
const RANDOM_IDENTITY_NAMES = ['mdynndeii', 'tuyetnhung_2807', '_vanhb.bp_', 'jinxthn', 'nt_hongf', 'yeu_e.m36', 'kditrh_'];
const RANDOM_IDENTITY_AVATARS = [
  'avatarthreads/358389238_577583710976396_4095180163388838913_n.jpg',
  'avatarthreads/573323465_1219825463302212_7278921664109726296_n.jpeg',
  'avatarthreads/615332121_17912009004280068_5286935376602378447_n.jpg',
  'avatarthreads/657332624_18068065118327029_1106849754719172851_n.jpg',
  'avatarthreads/691776211_18092243654076179_1348958173927704985_n.jpg',
  'avatarthreads/703988601_17928408645288931_7166899634710086540_n.jpg',
  'avatarthreads/719203607_17938171611256173_8701111777067028645_n.jpg',
  'avatarthreads/732544775_17934294942335167_7847933916115801145_n.jpg',
  'avatarthreads/742208514_17941413951252799_2376814528070332172_n.jpg',
];
// Thời gian đăng (vd "21 giờ") không có "hình dạng" nhạy cảm như tên/avatar
// khi blur, nên không cần CSS blur mờ — chỉ cần random ra 1 giá trị hợp lý
// khác thay cho số user tự gõ, đọc rõ bình thường (đã hỏi & chốt qua
// AskUserQuestion).
const RANDOM_IDENTITY_TIMES = ['1 giờ', '2 giờ', '3 giờ', '5 giờ', '7 giờ', '9 giờ', '12 giờ', '18 giờ', '21 giờ', '1 ngày', '2 ngày'];

// Hash chuỗi đơn giản → số nguyên không âm, dùng làm seed random ổn định
// (cùng seed luôn ra cùng kết quả, nên tên/avatar giả của 1 "nhân vật" giữ
// nguyên xuyên suốt video thay vì đổi lung tung mỗi lần render).
function seedHash(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h;
}

function blurIdentityOn() {
  const el = document.getElementById('blur-identity-toggle');
  return el ? el.checked : false;
}

// seed = khoá ổn định cho 1 "nhân vật" trong 1 step (vd stepId + ':m' hay
// stepId + ':m2' cho reply) — đảm bảo mỗi nhân vật có 1 tên/avatar/thời gian
// giả cố định riêng, không trùng logic giữa comment gốc và reply trong cùng step.
function displayIdentity(seed, realUname, realAvatarUrl, realTimeLabel, blurMode) {
  const shouldBlur = blurMode === 'blur' ? true : blurMode === 'clear' ? false : blurIdentityOn();
  if (!shouldBlur) return { uname: realUname, avatarUrl: realAvatarUrl, timeLabel: realTimeLabel };
  const h = seedHash(seed);
  return {
    uname: RANDOM_IDENTITY_NAMES[h % RANDOM_IDENTITY_NAMES.length],
    avatarUrl: RANDOM_IDENTITY_AVATARS[Math.floor(h / RANDOM_IDENTITY_NAMES.length) % RANDOM_IDENTITY_AVATARS.length],
    timeLabel: RANDOM_IDENTITY_TIMES[Math.floor(h / (RANDOM_IDENTITY_NAMES.length * RANDOM_IDENTITY_AVATARS.length)) % RANDOM_IDENTITY_TIMES.length],
  };
}

// Vị trí/xoay tuỳ chỉnh cho step (mục 5.21 CONTEXT.md) — offsetX/offsetY
// (cqw) + rotation (độ), cộng thêm vào layout tự nhiên. Trả chuỗi rỗng khi
// cả 3 đều 0 (đa số step không chỉnh gì) — tránh in transform thừa vô ích
// vào MỌI card. Dùng đơn vị cqw để tự scale đúng tỉ lệ giữa preview
// (~460px) và export (1080px) nhờ container-type:inline-size sẵn có.
function posTransform(s) {
  const finite = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const ox = finite(s.offsetX), oy = finite(s.offsetY), rot = finite(s.rotation);
  if (!ox && !oy && !rot) return '';
  const parts = [];
  if (ox || oy) parts.push(`translate(${ox}cqw, ${oy}cqw)`);
  if (rot) parts.push(`rotate(${rot}deg)`);
  return ` style="transform:${parts.join(' ')}"`;
}

function safeCardColor(value, fallback) {
  const raw=String(value||'').trim();
  return /^#[0-9a-f]{3,8}$/i.test(raw)?raw:fallback;
}
function textCardFontClass(value) {
  return ['serif','rounded','mono','display'].includes(value)?` font-${value}`:'';
}
function buildPlainTextCardHtml(s,posClass,stepAttr,posAttr){
  const size=Math.max(2,Math.min(12,Number(s.textFontSize)||6));
  const legacyStroke=s.textStrokeWidth??s.cardBorderWidth,stroke=Math.max(0,Math.min(1.5,Number.isFinite(Number(legacyStroke))?Number(legacyStroke):.18));
  const style=`--text-size:${size}cqw;--text-color:${safeCardColor(s.textColor,'#ffffff')};--text-stroke-color:${safeCardColor(s.textStrokeColor||s.cardBorderColor,'#000000')};--text-stroke-width:${stroke}cqw`;
  return `<div class="${posClass}"${stepAttr}${posAttr}><div class="plain-text-card${textCardFontClass(s.textFont)}" style="${style}">${esc(s.text||'')}</div></div>`;
}
function buildNoteCardHtml(s,posClass,stepAttr,posAttr){
  const preset=['black','paper','sunset','ocean','lavender','glass','custom'].includes(s.noteBgPreset)?s.noteBgPreset:'black';
  const size=Math.max(2,Math.min(12,Number(s.textFontSize)||2.6));
  const bgUrl=preset==='custom'?safeMediaUrl(s.noteBgUrl):'';
  const radius=Math.max(0,Math.min(10,Number.isFinite(Number(s.cardRadius))?Number(s.cardRadius):1.2)),padding=Math.max(0,Math.min(14,Number.isFinite(Number(s.notePadding))?Number(s.notePadding):6.2));
  const weight=Math.max(200,Math.min(900,Number(s.textFontWeight)||200));
  const style=`--text-size:${size}cqw;--text-color:${safeCardColor(s.textColor,'#ffffff')};--note-padding:${padding}cqw;font-weight:${weight};border-radius:${radius}cqw;${preset==='custom'||preset==='black'?`--note-bg:${safeCardColor(s.cardBgColor,'#050505')};`:''}${bgUrl?'--note-overlay:linear-gradient(#0005,#0009)':''}`;
  const bg=bgUrl?`<img class="note-card-bg-image" src="${esc(bgUrl)}" alt="" referrerpolicy="no-referrer"/>`:'';
  return `<div class="${posClass}"${stepAttr}${posAttr}><div class="note-card note-bg-${preset}${textCardFontClass(s.textFont)}" style="${style}">${bg}<div class="note-card-text">${esc(s.text||'')}</div></div></div>`;
}

function buildCardHtml(s) {
  const posAttr = posTransform(s);
  const stepAttr = ` data-step-id="${esc(s.id)}"`;
  const allowedAppearances = new Set(['fly-left', 'fly-right', 'fly-top', 'fly-bottom', 'drop-bounce', 'bounce-up', 'bounce-left', 'bounce-right']);
  const appearanceClass = allowedAppearances.has(s.appearance) ? ` appearance-${s.appearance}` : '';
  const identityClass=s.identityBlurMode==='blur'?' identity-force-blur':s.identityBlurMode==='clear'?' identity-force-clear':'';
  const posClass = (s.progressiveReveal ? 'tcard-pos progressive-reveal-card' : 'tcard-pos') + appearanceClass + identityClass;
  if (s.type === 'text') return buildPlainTextCardHtml(s,posClass,stepAttr,posAttr);
  if (s.type === 'note') return buildNoteCardHtml(s,posClass,stepAttr,posAttr);
  if (s.type === 'mess') return `<div class="${posClass}"${stepAttr}${posAttr}>${buildMessengerHtml(s)}</div>`;
  if (s.type === 'post') {
    const id1 = displayIdentity(s.id + ':m', s.uname, s.avatarUrl, (s.stats&&s.stats.timeLabel)||'vừa xong', s.identityBlurMode);
    const showFooter = !s.progressiveReveal || s._progressiveFinal;
    return `<div class="${posClass}"${stepAttr}${posAttr}><div class="tcard">${buildPostHtml(id1.uname, s.text, id1.timeLabel, showFooter?s.imgUrl:null, showFooter?statsRow(s.stats):'', id1.avatarUrl)}</div></div>`;
  }
  if (s.type === 'comment' || s.type === 'reply') {
    // Platform (Threads/TikTok, mục 5.39 CONTEXT.md) — thay hẳn step 'reply'
    // cũ kiểu gộp 2 khối (comment gốc + reply trong 1 step, có .connector-line
    // nối 2 khối). Giờ 'comment' và 'reply' đều là step ĐỘC LẬP 1 khối, khác
    // nhau ở: 'comment' TikTok có thể có s.hasReplyCount (chỉ hiện dòng "Xem N
    // câu trả lời", không sinh step con — TÍNH NĂNG NÀY CHỈ Ở TIKTOK, người
    // dùng xác nhận Threads không cần); 'reply' là 1 bình luận đứng riêng,
    // style khác biệt theo platform (Threads: thụt lề nhẹ không đường nối;
    // TikTok: hẹp hơn + lệch phải). platform mặc định 'threads' cho step cũ
    // thiếu field.
    const platform = s.platform || 'threads';
    const id1 = displayIdentity(s.id + ':m', s.uname, s.avatarUrl, (s.stats&&s.stats.timeLabel)||'vừa xong', s.identityBlurMode);
    if (platform === 'tiktok') {
      return buildTiktokCommentHtml(s, id1, posAttr, stepAttr);
    }
    // Threads reply thụt TOÀN BỘ card giống TikTok reply; không gắn margin
    // vào .thr-row vì làm vậy chỉ đẩy avatar/content bên trong nền trắng.
    // Không vẽ connector vì step này không chứa comment cha.
    const showFooter = !s.progressiveReveal || s._progressiveFinal;
    const commentHtml = buildCommentRow(id1.uname, s.text, s.hue, s.isAuthor, id1.timeLabel, showFooter?s.imgUrl:null, showFooter?statsRow(s.stats):'', null, id1.avatarUrl);
    return `<div class="${posClass}"${stepAttr}${posAttr}><div class="tcard${s.type==='reply'?' thr-reply-card':''}">${commentHtml}</div></div>`;
  }
  return '';
}

// Ảnh mặc định phải là asset đóng sẵn, không dùng CDN Facebook: URL mạng
// từng làm 0.3s đầu video xuất bị trống avatar trong lúc Playwright chờ tải.
const MESS_DEFAULT_AVATAR = 'messenger-default-avatar.png';
function messengerPartnerAvatar(value) {
  const raw = String(value || '');
  // Dự án cũ đã serialize URL avatar mặc định Facebook: tự nâng cấp về asset
  // local. Avatar mạng do người dùng tự chọn vẫn được giữ nguyên.
  if (raw.includes('453178253_471506465671661_2781666950760530985_n.png')) return safeMediaUrl(MESS_DEFAULT_AVATAR);
  return safeMediaUrl(raw) || safeMediaUrl(MESS_DEFAULT_AVATAR);
}
function buildMessengerFooterHtml() {
  return `<div class="mess-composer" aria-hidden="true">
    <span class="mess-compose-icon mess-plus"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="currentColor"/><path d="M12 6.25v11.5M6.25 12h11.5" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></svg></span>
    <span class="mess-compose-icon"><svg viewBox="58.5 740 25 22"><path fill-rule="evenodd" d="M73.1225 740C73.7671 740 74.388 740.242 74.8617 740.676L76.5133 742.193C76.9278 742.574 77.455 742.806 78.0122 742.858L78.2525 742.87H79.0128C81.491 742.87 83.5 744.868 83.5 747.333V757.536C83.5 760.002 81.491 762 79.0128 762H62.9872C60.509 762 58.5 760.002 58.5 757.536V747.333C58.5 744.868 60.509 742.87 62.9872 742.87H63.7475C64.3921 742.87 65.013 742.628 65.4867 742.193L67.1383 740.676C67.612 740.242 68.2329 740 68.8775 740H73.1225ZM71 745.5C67.4101 745.5 64.5 748.41 64.5 752C64.5 755.59 67.4101 758.5 71 758.5C74.5899 758.5 77.5 755.59 77.5 752C77.5 748.41 74.5899 745.5 71 745.5ZM71 747.5C73.4853 747.5 75.5 749.515 75.5 752C75.5 754.485 73.4853 756.5 71 756.5C68.5147 756.5 66.5 754.485 66.5 752C66.5 749.515 68.5147 747.5 71 747.5Z"/></svg></span>
    <span class="mess-compose-icon mess-image-icon"><svg viewBox="104 740 22 22" preserveAspectRatio="none"><path fill-rule="evenodd" d="M122 740C124.209 740 126 741.791 126 744V758C126 760.209 124.209 762 122 762H108C105.791 762 104 760.209 104 758V744C104 741.791 105.791 740 108 740H122ZM122 742H108C106.895 742 106 742.895 106 744V754.465C106 754.576 106.09 754.665 106.2 754.665C106.231 754.665 106.262 754.658 106.29 754.644L112.304 751.618C114 750.764 116 750.764 117.697 751.617L123.71 754.64C123.809 754.69 123.929 754.65 123.979 754.551C123.993 754.524 124 754.493 124 754.462V744C124 742.895 123.105 742 122 742ZM110.5 744C111.881 744 113 745.119 113 746.5C113 747.881 111.881 749 110.5 749C109.119 749 108 747.881 108 746.5C108 745.119 109.119 744 110.5 744Z"/></svg></span>
    <span class="mess-compose-icon"><svg viewBox="150 739 18.1 24"><path fill-rule="evenodd" d="M155 761H163C163.552 761 164 761.448 164 762C164 762.513 163.614 762.936 163.117 762.993L163 763H155C154.448 763 154 762.552 154 762C154 761.487 154.386 761.064 154.883 761.007L155 761H163H155ZM167.101 749.603C167.653 749.62 168.088 750.08 168.071 750.632C168.029 752.065 167.571 753.582 166.794 754.928C164.309 759.233 158.805 760.708 154.5 758.223C151.746 756.633 150.072 753.746 150.001 750.626C149.988 750.074 150.426 749.616 150.978 749.603C151.53 749.591 151.988 750.028 152 750.58C152.056 753.009 153.357 755.254 155.5 756.491C158.848 758.424 163.129 757.276 165.062 753.928C165.679 752.86 166.04 751.663 166.072 750.574C166.088 750.022 166.549 749.587 167.101 749.603ZM159 739C161.761 739 164 741.239 164 744V750.5C164 753.261 161.761 755.5 159 755.5C156.239 755.5 154 753.261 154 750.5V744C154 741.239 156.239 739 159 739Z"/></svg></span>
    <div class="mess-compose-input"><span>Aa</span><svg viewBox="289 739 24 24"><path fill-rule="evenodd" d="M301 739C307.627 739 313 744.373 313 751C313 757.627 307.627 763 301 763C294.373 763 289 757.627 289 751C289 744.373 294.373 739 301 739ZM305.712 754.72C304.518 756.249 302.976 756.995 301 756.995C299.024 756.995 297.482 756.249 296.288 754.72C295.948 754.285 295.32 754.207 294.885 754.547C294.449 754.887 294.372 755.515 294.712 755.951C296.286 757.967 298.411 758.995 301 758.995C303.589 758.995 305.714 757.967 307.288 755.951C307.628 755.515 307.551 754.887 307.115 754.547C306.68 754.207 306.052 754.285 305.712 754.72ZM297 747C296.172 747 295.5 747.895 295.5 749C295.5 750.105 296.172 751 297 751C297.828 751 298.5 750.105 298.5 749C298.5 747.895 297.828 747 297 747ZM305 747C304.172 747 303.5 747.895 303.5 749C303.5 750.105 304.172 751 305 751C305.828 751 306.5 750.105 306.5 749C306.5 747.895 305.828 747 305 747Z"/></svg></div><span class="mess-compose-like"><svg viewBox="335 738 24 25"><path fill-rule="evenodd" d="M338.983 749.618C339.608 749.618 340.115 750.126 340.115 750.753V761.865C340.115 762.492 339.608 763 338.983 763H336.948C336.603 763 336.283 762.831 336.087 762.553L336.02 762.443C335.34 761.173 335 759.128 335 756.309C335 753.556 335.324 751.541 335.973 750.265L336.084 750.06C336.24 749.787 336.531 749.618 336.845 749.618H338.983ZM347.156 738.008C349.764 738.348 351.069 740.018 351.069 743.016C351.069 743.622 350.906 744.978 350.58 747.084C355.897 747.195 358.403 748.138 358.403 749.914C358.403 750.352 358.291 750.771 357.762 751.171C358.587 751.676 359 752.395 359 753.327C359 754.26 358.525 755.022 357.576 755.615C358.033 756.131 358.204 756.732 358.089 757.42C357.974 758.108 357.455 758.625 356.531 758.971C356.98 760.915 354.791 761.886 349.964 761.886C342.853 761.886 342.185 760.104 342.163 758.501L342.163 752.04C342.09 750.566 342.52 749.327 343.453 748.325L343.647 748.128C345.3 746.523 346.194 744.918 346.327 743.314L346.133 738.948C346.111 738.447 346.499 738.023 346.999 738.001C347.051 737.999 347.104 738.001 347.156 738.008Z"/></svg></span>
  </div>`;
}
function buildZaloFooterHtml() {
  return `<div class="zalo-composer" aria-hidden="true"><span class="zalo-compose-more">•••</span><div class="zalo-compose-input">Tin nhắn</div><span>♩</span><span>▧</span></div>`;
}
function buildMessengerHtml(s) {
  if (s.messPlatform === 'zalo') return buildZaloHtml(s);
  const history = s.messHistory || [];
  const editable = !!s.messEditable;
  const identityEditable = !!s.messIdentityEditable;
  const audioIndexes = new Set(s.messAudioIndexes || []);
  const partnerName = s.messPartnerName || 'Tùng';
  const partnerAvatar = messengerPartnerAvatar(s.messPartnerAvatar);
  const partnerNameAttr = esc(partnerName).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const partnerNameSize = Math.max(1, Math.min(24, Array.from(partnerName).length));
  const showHeader = s.messShowHeader !== false;
  const rows = history.map((m,i) => {
    const prevSame = i>0 && history[i-1].side===m.side, nextSame=i<history.length-1&&history[i+1].side===m.side;
    const groupCls = prevSame&&nextSame?' middle':nextSame?' first':prevSame?' last':'';
    const avatar = m.side==='theirs' ? (nextSame?'<div class="mess-avatar-space"></div>':`<div class="mess-mini-avatar"><img src="${esc(partnerAvatar)}" alt="" referrerpolicy="no-referrer"/></div>`) : '';
    const textHtml = m.text ? `<div class="mess-bubble">${esc(m.text)}</div>` : '';
    const imageUrl = safeMediaUrl(m.imageUrl);
    const imageHtml = imageUrl ? `<img class="mess-chat-image${m.imageType==='sticker'?' is-sticker':''}" src="${esc(imageUrl)}" alt="" referrerpolicy="no-referrer"/>` : '';
    const audioBadge = editable && (audioIndexes.has(i) || (m.parallelAudios && m.parallelAudios.length)) ? `<span class="mess-audio-badge">🔊 ${m.parallelAudios?.length || 1}<button type="button" onclick="event.stopPropagation();removeMessengerQuickAudio(${i})" title="Xóa âm thanh song song" aria-label="Xóa âm thanh song song">×</button></span>` : '';
    const insert = editable ? `<div class="mess-insert-slot" onclick="event.stopPropagation();beginMessengerInsert(${i})" title="Chèn tin nhắn trước tin này"><span>＋ Chèn tin tại đây</span></div>` : '';
    const menuDirection = i < 4 ? '' : ' menu-up';
    const actions = editable ? `<div class="mess-draft-actions${menuDirection}">
      <button type="button" class="mess-more-btn" onclick="event.stopPropagation();toggleMessengerDraftMenu(${i})" title="Tùy chọn tin nhắn" aria-label="Tùy chọn tin nhắn">•••</button>
      <div class="mess-draft-menu" id="mess-draft-menu-${i}">
        <button type="button" onclick="event.stopPropagation();editMessengerDraft(${i})">Chỉnh sửa</button>
        <button type="button" onclick="event.stopPropagation();openMessengerQuickAudio(${i})">Thêm âm thanh</button>
        <button type="button" class="danger" onclick="event.stopPropagation();deleteMessengerDraft(${i})">Xóa</button>
      </div></div>` : '';
    return `<div class="mess-row ${m.side}${groupCls}" data-mess-index="${i}">${insert}${avatar}<div class="mess-content">${textHtml}${imageHtml}</div>${audioBadge}${actions}</div>`;
  }).join('');
  const appendControl = editable ? `<button type="button" class="mess-append-btn" onclick="event.stopPropagation();beginMessengerInsert(${history.length})">＋ Thêm tin ở cuối</button>` : '';
  const avatarEditAttr = identityEditable ? ` role="button" tabindex="0" title="Đổi ảnh đại diện" onclick="event.stopPropagation();document.getElementById('mess-avatar-file').click()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();document.getElementById('mess-avatar-file').click()}"` : '';
  const nameHtml = identityEditable
    ? `<input class="mess-name-input" value="${partnerNameAttr}" size="${partnerNameSize}" aria-label="Tên đối phương" title="Nhấn để sửa tên" oninput="messPartnerName=this.value;this.size=Math.max(1,Math.min(24,Array.from(this.value).length))" onblur="commitMessengerPartnerName(this)" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"/>`
    : `<span>${esc(partnerName)}</span>`;
  const headerHtml = showHeader ? `<div class="mess-head"><svg class="mess-back" viewBox="19 58 13 23"><path d="M31.183 60.5607C31.7688 59.9749 31.7688 59.0251 31.183 58.4393C30.5972 57.8536 29.6475 57.8536 29.0617 58.4393L19.4393 68.0617C18.8536 68.6475 18.8536 69.5972 19.4393 70.183L29.0617 79.8054C29.6475 80.3912 30.5972 80.3912 31.183 79.8054C31.7688 79.2196 31.7688 78.2699 31.183 77.6841L22.6213 69.1224L31.183 60.5607Z"/></svg><span class="mess-unread">6</span><div class="mess-avatar${identityEditable?' is-editable':''}"${avatarEditAttr}><img src="${esc(partnerAvatar)}" alt="" referrerpolicy="no-referrer"/><span class="mess-online-dot"></span></div>
      <div class="mess-who"><div class="mess-name">${nameHtml}</div><div class="mess-status">Đang hoạt động</div></div>
      <div class="mess-head-icons">
        <svg viewBox="0 0 24 24"><path d="M9.878 13.992 10.008 14.122c2.238 2.166 4.11 3.184 5.618 3.052l.435-.405c.617-.563 1.083-.919 1.397-1.07.654-.313 1.113-.307 2.351.363 1.63.882 2.755 1.907 3.33 2.482.534.534.368 1.454.181 1.862l-.124.227c-.334.58-1.544 2.492-3.863 2.819-.881.124-1.993.01-3.297-.382-2.794-.968-5.576-2.74-8.347-5.319l-.492-.468-.005.005-.238-.242-.242-.238.005-.005-.468-.492C3.67 13.54 1.898 10.758.93 7.964.538 6.66.424 5.548.548 4.667.898 2.184 3.066.972 3.469.747l.041-.023C3.858.522 4.881.286 5.456.861l.272.28c.593.627 1.454 1.653 2.21 3.05.67 1.238.676 1.697.363 2.35-.184.384-.676.995-1.475 1.833-.132 1.508.886 3.381 3.052 5.619Z"/></svg>
        <svg viewBox="0 0 27 18"><path d="M15.2 0C17.299 0 19 1.727 19 3.857v10.286C19 16.273 17.299 18 15.2 18H3.8C1.701 18 0 16.273 0 14.143V3.857C0 1.727 1.701 0 3.8 0h11.4Zm11.666 4.022c.088.151.134.321.134.493v8.97c0 .561-.478 1.015-1.067 1.015-.181 0-.359-.044-.518-.128l-3.729-1.972A1.25 1.25 0 0 1 21 11.291V6.709c0-.461.263-.886.686-1.109l3.729-1.972a1.067 1.067 0 0 1 1.451.394Z"/></svg>
      </div></div>` : '';
  return `<div class="mess-card">${headerHtml}<div class="mess-body">${rows}${appendControl}</div>${s.messShowFooter?buildMessengerFooterHtml():''}</div>`;
}

function buildZaloHtml(s) {
  const history = s.messHistory || [];
  const editable = !!s.messEditable;
  const identityEditable = !!s.messIdentityEditable;
  const audioIndexes = new Set(s.messAudioIndexes || []);
  const partnerName = s.messPartnerName || 'Thảo Mộc Coffee_Group';
  const partnerAvatar = messengerPartnerAvatar(s.messPartnerAvatar);
  const partnerNameAttr = esc(partnerName).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const partnerNameSize = Math.max(1, Math.min(24, Array.from(partnerName).length));
  const showHeader = s.messShowHeader !== false;
  const rows = history.map((m,i) => {
    const avatarEdit = identityEditable ? ` role="button" tabindex="0" title="Đổi ảnh đại diện" onclick="event.stopPropagation();document.getElementById('mess-avatar-file').click()"` : '';
    const avatar = m.side === 'theirs' ? `<div class="zalo-mini-avatar${identityEditable?' is-editable':''}"${avatarEdit}><img src="${esc(partnerAvatar)}" alt="" referrerpolicy="no-referrer"/></div>` : '';
    const textHtml = m.text ? `<div class="zalo-bubble">${esc(m.text)}</div>` : '';
    const imageUrl = safeMediaUrl(m.imageUrl);
    const imageHtml = imageUrl ? `<img class="zalo-chat-image${m.imageType==='sticker'?' is-sticker':''}" src="${esc(imageUrl)}" alt="" referrerpolicy="no-referrer"/>` : '';
    const audioBadge = editable && (audioIndexes.has(i) || (m.parallelAudios && m.parallelAudios.length)) ? `<span class="mess-audio-badge">🔊 ${m.parallelAudios?.length || 1}<button type="button" onclick="event.stopPropagation();removeMessengerQuickAudio(${i})" title="Xóa âm thanh song song" aria-label="Xóa âm thanh song song">×</button></span>` : '';
    const insert = editable ? `<div class="mess-insert-slot" onclick="event.stopPropagation();beginMessengerInsert(${i})" title="Chèn tin nhắn trước tin này"><span>＋ Chèn tin tại đây</span></div>` : '';
    const menuDirection = i < 4 ? '' : ' menu-up';
    const actions = editable ? `<div class="mess-draft-actions${menuDirection}">
      <button type="button" class="mess-more-btn" onclick="event.stopPropagation();toggleMessengerDraftMenu(${i})" title="Tùy chọn tin nhắn" aria-label="Tùy chọn tin nhắn">•••</button>
      <div class="mess-draft-menu" id="mess-draft-menu-${i}">
        <button type="button" onclick="event.stopPropagation();editMessengerDraft(${i})">Chỉnh sửa</button>
        <button type="button" onclick="event.stopPropagation();openMessengerQuickAudio(${i})">Thêm âm thanh</button>
        <button type="button" class="danger" onclick="event.stopPropagation();deleteMessengerDraft(${i})">Xóa</button>
      </div></div>` : '';
    return `<div class="mess-row zalo-row ${m.side}" data-mess-index="${i}">${insert}${avatar}<div class="zalo-content">${textHtml}${imageHtml}</div>${audioBadge}${actions}</div>`;
  }).join('');
  const appendControl = editable ? `<button type="button" class="mess-append-btn" onclick="event.stopPropagation();beginMessengerInsert(${history.length})">＋ Thêm tin ở cuối</button>` : '';
  const avatarEditAttr = identityEditable ? ` role="button" tabindex="0" title="Đổi ảnh đại diện" onclick="event.stopPropagation();document.getElementById('mess-avatar-file').click()"` : '';
  const nameHtml = identityEditable
    ? `<input class="zalo-name-input" value="${partnerNameAttr}" size="${partnerNameSize}" aria-label="Tên cuộc trò chuyện" oninput="messPartnerName=this.value;this.size=Math.max(1,Math.min(24,Array.from(this.value).length))" onblur="commitMessengerPartnerName(this)" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"/>`
    : `<span>${esc(partnerName)}</span>`;
  const headerHtml = showHeader ? `<div class="zalo-head"><span class="zalo-back">‹</span><div class="zalo-title"><div>${nameHtml}</div></div>
      <div class="zalo-head-icons" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M15 9l5-3v12l-5-3v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2z"/></svg><svg viewBox="0 0 24 24"><circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/></svg><svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13"/></svg></div>
    </div>` : '';
  return `<div class="zalo-card">${headerHtml}<div class="zalo-body">${rows}${appendControl}</div>${s.messShowFooter?buildZaloFooterHtml():''}
    ${identityEditable ? `<div class="zalo-avatar-editor"${avatarEditAttr}><img src="${esc(partnerAvatar)}" alt=""/></div>` : ''}
  </div>`;
}

// TikTok comment/reply — copy nguyên cấu trúc từ threads/tiktok_ui_mock.html
// đã chốt với người dùng (mục 5.39 CONTEXT.md — KHÔNG tự vẽ lại UI, đúng tinh
// thần mục 7.1 áp dụng luôn cho platform mới). Nền card trắng bo góc riêng
// (khác Threads — card TikTok KHÔNG cộng dồn liền mạch vào 1 nền chung, mỗi
// comment là 1 khối card độc lập nổi trên video nền), icon tim viền rỗng +
// mũi tên "Xem N câu trả lời" dùng đúng path SVG người dùng cung cấp.
// Ảnh đính kèm comment TikTok — bo góc nhẹ, KHÔNG có nút share (khác
// .thr-img-wrap của Threads) — người dùng xác nhận chỉ cần thêm được ảnh,
// không cần thêm dislike hay chi tiết UI nào khác (đối chiếu threads/commentIMG.jpg).
function tiktokImgWrapHtml(imgUrl) {
  if (!imgUrl) return '';
  return `<div class="tk-img-wrap"><img src="${imgUrl}" alt="" referrerpolicy="no-referrer"/></div>`;
}

function buildTiktokCommentHtml(s, id1, posAttr, stepAttr = '') {
  const isReply = s.type === 'reply';
  const cardCls = 'tk-comment' + (isReply ? ' tk-comment-reply' : '');
  const likeCount = (s.stats && s.stats.likes) || '0';
  const timeLabel = id1.timeLabel;
  const replyNoteHtml = (!isReply && s.hasReplyCount) ? `
      <div class="tk-view-replies">
        Xem ${s.replyCount} câu trả lời
        <svg viewBox="0 0 48 48" fill="currentColor"><path d="m24 27.76 13.17-13.17a1 1 0 0 1 1.42 0l2.82 2.82a1 1 0 0 1 0 1.42L25.06 35.18a1.5 1.5 0 0 1-2.12 0L6.59 18.83a1 1 0 0 1 0-1.42L9.4 14.6a1 1 0 0 1 1.42 0L24 27.76Z"/></svg>
      </div>` : '';
  // Badge "Nhà sáng tạo" (mục 5.43 CONTEXT.md) — dùng CHUNG checkbox
  // s.isAuthor đã có sẵn cho Threads ("Là tác giả bài gốc"), chỉ khác nhãn
  // hiển thị theo platform (Threads: badge riêng "Tác giả"; TikTok: nối
  // "· Nhà sáng tạo" ngay sau tên). TÁCH RIÊNG khỏi .tk-uname (element cha
  // .tk-uname-row bọc cả 2, không phải .tk-uname bọc badge như trước) — vì
  // CSS filter:blur() trên phần tử cha áp dụng cho MỌI nội dung con không
  // trừ ngoại lệ, nếu badge nằm trong .tk-uname nó sẽ bị mờ theo khi bật
  // "Làm mờ tên/avatar" (mục 5.10/5.42) dù badge không phải thông tin định
  // danh cần che — người dùng phản hồi thực tế không thấy được badge màu
  // khi tick "Là tác giả" vì lý do này.
  const creatorBadge = s.isAuthor ? '<span class="tk-creator-badge">· Nhà sáng tạo</span>' : '';
  const showFooter = !s.progressiveReveal || s._progressiveFinal;
  const identityClass=s.identityBlurMode==='blur'?' identity-force-blur':s.identityBlurMode==='clear'?' identity-force-clear':'';
  return `<div class="tcard-pos${s.progressiveReveal?' progressive-reveal-card':''}${identityClass}"${stepAttr}${posAttr}><div class="${cardCls}">
    ${tiktokAvatarHtml(id1.uname, s.hue, id1.avatarUrl)}
    <div class="tk-main">
      <div class="tk-uname-row"><div class="tk-uname">${esc(id1.uname)}</div>${creatorBadge}</div>
      <div class="tk-text">${esc(s.text)}</div>
      ${showFooter?tiktokImgWrapHtml(s.imgUrl):''}
      ${showFooter?`<div class="tk-foot">
        <div class="tk-foot-left">
          <span class="tk-date">${esc(timeLabel)}</span>
          <span class="tk-reply-btn">Trả lời</span>
        </div>
        <div class="tk-foot-right">
          <div class="tk-like">
            <svg viewBox="0 0 48 48" fill="currentColor" stroke="currentColor" stroke-width="1.6"><path d="M24 12.62c3.91-4.08 9.84-4.1 13.6-.42a9.48 9.48 0 0 1 0 13.63L25.06 38.07a1.5 1.5 0 0 1-2.1 0L10.4 25.83a9.48 9.48 0 0 1 0-13.63c3.77-3.68 9.7-3.66 13.61.42Zm-1.9 2.36c-2.8-3.2-6.99-3.2-9.61-.63a6.48 6.48 0 0 0 0 9.33L24 34.91l11.51-11.23a6.48 6.48 0 0 0 0-9.33c-2.62-2.56-6.8-2.56-9.6.63l-.05.05-.81.8a1.5 1.5 0 0 1-2.1 0l-.81-.8-.04-.05Z"/></svg>
            <span class="tk-like-count">${esc(String(likeCount))}</span>
          </div>
        </div>
      </div>
      ${replyNoteHtml}`:''}
    </div>
  </div></div>`;
}

// Chuỗi transform đầy đủ cho #meme-overlay — dùng chung index.html/
// export_render.html (mục 5.21). Neo mép trên cố định (CSS top:20%, mục
// 5.31) — KHÔNG còn translateY(-50%) căn giữa: ảnh cao thấp khác nhau giữa
// các step (vd. chuỗi crop nhiều-vùng tăng dần) phải đứng yên ở đỉnh thay
// vì giãn đều 2 phía. offsetX/offsetY (D-pad) dịch thêm từ điểm neo đó.
function memeOverlayTransform(memeStep) {
  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const ox = finite(memeStep && memeStep.offsetX);
  const oy = finite(memeStep && memeStep.offsetY);
  const rot = finite(memeStep && memeStep.rotation);
  const scale = Math.max(.35, Math.min(2.5, finite(memeStep && memeStep.overlayScale, 1)));
  return `translate(${ox}cqw, ${oy}cqw) rotate(${rot}deg) scale(${scale})`;
}

// Meme dạng video/gif ĐỘNG (khác ảnh tĩnh) — dùng chung export_render.html
// (quyết định bỏ qua rotation khi xuất video) và index.html (badge cảnh báo
// UI trong step-card). Trước đây định nghĩa riêng trong export_render.html,
// chuyển vào đây để 1 nguồn duy nhất, index.html cũng gọi được.
function isDynamicMeme(memeStep) {
  if (!memeStep || !memeStep.mediaUrl) return false;
  if (memeStep.mediaIsVideo) return true;
  return /\.gif(\?|$)/i.test(memeStep.mediaUrl);
}

// ---------- Render cảnh tại thời điểm t ----------
let _lastStackKey = null;
let _lastStackIds = [];   // mảng id của _lastStackKey — dùng để so khớp vị trí với stack mới
let _lastStackRevisions = []; // revision của từng id tại _lastStackIds, cùng vị trí — phát hiện "id giữ nguyên nhưng nội dung đã sửa"
let _lastStackRevealStages = [];
let _lastMemeKey = null;
let _lastRetainedMemeKey = null;

// Đếm số lần mỗi step id được sửa nội dung (submitStepModal khi editingStepId
// khớp) — dùng để renderAtTime() biết 1 step "trông giống cũ" (id không đổi,
// vẫn nằm đúng vị trí trong stack) có thực sự cần vẽ lại hay không, tránh
// phải deep-compare nội dung step (text/stats/ảnh...) mỗi frame.
const stepRevision = new Map();
function bumpStepRevision(id) { stepRevision.set(id, (stepRevision.get(id) || 0) + 1); }

// Cảnh tại thời điểm t: nội dung post/comment/reply tồn tại trong cả cửa sổ
// đọc lẫn "gap sau" của chính nó. Gap là thời gian CHỜ trên trạng thái cuối
// của card trước, không phải khoảng màn hình trống. Bước sang step kế tiếp
// mới đổi stack/clearBefore theo luật của step kế.
// Stack tại t = duyệt NGƯỢC từ step hiện tại (step mà t đang nằm trong nó),
// cộng dồn các step post/comment/reply liền kề TRƯỚC nó mà giữa chúng không
// có khoảng gap nào chưa kết thúc — tức chuỗi step "dính liền nhau" tính đến
// t. Gặp step gap > 0 đã trôi qua (đang ở giữa 2 step) → dừng, hoặc gặp
// clearBefore → dừng (không cộng dồn qua ranh giới "xoá nội dung cũ").
function computeSceneAt(t) {
  // Tìm step track-chính đang "giữ màn" tại t. Khác _end: nếu t đang nằm
  // trong gap sau step, vẫn chọn step đó để nó đứng yên chờ step sau.
  const mainSteps = steps.filter(s => !isParallelMedia(s));
  let curIdx = -1;
  for (let i = 0; i < mainSteps.length; i++) {
    const s = mainSteps[i];
    const holdEnd = Math.max(Number(s._end || 0), Number(s._end || 0) + Math.max(0, Number(s.gap || 0)));
    if (t >= s._start - 1e-6 && t < holdEnd - 1e-6) { curIdx = i; break; }
  }

  let stack = [];
  if (curIdx >= 0) {
    // Cộng dồn ngược: từ curIdx lùi về, miễn là mỗi step liền trước kết thúc
    // đúng lúc step liền sau bắt đầu (không có gap chưa trôi hết ở giữa) và
    // không dính clearBefore chặn giữa đường. clearBefore là NGUỒN QUYẾT
    // ĐỊNH DUY NHẤT (giống hệt post/comment/reply, mục 5.37 CONTEXT.md) —
    // logic render.
    let i = curIdx;
    const visibleMessengerConversations = new Set();
    while (i >= 0) {
      const s = mainSteps[i];
      if (s.type === 'mess') {
        const conversationKey = s.conversationId || `mess-step-${s.id}`;
        if (!visibleMessengerConversations.has(conversationKey)) {
          stack.unshift(s);
          visibleMessengerConversations.add(conversationKey);
        }
      } else if (['post','comment','reply','text','note'].includes(s.type)) stack.unshift(s);
      if (s.clearBefore) break;
      const prev = mainSteps[i - 1];
      if (!prev || Math.abs(prev._end + (prev.gap || 0) - s._start) > 1e-6) break;
      i--;
    }
  }

  // TEXT có thể được kéo vào một card khác để chạy song song. Nó không chiếm
  // thời gian trên track chính nhưng vẫn được render như card chữ đầy đủ trong
  // stack đúng cửa sổ _start/_end riêng của nó.
  for(const s of steps){
    if(!isParallelText(s)||t<s._start-1e-6||t>=s._end-1e-6)continue;
    if(!stack.some(item=>item.id===s.id))stack.push(s);
  }

  // Ảnh/sticker tuần tự tồn tại tiếp cho tới khi một step sau bật
  // clearBefore. Media song song vẫn chỉ sống đúng bằng cửa sổ card neo.
  // Nhờ vậy sticker không tick "Xóa nội dung cũ" có thể nằm cùng ảnh trước.
  const memeSteps = [];
  if (curIdx >= 0) for (const s of steps) {
    if ((s._start || 0) > t + 1e-6) continue;
    if (!isParallelMedia(s) && s.clearBefore) memeSteps.length = 0;
    if (s.type !== 'image' && s.type !== 'sticker') continue;
    if (isParallelVisual(s) && !(t >= s._start - 1e-6 && t < s._end - 1e-6)) continue;
    memeSteps.push(s);
  }
  const memeStep = memeSteps.length ? memeSteps[memeSteps.length - 1] : null;

  return { stack, memeStep, memeSteps };
}

function retainedMemeHtml(step) {
  const mediaUrl = safeMediaUrl(step?.mediaUrl);
  if (!step || !mediaUrl) return '';
  const media = step.mediaIsVideo
    ? `<video src="${esc(mediaUrl)}" preload="metadata" playsinline></video>`
    : `<img src="${esc(mediaUrl)}" alt=""/>`;
  return `<div class="meme-overlay retained-meme ${step.type==='sticker'?'sticker-overlay':''}" data-id="${esc(step.id)}" style="display:block;transform:${memeOverlayTransform(step)}">${media}</div>`;
}

function autoScrollStack() {
  const frame = document.getElementById('preview-frame');
  const stackEl = document.getElementById('thr-stack');
  const frameH = frame.clientHeight;
  const stackH = stackEl.scrollHeight; // gồm cả padding-top
  const targetBottom = frameH * 0.9;
  const translate = Math.min(0, targetBottom - stackH);
  stackEl.style.transform = `translateY(${translate}px)`;
}

function autoScrollMessenger(root = document) {
  root.querySelectorAll('.mess-body,.zalo-body').forEach(body => {
    body.scrollTop = body.scrollHeight;
  });
}
