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
function effectiveDuration(s) { return s.duration || 0; } // duration = độ dài TTS thật (post/comment/reply) hoặc số chỉnh tay (meme/audio)

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
function isParallelMedia(s) { return isParallelAudio(s) || isParallelVisual(s); }

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
    s._start = lastMainStep ? lastMainStep._start : 0;
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
    const pause = Math.max(0, Number(s.progressivePause ?? .3));
    const localT = Math.max(0, Number(t) - Number(s._start));
    let stage = 1, nextStart = clipDurations[0] + pause;
    while (stage < parts.length && localT >= nextStart - 1e-6) {
      stage++;
      nextStart += clipDurations[stage - 1] + (stage < parts.length ? pause : 0);
    }
    return stage;
  }
  const weights = parts.map(part => Math.max(1, [...part].length));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  const duration = Math.max(.001, Number(s._end) - Number(s._start));
  const elapsedWeight = Math.max(0, Math.min(1, (Number(t) - Number(s._start)) / duration)) * totalWeight;
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
function displayIdentity(seed, realUname, realAvatarUrl, realTimeLabel) {
  if (!blurIdentityOn()) return { uname: realUname, avatarUrl: realAvatarUrl, timeLabel: realTimeLabel };
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

function buildCardHtml(s) {
  const posAttr = posTransform(s);
  const stepAttr = ` data-step-id="${esc(s.id)}"`;
  const posClass = s.progressiveReveal ? 'tcard-pos progressive-reveal-card' : 'tcard-pos';
  if (s.type === 'mess') return `<div class="${posClass}"${stepAttr}${posAttr}>${buildMessengerHtml(s)}</div>`;
  if (s.type === 'post') {
    const id1 = displayIdentity(s.id + ':m', s.uname, s.avatarUrl, (s.stats&&s.stats.timeLabel)||'vừa xong');
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
    const id1 = displayIdentity(s.id + ':m', s.uname, s.avatarUrl, (s.stats&&s.stats.timeLabel)||'vừa xong');
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
    <span class="mess-compose-icon"><svg viewBox="0 0 16 16"><path d="M8 .5A3.5 3.5 0 0 0 4.5 4v4a3.5 3.5 0 1 0 7 0V4A3.5 3.5 0 0 0 8 .5z"/><path d="M8.749 13.954 8.75 14v.75a.75.75 0 0 1-1.5 0V14l.001-.046A6.001 6.001 0 0 1 2 8v-.25a.75.75 0 0 1 1.5 0V8a4.5 4.5 0 1 0 9 0v-.25a.75.75 0 0 1 1.5 0V8a6.001 6.001 0 0 1-5.251 5.954z"/></svg></span>
    <span class="mess-compose-icon"><svg viewBox="0 0 16 16"><path d="M7 4.25a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0z"/><path d="M9.302.5H6.698C5.8.5 5.05.5 4.456.58c-.628.084-1.195.27-1.65.725-.456.456-.642 1.023-.726 1.65C2 3.55 2 4.3 2 5.199v5.604c0 .899 0 1.648.08 2.242.084.628.27 1.195.725 1.65a2.3 2.3 0 0 0 .81.522c.237.092.489.152.752.192.571.086 1.281.091 2.122.092h3.174c.795-.002 1.472-.011 2.023-.1.573-.094 1.088-.285 1.509-.705.455-.456.64-1.023.725-1.65.08-.595.08-1.344.08-2.243V5.198c0-.898 0-1.648-.08-2.242-.084-.628-.27-1.195-.726-1.65C12.74.85 12.172.663 11.544.58 10.95.5 10.2.5 9.302.5zm2.602 6.427a1.75 1.75 0 0 0-2.475 0L7 9.356l-.43-.43a1.75 1.75 0 0 0-2.474 0l-.596.597V5.25c0-.964.002-1.612.067-2.095.062-.461.169-.659.3-.789.13-.13.327-.237.788-.3C5.138 2.003 5.786 2 6.75 2h2.5c.964 0 1.612.002 2.095.067.461.062.659.169.789.3.13.13.237.327.3.788.064.483.066 1.131.066 2.095v2.272l-.596-.595z"/></svg></span>
    <span class="mess-compose-icon"><svg viewBox="0 0 16 16"><path d="M8.305 1h-.61c-1.368 0-2.47 0-3.337.116-.9.121-1.658.38-2.26.982-.602.602-.86 1.36-.982 2.26C1 5.225 1 6.327 1 7.695v.61c0 1.367 0 2.47.116 3.336.121.9.38 1.658.982 2.26.602.602 1.36.861 2.26.982.867.117 1.97.117 3.337.117.268 0 .537.002.805-.004v-1.034c0-.764 0-1.423.028-1.99a5.007 5.007 0 0 1-3.761-1.149.75.75 0 0 1 .966-1.147c.61.513 1.4.823 2.267.823.212 0 .419-.018.62-.053a.754.754 0 0 1 .137-.012c.127-.404.32-.755.621-1.056.475-.474 1.076-.684 1.812-.783.714-.096 1.626-.096 2.772-.095h1.034c.006-.269.004-.537.004-.805 0-1.368 0-2.47-.117-3.337-.12-.9-.38-1.658-.982-2.26-.601-.602-1.36-.86-2.26-.982C10.774.999 9.673.999 8.306 1zm3.693 4.747a1.249 1.249 0 1 1-2.498 0 1.249 1.249 0 0 1 2.498 0zM5.248 7a1.249 1.249 0 1 1 0-2.498 1.249 1.249 0 0 1 0 2.498z"/><path d="M14.87 9.5H14c-1.193 0-2.037 0-2.677.087-.625.084-.98.24-1.238.498-.257.258-.414.613-.498 1.238-.086.64-.087 1.484-.087 2.676v.87c.076-.022.15-.048.224-.079.508-.21.899-.602 1.392-1.096l2.578-2.578c.494-.493.886-.884 1.096-1.393.03-.073.057-.148.08-.223z"/></svg></span>
    <span class="mess-compose-icon"><svg viewBox="0 0 16 16"><path d="M7.695 1h.61c1.367 0 2.47 0 3.337.117.9.12 1.658.38 2.26.981.602.602.86 1.36.982 2.26.116.867.116 1.97.116 3.337v.61c0 1.367 0 2.47-.117 3.337-.12.9-.38 1.658-.981 2.26-.602.602-1.36.86-2.26.982-.867.116-1.97.116-3.337.116h-.61c-1.367 0-2.47 0-3.337-.117-.9-.12-1.658-.38-2.26-.981-.602-.602-.86-1.36-.981-2.26C1 10.775 1 9.672 1 8.305v-.61c0-1.367 0-2.47.117-3.337.12-.9.38-1.658.981-2.26.602-.602 1.36-.86 2.26-.981C5.225 1 6.328 1 7.695 1zM4.812 5.008A2.062 2.062 0 0 0 2.75 7.07v1.942a1.995 1.995 0 0 0 3.99 0v-.727a.75.75 0 0 0-1.5 0v.727a.495.495 0 0 1-.99 0V7.07c0-.31.252-.562.562-.562H5a.75.75 0 1 0 0-1.5h-.188zm3.438 0a.75.75 0 0 0-.75.75v4.5a.75.75 0 0 0 1.5 0v-4.5a.75.75 0 0 0-.75-.75zm2.5 0a.75.75 0 0 0-.75.75v4.5a.75.75 0 0 0 1.5 0v-1.5h.5a.75.75 0 0 0 0-1.5h-.5v-.75h1a.75.75 0 0 0 0-1.5h-1.75z"/></svg></span>
    <div class="mess-compose-input"><span>Aa</span><svg viewBox="0 0 38 38"><path d="M26.5 18c-1.379 0-2.5-1.121-2.5-2.5s1.121-2.5 2.5-2.5 2.5 1.121 2.5 2.5-1.121 2.5-2.5 2.5m2.072 6.549C26.428 26.742 22.938 28 19 28s-7.428-1.258-9.572-3.451a1.5 1.5 0 0 1 2.144-2.098C13.133 24.047 15.909 25 19 25s5.867-.953 7.428-2.549a1.5 1.5 0 0 1 2.144 2.098M11.5 13c1.379 0 2.5 1.121 2.5 2.5S12.879 18 11.5 18 9 16.879 9 15.5s1.121-2.5 2.5-2.5M19 0C8.523 0 0 8.523 0 19s8.523 19 19 19 19-8.523 19-19S29.477 0 19 0"/></svg></div><span class="mess-compose-like"><svg viewBox="0 0 16 16"><path d="M16,9.1c0-0.8-0.3-1.1-0.6-1.3c0.2-0.3,0.3-0.7,0.3-1.2c0-1-0.8-1.7-2.1-1.7h-3.1c0.1-0.5,0.2-1.3,0.2-1.8c0-1.1-0.3-2.4-1.2-3C9.3,0.1,9,0,8.7,0C8.1,0,7.7,0.2,7.6,0.4C7.5,0.5,7.5,0.6,7.5,0.7L7.6,3c0,0.2,0,0.4-0.1,0.5L5.7,6.6c0,0-0.1,0.1-0.1,0.1l-0.3,0.1C5.1,7,5,7.2,5,7.4v6.1c0,0.2,0.1,0.4,0.2,0.5c0.1,0.1,1,1,2,1h5.2c0.9,0,1.4-0.3,1.8-0.9c0.3-0.5,0.2-1,0.1-1.4c0.5-0.2,0.9-0.5,1.1-1.2c0.1-0.4,0-0.8-0.2-1C15.6,10.3,16,9.9,16,9.1z"/><path d="M3.3,6H0.7C0.3,6,0,6.3,0,6.7v8.5C0,15.7,0.3,16,0.7,16h2.5C3.7,16,4,15.7,4,15.3V6.7C4,6.3,3.7,6,3.3,6z"/></svg></span>
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
  return `<div class="mess-card">
    <div class="mess-head"><div class="mess-avatar${identityEditable?' is-editable':''}"${avatarEditAttr}><img src="${esc(partnerAvatar)}" alt="" referrerpolicy="no-referrer"/><span class="mess-online-dot"></span></div>
      <div class="mess-who"><div class="mess-name">${nameHtml} <span class="mess-name-chevron"></span></div></div>
      <div class="mess-head-icons">
        <svg viewBox="0 0 16 16"><path d="M4.776 1.111a1.728 1.728 0 0 0-2.56-.127l-.472.472a4.516 4.516 0 0 0-.739 5.415 21.46 21.46 0 0 0 8.123 8.123 4.516 4.516 0 0 0 5.415-.738L15 13.8a1.75 1.75 0 0 0-.13-2.591l-2.131-1.745a1.75 1.75 0 0 0-2.143-.056L9.22 10.416a.052.052 0 0 1-.06.002 12.077 12.077 0 0 1-3.578-3.58.052.052 0 0 1 .002-.058l1.008-1.375a1.75 1.75 0 0 0-.056-2.143l-1.76-2.15z"/></svg>
        <svg viewBox="0 0 16 16"><path d="M6.304 2h-.108c-1.133 0-2.058 0-2.79.098-.763.103-1.425.325-1.954.854C.923 3.48.7 4.142.598 4.907.5 5.637.5 6.563.5 7.697v.607c0 1.133 0 2.058.098 2.79.103.763.325 1.425.854 1.954.529.529 1.19.751 1.955.854.73.098 1.656.098 2.79.098h.107c1.133 0 2.058 0 2.79-.098.763-.103 1.425-.325 1.954-.854.529-.529.751-1.19.854-1.955.038-.286.062-.602.076-.949l1.93 1.418a1 1 0 0 0 1.592-.806V5.228a1 1 0 0 0-1.57-.822l-1.956 1.357a9.984 9.984 0 0 0-.072-.856c-.103-.764-.325-1.426-.854-1.955-.529-.529-1.19-.751-1.955-.854C8.363 2 7.437 2 6.303 2z"/></svg>
        <svg viewBox="0 0 16 16"><path d="M3 8a1 1 0 0 1 1-1h8a1 1 0 1 1 0 2H4a1 1 0 0 1-1-1z"/></svg>
        <svg viewBox="0 0 16 16"><path d="M13.457 3.957a1 1 0 0 0-1.414-1.414L8 6.586 3.957 2.543a1 1 0 0 0-1.414 1.414L6.586 8l-4.043 4.043a1 1 0 1 0 1.414 1.414L8 9.414l4.043 4.043a1 1 0 0 0 1.414-1.414L9.414 8l4.043-4.043z"/></svg>
      </div></div><div class="mess-body">${rows}${appendControl}</div>${s.messShowFooter?buildMessengerFooterHtml():''}</div>`;
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
  return `<div class="zalo-card">
    <div class="zalo-head"><span class="zalo-back">‹</span><div class="zalo-title"><div>${nameHtml}</div></div>
      <div class="zalo-head-icons" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M15 9l5-3v12l-5-3v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2z"/></svg><svg viewBox="0 0 24 24"><circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/></svg><svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13"/></svg></div>
    </div><div class="zalo-body">${rows}${appendControl}</div>${s.messShowFooter?buildZaloFooterHtml():''}
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
  return `<div class="tcard-pos${s.progressiveReveal?' progressive-reveal-card':''}"${stepAttr}${posAttr}><div class="${cardCls}">
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

// Cảnh tại thời điểm t: nội dung (post/comment/reply) KHÔNG tồn tại xuyên
// suốt — mỗi step chỉ "sống" trong đúng cửa sổ [_start, _end) của nó (thời
// gian TTS đọc). Bước sang "gap" (khoảng lặng sau _end), nội dung của step
// đó biến mất khỏi màn hình hoàn toàn cho tới khi step kế bắt đầu.
// Stack tại t = duyệt NGƯỢC từ step hiện tại (step mà t đang nằm trong nó),
// cộng dồn các step post/comment/reply liền kề TRƯỚC nó mà giữa chúng không
// có khoảng gap nào chưa kết thúc — tức chuỗi step "dính liền nhau" tính đến
// t. Gặp step gap > 0 đã trôi qua (đang ở giữa 2 step) → dừng, hoặc gặp
// clearBefore → dừng (không cộng dồn qua ranh giới "xoá nội dung cũ").
function computeSceneAt(t) {
  // Tìm step track-chính (post/comment/reply/image + audio TUẦN TỰ) đang
  // "sống" tại t — chỉ audio SONG SONG (isParallelAudio) mới bị loại khỏi
  // track chính (xem syncSfxAudio cho phần phát tiếng của track song song).
  const mainSteps = steps.filter(s => !isParallelMedia(s));
  let curIdx = -1;
  for (let i = 0; i < mainSteps.length; i++) {
    if (t >= mainSteps[i]._start - 1e-6 && t < mainSteps[i]._end - 1e-6) { curIdx = i; break; }
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
      } else if (s.type === 'post' || s.type === 'comment' || s.type === 'reply') stack.unshift(s);
      if (s.clearBefore) break;
      const prev = mainSteps[i - 1];
      if (!prev || Math.abs(prev._end + (prev.gap || 0) - s._start) > 1e-6) break;
      i--;
    }
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
