# Threads Short Video Maker — Ngữ cảnh dự án

> Đọc file này trước khi làm bất cứ việc gì trong thư mục `threads/`. Đây là
> toàn bộ ý định, quyết định thiết kế, và lý do đằng sau — không chỉ là "code
> làm gì" mà còn "tại sao lại làm vậy", để không vô tình phá vỡ các quyết định
> đã chốt qua nhiều vòng chỉnh sửa với người dùng.

## 1. Mục tiêu tổng thể

Công cụ tự dựng **short video (9:16) kiểu "Threads story"** để đăng
TikTok/Reels/Shorts, dạng: nền là video gameplay (Subway Surfers, Minecraft
parkour...) chạy liên tục, phía trên là các bài post/comment Threads lần lượt
xuất hiện, có giọng đọc (TTS) tự động theo nội dung, kèm nhạc nền và hiệu ứng
meme (video/gif/âm thanh) xen giữa.

Người dùng **không muốn dùng phần mềm dựng video** (CapCut, Premiere...) —
mọi thao tác edit phải làm được ngay trên web UI này, kéo/thả/chỉnh số là đủ,
không cần thao tác timeline phức tạp.

## 2. Vị trí file, quan hệ với phần còn lại của repo

- Đây là nhánh git riêng tên `threads` (tách từ nhánh `macos`), thư mục
  `threads/` là workspace riêng cho dự án này — **không đụng vào** các file
  Python ở gốc repo (`content_video.py`, `translate_server.py`...), đó là một
  công cụ khác (dịch video / tạo content Threads dạng ảnh tĩnh) không liên
  quan tới short-video-maker này. Ngoại lệ duy nhất: `threads/tts_server.py`
  (mục 5.7) *import* (không sửa) `translate_video.py` để tái dùng hạ tầng
  CapCut TTS — vẫn không sửa file gốc, chỉ đọc hàm dùng chung.
- `threads/threads_ui_mock.html` — bản **mock UI tĩnh đã chốt xong**, gồm 4
  mẫu minh hoạ: Post, Comment đơn, Comment+Reply, Post+ảnh thật. Đây là
  **nguồn chân lý (source of truth) cho toàn bộ style Threads** (avatar, tên,
  badge follow, icon tim/comment/repost/share, đường nối reply...). Đã qua
  rất nhiều vòng chỉnh sửa tỉ mỉ với người dùng (xem mục 4). **Không tự vẽ lại
  UI Threads — luôn copy nguyên cấu trúc CSS/HTML từ file này**, chỉ quy đổi
  đơn vị nếu cần (xem mục 5.2).
- `threads/index.html` — **ứng dụng thật đang phát triển**, nơi ráp toàn bộ
  UI Threads (từ mock) vào một bộ máy dựng video có thể thao tác (step
  engine). Đây là file duy nhất người dùng thao tác thật, chạy trực tiếp
  bằng cách mở trong trình duyệt (không cần server/build step gì — thuần
  HTML/CSS/JS một file).
- `threads/render_shared.js` — engine timeline + build-HTML thẻ Threads
  TÁCH RIÊNG (mục 5.17), dùng chung giữa `index.html` và
  `export_render.html` để xuất video không bị lệch UI. Sửa style/markup
  thẻ Threads thì sửa Ở ĐÂY, không sửa trùng lặp trong `index.html`.
- `threads/export_render.html` — trang nội bộ (KHÔNG dành cho người dùng mở
  trực tiếp), Playwright điều khiển để chụp ảnh thẻ Threads phục vụ xuất
  video (mục 5.17).
- `threads/tts_server.py`, `threads/media_server.py`,
  `threads/export_server.py` — 3 server phụ trợ độc lập (cổng 8007/8010/
  8011), người dùng cần tự chạy song song với việc mở `index.html` để dùng
  đủ tính năng TTS/upload-media/xuất-video (mục 5.7/5.12/5.17).

## 3. Lộ trình đã đi qua (không lặp lại các bước này)

Thứ tự phát triển UI Threads (đã hoàn tất, đừng làm lại từ đầu):

1. Dựng mock 4 mẫu Threads UI thô ban đầu — dùng style tự đoán, nhiều chỗ sai
   (khoảng cách, icon tự vẽ, layout thụt lề).
2. Qua ~15 vòng phản hồi trực tiếp từ người dùng (so sánh với ảnh chụp màn
   hình Threads thật, có cả HTML/CSS thật của Threads người dùng cung cấp),
   sửa tới khi khớp pixel-perfect: icon SVG dùng đúng `path` gốc của Threads
   (không tự vẽ), avatar/dấu "+" avatar dùng kỹ thuật vẽ bằng CSS pseudo-
   element (không dùng ký tự "+" vì glyph font không căn giữa hình học được),
   cấu trúc `.thr-row > avatar-wrap + thr-main > thr-head(.who + .side-
   actions)` tách bạch tên/giờ khỏi nút "⋯", post thì nội dung KHÔNG thụt lề
   theo avatar còn comment thì CÓ thụt lề, đường nối dọc reply dùng kỹ thuật
   "line phủ hết chiều cao còn lại, avatar con tự che phần thừa" (giống cách
   Threads thật làm, xác nhận qua việc đọc HTML thật của Threads).
3. Sau khi mock chốt xong, **build lại `index.html` từ đầu** theo đúng mock —
   có một lần AI tự ý vẽ lại CSS (dùng `align-items:center` sai, thiếu
   `.who`/`.side-actions`, thiếu đường nối reply) và bị người dùng phát hiện
   ngay ("bạn làm sai UI quá sai so với mock UI rồi"). Bài học: **luôn copy
   nguyên văn CSS/HTML từ mock, verify bằng cách so ảnh chụp trực tiếp, không
   tự tin "nhớ đại khái" rồi viết lại.**
4. Dựng "step engine" — cơ chế kịch bản dạng danh sách các bước (xem mục 5).
5. Sửa cơ chế "gap" theo đúng ý người dùng (ban đầu AI hiểu sai — xem mục
   5.4).
6. Thêm ảnh đính kèm, số liệu tương tác tuỳ chỉnh, thời gian đăng tuỳ chỉnh
   cho mỗi step.

## 4. Các quyết định UI đã chốt — KHÔNG tự ý đổi lại

Nếu cần sửa UI Threads, luôn tham chiếu `threads_ui_mock.html` trước. Vài
điểm dễ làm sai lại nếu không đọc kỹ:

- **Font**: `SFPro` (San Francisco, dùng qua `local("SFNS")`) — đúng font hệ
  điều hành Apple mà Threads thật dùng.
- **Theme**: nền trắng, chữ đen (Threads light mode) — không phải dark mode.
- **Icon**: 4 icon dùng `path` SVG chính thức lấy trực tiếp từ HTML thật của
  Threads (tim đặc `ic-heart-fill`, bong bóng chat `ic-comment`, 2 mũi tên
  cong `ic-repost`, paper-plane `ic-share`). Mỗi `<path>` có `fill=
  "currentColor"` **gắn trực tiếp trên path** (không chỉ dựa vào class CSS)
  — vì kỹ thuật `<symbol>+<use>` không đáng tin cậy 100% khi style áp dụng
  qua class kế thừa trong một số trường hợp render.
- **Dấu "+" follow badge**: vẽ bằng 2 thanh CSS pseudo-element
  (`::before`/`::after`, `position:absolute; top/left:50%; transform:
  translate(-50%,-50%)`), KHÔNG dùng ký tự text "+" vì glyph font luôn lệch
  vài pixel so với tâm hình học thật.
- **Post vs Comment khác nhau về layout**:
  - Post (`.thr-row.post-head-row`): `align-items:center`, chỉ avatar + tên/
    giờ nằm trong `.thr-row`; nội dung (`.thr-body`, ảnh, stats) nằm **NGOÀI**
    `.thr-row`, tràn full-width, KHÔNG thụt lề theo avatar.
  - Comment (`.thr-row` thường): `align-items:flex-start` (bắt buộc — nếu
    dùng `center` hoặc thiếu hẳn, flexbox sẽ kéo giãn `.avatar-wrap` theo
    chiều cao nội dung comment, làm dấu "+" bị lệch xuống đáy thay vì đáy
    avatar); toàn bộ nội dung nằm **TRONG** `.thr-main` cùng cấp với
    `.thr-head`, nên bị thụt lề theo avatar.
- **Đường nối reply** (`.thread-group > .connector-line`): dùng kỹ thuật
  `position:absolute; top:[đáy avatar cha]; bottom:0` — line kéo dài hết
  chiều cao còn lại, KHÔNG đo/tính điểm dừng chính xác. Avatar của reply có
  nền solid + `z-index` cao hơn nên tự nhiên che phần line thừa phía sau nó.
  Đây đúng là kỹ thuật Threads thật dùng (xác nhận qua HTML thật họ cung
  cấp), đừng thay bằng cách "đo chiều cao rồi set height" — cách đó không
  đáng tin cậy theo nội dung động.
- **Badge "Tác giả"**: hiện cạnh tên trong `.uname-row`, dùng khi người
  comment/reply là chính tác giả bài gốc.

## 5. Kiến trúc `index.html` (đang phát triển — hiểu kỹ trước khi sửa)

### 5.1. Layout 3 cột

```
┌─────────────┬──────────────────────┬─────────────────────┐
│  SIDEBAR     │   MAIN (preview)      │   SCRIPT-PANEL       │
│  (cài đặt    │   khung 9:16, video   │   (kịch bản = danh   │
│  chung: video│   nền + stack thẻ     │   sách các "step")   │
│  nền, âm     │   Threads đè lên +    │   nút + Post/Comment/│
│  thanh nền)  │   thanh Play          │   Cmt+Reply/Meme/    │
│              │                       │   Âm thanh, mỗi step │
│              │                       │   là 1 thẻ có thể    │
│              │                       │   sửa/xoá/đổi thứ tự │
└─────────────┴──────────────────────┴─────────────────────┘
```

- Sidebar trái: mục 1 "Video nền" và mục 2 "Âm thanh nền" — set nền tảng
  chung cho cả video (video chạy nền + nhạc nền phát cùng lúc khi Play).
- Panel phải: nơi soạn **nội dung động** của video (các step).
- Khung giữa: nơi xem trước kết quả ghép — luôn giữ tỉ lệ 9:16 bằng CSS
  `aspect-ratio: 9/16`.

### 5.2. Kỹ thuật scale UI Threads theo khung preview (`cqw`)

Mock gốc (`threads_ui_mock.html`) thiết kế cho khung cố định **1080px**
(dùng `transform:scale(0.42)` để hiện nhiều mẫu cùng lúc trên màn hình lớn).
`index.html` cần khung preview **co giãn theo kích thước cửa sổ thật** (max-
width 460px trên desktop, nhưng có thể nhỏ hơn trên màn hình bé) — không thể
dùng `px` cố định.

Giải pháp: `.preview-frame { container-type: inline-size }` (CSS Container
Query), rồi mọi kích thước bên trong `.thr-stack`/`.tcard` dùng đơn vị
**`cqw`** (1cqw = 1% chiều rộng của `.preview-frame`). Công thức quy đổi từ
mock:

```
X (px, thiết kế gốc 1080px) → (X / 1080 * 100) cqw
```

Ví dụ: avatar mock là `120px` → `120/1080*100 = 11.11cqw`. **Khi thêm style
mới, luôn tính theo công thức này, không áng chừng số.**

### 5.3. Step Engine — cơ chế lõi

Đây là phần quan trọng nhất, người dùng gọi là "phần khó". Ý tưởng cốt lõi:

**Video là một chuỗi "step" chạy nối tiếp nhau theo thời gian TƯƠNG ĐỐI**,
không phải thời gian tuyệt đối cố định. Mỗi step tự động bắt đầu ngay sau khi
step trước đó **kết thúc + gap của nó**. Nhờ vậy, người dùng chèn/xoá/kéo đổi
thứ tự step thoải mái — mọi mốc thời gian tự tính lại, không bao giờ phải
sửa số tay.

```js
// computeTimeline() — file index.html, ~dòng 1103
let t = 0;
for (const s of steps) {
  s._start = t;
  s._end = t + s.duration;   // Phase 1: duration là số giả, Phase sau = độ dài TTS thật
  t = s._end + (s.gap || 0);
}
```

5 loại step (`TYPE_LABEL`, `DEFAULT_DUR`):
- `post` — bài đăng gốc, dùng layout `post-head-row`.
- `comment` — 1 bình luận đơn.
- `reply` — 1 khối gồm CẢ comment gốc + reply (không tách 2 step riêng, vì
  người dùng nói rõ "comment reply là chung thôi" — 1 form nhập cả 2 phần,
  submit ra 1 step duy nhất chứa `.thread-group` với đường nối).
- `meme` — overlay video/gif/ảnh phủ giữa khung, tự biến mất khi hết
  `duration` của nó.
- `audio` — chỉ phát âm thanh hiệu ứng, không có hình (Phase 1 chưa xử lý
  phát audio thật, chỉ có trong timeline).

### 5.4. Cơ chế "gap" — ĐIỂM DỄ HIỂU SAI, đọc kỹ

**Sai lầm ban đầu của AI**: hiểu gap là "khoảng nghỉ giữa 2 step nhưng nội
dung step trước vẫn đứng yên trên màn hình". SAI.

**Đúng theo người dùng xác nhận**: gap là khoảng **im lặng hoàn toàn** — khi
một step (post/comment/reply) đọc xong TTS (chạm mốc `_end`), nội dung của nó
**biến mất khỏi màn hình ngay lập tức** (chỉ còn video nền chạy, không có
card nào). Post/comment mới chỉ xuất hiện khi step kế tiếp bắt đầu
(`_start`). Đây là khoảng lặng có chủ đích để người xem "thở", tránh cảm giác
nội dung nhảy dồn dập.

Hệ quả logic (xem `computeSceneAt()`, file `index.html` ~dòng 1136):

- Một step chỉ "sống" (hiển thị) trong đúng cửa sổ `[_start, _end)` của
  chính nó.
- Nếu 2 step liền nhau có **gap = 0** (dính sát, không có khoảng lặng), nội
  dung của chúng **cộng dồn chồng lên nhau** trên màn hình (giữ đúng hành vi
  "stack" ban đầu người dùng muốn — nhiều post/comment xuất hiện dồn dập không
  bị mất nhau).
- Nếu có **gap > 0**, chuỗi cộng dồn bị "cắt đứt" — bước sang step kế, màn
  hình bắt đầu lại từ đầu (rỗng, rồi hiện step mới).
- Cờ `clearBefore` (tick "xoá nội dung cũ") vẫn hoạt động độc lập với gap:
  dù gap = 0 (nội dung lẽ ra cộng dồn), nếu step có `clearBefore = true` thì
  vẫn xoá sạch chồng cũ trước khi thêm mình vào.

Thuật toán tính "stack tại thời điểm t": tìm step đang sống tại `t` (nếu có),
rồi duyệt NGƯỢC về các step liền trước, cộng dồn miễn là chuỗi giữa chúng
**liên tục không đứt đoạn** (`prev._end + prev.gap === s._start`, sai số
`1e-6`) và không gặp `clearBefore` chặn giữa đường.

**Auto-scroll**: khi stack chồng nhiều thẻ vượt quá chiều cao khung 9:16,
`autoScrollStack()` tự dịch cả `.thr-stack` lên (CSS `transform: translateY`)
để thẻ mới nhất luôn nằm trong khung nhìn (~90% chiều cao khung). Đây là
quyết định người dùng chọn qua `AskUserQuestion` (thay vì "giữ giữa màn hình"
hay "tự thu nhỏ").

### 5.5. Render — không rebuild DOM mỗi frame

`renderAtTime(t)` được gọi liên tục khi Play (qua `requestAnimationFrame`).
Để tránh giật/lag, nó **chỉ ghi lại `innerHTML` khi nội dung thực sự đổi**
(so sánh `key` = danh sách id các step đang hiển thị, cache vào biến module-
scope `_lastStackKey`/`_lastMemeKey`). Khi sửa hàm này, luôn giữ nguyên tắc
"không so sánh được thì đừng update DOM" để animation CSS (`tcard-in`) không
bị restart liên tục mỗi frame.

`renderIdleScene()` — dùng khi KHÔNG phát (đang chỉnh sửa step) để người
dùng vẫn thấy được nội dung tổng quan. Vì nội dung giờ biến mất trong gap,
không thể render đúng lúc `totalDuration()` (có thể đang rơi vào gap cuối,
màn hình sẽ trống) — thay vào đó lấy mốc **giữa `_start` và `_end` của step
cuối cùng** để đảm bảo luôn thấy nội dung.

### 5.6. Modal soạn step — cấu trúc field

Mỗi step post/comment/reply có bộ field:
- `uname`, `text` (bắt buộc — chặn submit nếu rỗng).
- `isAuthor` (comment/reply) — hiện badge "Tác giả".
- Ảnh đính kèm tuỳ chọn (`imgFile`/`imgUrl`, dùng `URL.createObjectURL`).
- 4 số liệu tương tác dạng text tự do (không phải number) — vì cần hiển thị
  kiểu "1,2K", "99,9K" giống Threads thật, không phải số nguyên thuần:
  `stats.likes/comments/reposts/shares`.
- `stats.timeLabel` — text tự do (vd "21 giờ", "9 giờ", "2 ngày") thay cho
  "vừa xong" cứng.

Với `reply`, có **2 bộ field riêng biệt** (`m-*` cho comment gốc, `m2-*` cho
reply) vì đây là 1 step chứa 2 "nhân vật".

`openStepModal(type, stepId)` — `stepId = null` nghĩa là thêm mới, khác
`null` nghĩa sửa step đã có (field được điền lại từ dữ liệu cũ).

### 5.7. TTS thật — kiến trúc & quyết định

**Backend**: `threads/tts_server.py` — một FastAPI server **riêng biệt**,
đứng độc lập với `content_server.py`/`translate_server.py`/`auth_server.py`
(cổng `8007`, các server kia đang chiếm `8004`/`8005`/`8006`). File này
**import `translate_video.py`** (`capcut_tts_batch`, `CAPCUT_VOICES_VI`) từ
gốc repo để tái dùng phần hạ tầng gọi CapCut TTS đã được kiểm chứng ổn định
(ký RSA, xoay `device_id` khi bị "shark block", danh sách 11 giọng tiếng
Việt) — đây là ngoại lệ **có chủ đích** cho quy tắc "không đụng file gốc"
ở mục 2: `tts_server.py` chỉ *đọc* (import hàm), không sửa file gốc, và là
file mới hoàn toàn nằm trong `threads/`. Người dùng đã chọn CapCut TTS thay
vì edge-tts (dù edge-tts free/không rủi ro hơn) vì chất lượng giọng đọc tự
nhiên hơn — chấp nhận đánh đổi rủi ro bị block (giảm thiểu bằng cơ chế retry
đổi `device_id` tới 5 lần, xem `generate_tts()` trong `tts_server.py`).

Chạy server: `python3 threads/tts_server.py` (cần `.env` ở gốc repo có
`CAPCUT_DEVICE_ID`). Endpoint:
- `GET /voices` — trả danh sách giọng (label + voice_type + resource_id).
- `POST /tts {text, voice_type, resource_id, rate}` — trả
  `{audio_url, duration}`, file MP3 lưu ở `threads/tts_audio/` (đã có trong
  `.gitignore` chung qua rule `*.mp3` toàn repo, không cần thêm rule riêng).
- `GET /audio/{filename}` — serve file MP3 đã tạo.

**Frontend**: `submitStepModal()` (file `index.html`, ~dòng 1074) giờ là
`async function`. Khi type là `post`/`comment`/`reply`, trước khi push/sửa
step, nó gọi `fetchTts(text)` (~dòng 918) — POST tới `/tts`, chờ kết quả rồi
mới thêm step vào `steps[]` với `duration` = độ dài audio thật (không còn
dùng `DEFAULT_DUR` giả cho 3 loại này nữa, `DEFAULT_DUR` giờ chỉ áp dụng cho
`meme`/`audio`). Nút submit bị `disable` + hiện dòng trạng thái
`#step-modal-tts-status` trong lúc chờ; nếu TTS lỗi (server tắt, mất mạng),
`alert()` báo lỗi rõ ràng và **step KHÔNG được thêm vào kịch bản** — người
dùng phải bật `tts_server.py` rồi thử lại, tránh cảnh step "treo" với
duration sai mà không ai biết.

**`reply` có 2 audio riêng biệt** (comment gốc + reply, gọi TTS 2 lần tuần
tự) — lưu `audioUrl`/`ttsDuration` (khối 1) và `audio2Url`/`ttsDuration2`
(khối 2). `duration` của step = tổng 2 khối (đọc nối tiếp, không chồng
tiếng). Việc quyết định "audio nào đang phát tại thời điểm t" nằm trong
`ttsClipAt(t)` (~dòng 1341): nếu `t` còn trong nửa đầu cửa sổ step (dưới
`ttsDuration`) → phát khối 1 với offset = `t - start`; qua khỏi mốc đó →
phát khối 2 với offset = `t - start - ttsDuration`. Ngoài cửa sổ sống
`[_start, _end)` của step (tức đang trong "gap") → trả `null`, không audio
nào phát — nhất quán với cơ chế gap ở mục 5.4 (nội dung + tiếng đọc cùng
biến mất trong khoảng lặng).

**Phát audio khi Play**: 1 thẻ `<audio id="tts-audio">` riêng (không dùng
chung với `preview-audio` — thẻ đó dành cho nhạc nền). `syncTtsAudio(t)`
(~dòng 1362, gọi từ trong `renderAtTime()` mỗi frame khi đang `playing`) so
`clipKey` (`stepId:part`) với lần trước — chỉ đổi `src`/seek/play khi
chuyển sang đoạn audio khác, tránh restart liên tục giống nguyên tắc ở mục
5.5. Nếu lệch quá 0.35s so với offset kỳ vọng (vd người dùng tua timeline)
thì chỉnh lại `currentTime` cho khớp. `stopPlay()` phải `pause()` cả
`tts-audio` và reset `_lastTtsClipKey = null`, nếu không audio cũ sẽ tiếp
tục phát ngầm sau khi bấm Dừng.

**Duration không còn sửa tay được** cho step có `audioUrl` (input trong
step-card bị `disabled`, kèm icon 🔊 và tooltip giải thích) — muốn đổi
duration phải sửa lại nội dung text (bấm ✎) để tạo lại TTS, tránh tình
trạng duration hiển thị không khớp với độ dài audio thật sẽ phát.

**Ô "Nội dung hiển thị" tách riêng khỏi ô "Nội dung đọc (TTS)"** — người
dùng phản hồi rằng text hiển thị trên thẻ Threads (có thể có hashtag, emoji,
viết tắt) không phải lúc nào cũng là text muốn nghe đọc thành tiếng. Mỗi ô
`#m-text`/`#m-text2` (hiển thị) giờ đi kèm 1 ô `#m-tts-text`/`#m2-tts-text`
(đọc) ngay bên dưới, tạo bởi `ttsFieldHtml(prefix, currentTtsText)` (~dòng
969). Hành vi đồng bộ (đã hỏi và chốt với người dùng qua `AskUserQuestion`):
- **Mặc định tự đồng bộ real-time** theo ô hiển thị — gõ ô hiển thị thì ô
  đọc tự cập nhật theo, không cần gõ 2 lần cho trường hợp đơn giản (text
  hiển thị = text muốn đọc, đa số trường hợp).
- **Ngay khi user tự gõ vào ô đọc**, cờ `ttsFieldDirty[prefix]` bật lên
  (`bindTtsAutoSync()` ~dòng 986 lắng nghe `input` trên ô hiển thị, chỉ copy
  qua ô đọc nếu `!ttsFieldDirty[prefix]`) — từ đó ngưng tự đồng bộ, ô đọc
  thuộc quyền kiểm soát của user, gõ thêm vào ô hiển thị không ghi đè ô đọc
  nữa.
- Cờ dirty **reset mỗi lần mở modal** (`openStepModal()`), nhưng nếu đang
  sửa 1 step đã lưu mà `ttsText` khác `text` (chứng tỏ lần trước user đã tự
  sửa ô đọc), cờ dirty được khôi phục lại đúng trạng thái — không tự ý đồng
  bộ đè lên bản đọc đã tối ưu tay của user chỉ vì mở modal sửa lại.
- `readTtsText(prefix, textFieldId)`: nếu ô đọc có giá trị → dùng giá trị đó
  gọi TTS; nếu để trống → fallback dùng nguyên text ô hiển thị (cho phép để
  trống mà không lỗi). **Lưu ý bẫy đã gặp**: id ô hiển thị KHÔNG theo cùng
  quy tắc `prefix + '-text'` như ô tts-text/voice — khối 1 của reply là
  `m-text` nhưng khối 2 là `m-text2` (không phải `m2-text`, vì lịch sử code
  cũ đặt tên vậy — xem mục 5.6). Vì vậy `readTtsText`/`bindTtsAutoSync` phải
  nhận **id ô hiển thị làm tham số tường minh** (`textFieldId`) thay vì tự
  suy luận từ `prefix`, nếu không sẽ lỗi `Cannot read properties of null` khi
  submit reply lúc ô đọc để trống (bug này đã xảy ra thật, phát hiện qua CDP
  test rồi sửa — nhắc để không lặp lại nếu thêm field tương tự sau này).
- `submitStepModal()` lưu cả `ttsText`/`ttsText2` vào step (không chỉ dùng
  tạm lúc gọi API) để khi sửa lại step, ô đọc hiện đúng giá trị đã lưu lần
  trước thay vì bị reset về giống ô hiển thị.

**Chọn giọng đọc riêng theo từng step** — ngoài giọng mặc định chung ở mục
3 sidebar (`#tts-voice`), mỗi post/comment/reply có ô `voiceFieldHtml(prefix,
currentIdx)` riêng (`#m-voice`, `#m2-voice` cho reply) để đổi giọng cho từng
nhân vật (vd bài gốc giọng nữ, người reply giọng nam) — người dùng chọn qua
`AskUserQuestion`, ưu tiên linh hoạt hơn 1 giọng chung cho cả video. Mặc định
ô chọn giọng trong modal lấy theo giọng đang chọn ở sidebar
(`document.getElementById('tts-voice').value`), nhưng lưu riêng `voiceIdx`
(khối 1) / `voiceIdx2` (khối 2, reply) vào step — đổi giọng sidebar sau đó
KHÔNG ảnh hưởng step đã tạo trước, chỉ áp dụng cho step mới. `selectedVoice(idx)`
và `fetchTts(text, voiceIdx)` nhận thêm tham số idx tuỳ chọn — không truyền
thì fallback dùng giọng sidebar (giữ tương thích chỗ nào gọi cũ). Tên giọng
đang dùng hiện dưới mỗi step-card (`.sc-voice`, hàm `voiceLabelHtml(s)`) để
dễ nhận biết, reply hiện dạng `giọng 1 ↳ giọng 2`. Nếu `ttsVoices` chưa tải
xong (server chưa kết nối) khi mở modal, ô chọn giọng hiện cảnh báo đỏ thay
vì dropdown rỗng.

### 5.8. Step `audio` — chạy SONG SONG, phát tiếng thật

Trước đây step `audio` chỉ tồn tại trong timeline (chiếm chỗ như post/
comment/meme) nhưng chưa phát tiếng thật khi Play — người dùng phản hồi 2
việc cần sửa: (1) làm nó phát tiếng thật, (2) đổi cơ chế thành **chạy song
song** với nội dung khác (post/comment/hình ảnh) thay vì chiếm thời gian
riêng của video. Đã hỏi lại qua `AskUserQuestion` và chốt: chỉ `audio` chạy
song song (không áp dụng cho `meme` — meme là overlay hình ảnh/video phủ
khung, vẫn giữ tuần tự vì khó "đè" lên content khác về mặt hình ảnh); step
audio song song tự bắt đầu (`_start`) trùng với `_start` của step đứng ngay
**trước nó trong danh sách** — nghĩa là nó "đè" lên đúng lúc step đó bắt đầu,
không phải đợi step đó phát xong.

**Timeline 2 lượt** (`computeTimeline()`, ~dòng 1327): lượt 1 tính `_start`/
`_end` cho **track chính** (post/comment/reply/meme) như cũ — biến `t` chạy
tuần tự **bỏ qua hoàn toàn** step `audio` (không cộng `duration`/`gap` của nó
vào `t`). Lượt 2 duyệt lại toàn bộ `steps[]`, với mỗi step `audio` gặp phải,
gán `_start` = `_start` của step track-chính **gần nhất đứng trước nó trong
mảng** (biến `lastMainStart`, cập nhật liên tục khi duyệt qua step chính; nếu
audio đứng trước mọi step chính — vd audio là step đầu tiên — `lastMainStart`
mặc định là `0`). Nhờ vậy chèn/xoá step chính ở giữa vẫn tự tính lại đúng vị
trí audio bám theo (đã test: xoá step chính mà audio đang bám vào, audio tự
"rơi" xuống bám theo step chính đứng trước nó tiếp theo, hoặc về 0 nếu không
còn step chính nào trước).

**Ảnh hưởng tới `computeSceneAt()`** (~dòng 1465): vì step `audio` giờ xen
giữa mảng `steps[]` mà **không thuộc track chính**, mọi phép tính duyệt/tìm
kiếm chuỗi liên tục (tìm step đang sống, cộng dồn stack lùi qua `steps[i-1]`)
phải lọc `mainSteps = steps.filter(s => s.type !== 'audio')` trước, rồi mới
duyệt trên mảng đã lọc đó — nếu không, step audio xen giữa sẽ bị coi nhầm là
"step liền trước" trong chuỗi gap/stack và phá vỡ cơ chế gap (mục 5.4).
Tương tự, `renderIdleScene()` (mốc xem tĩnh khi không Play) phải lấy step
**track-chính** cuối cùng, không lấy `steps[steps.length-1]` thẳng (có thể là
audio, không đại diện đúng "toàn cảnh cuối").

**Phát tiếng thật khi Play**: khác với TTS (luôn đúng 1 clip đang đọc tại 1
thời điểm — dùng 1 `<audio id="tts-audio">` cố định), audio hiệu ứng có thể
**nhiều clip chồng lên nhau cùng lúc** (vd 2 step audio liên tiếp trong danh
sách, không có step chính nào xen giữa → cả 2 cùng bám 1 `_start`, phát đè
lên nhau thật). Vì vậy dùng cơ chế khác: `sfxAudioEls` — 1 `Map<stepId,
HTMLAudioElement>`, tạo **động** bằng `new Audio(s.mediaUrl)` khi step audio
đó bắt đầu "sống" (`syncSfxAudio(t)`, gọi từ `renderAtTime()` mỗi frame khi
đang `playing`, y hệt cách gọi `syncTtsAudio`), và `.pause()` + xoá khỏi Map
ngay khi step đó ra khỏi cửa sổ `[_start, _end)`. Không dùng chung cơ chế
"chỉ update khi key đổi" như `_lastStackKey`/`_lastTtsClipKey` (vì có thể
nhiều clip sống cùng lúc, không phải 1 giá trị đơn) — thay vào đó mỗi frame
tính lại tập `livingIds`, đối chiếu với `sfxAudioEls` hiện có để biết cái nào
cần tạo mới / cái nào cần dọn. `stopPlay()` và `deleteStep()` đều phải gọi
dọn dẹp (`stopAllSfxAudio()` / xoá entry theo id tương ứng) để tránh audio
hiệu ứng phát ngầm sau khi dừng Play hoặc xoá step khỏi kịch bản.

### 5.9. Ảnh/media đính kèm — đổi từ upload file sang URL / đường dẫn file

Ban đầu ảnh post/comment và file meme/audio dùng `<input type="file">` +
`URL.createObjectURL()` (blob URL). Người dùng yêu cầu đổi vì 2 lý do: (1)
đa số ảnh test lấy từ URL có sẵn, không cần upload; (2) blob URL chỉ sống
trong session hiện tại (mất khi reload trang), là rào cản chính khiến
**không thể export/import JSON được** (mục 8) — File object và blob: URL
không serialize được vào JSON.

- **Ảnh post/comment** (`imgFieldHtml()`): đổi thành ô nhập **URL ảnh**
  (`<input type="text">`) thay vì file picker. Preview bằng `<img src=URL>`,
  bắt `onerror` để báo "Không tải được ảnh từ URL này" nếu URL sai/ảnh
  không tồn tại. Lưu thẳng `imgUrl`/`img2Url` = URL string vào step — không
  còn `imgFile`/`img2File` (File object) hay `modalImgDataUrl`/
  `modalImg2DataUrl` (state tạm quản lý blob) nữa, đã xoá sạch khỏi code.
  Vì là URL public, việc ghép/xuất video sau này (FFmpeg hoặc Python) vẫn
  tải ảnh về bình thường — không có vấn đề gì cho pipeline export.
- **Avatar post/comment/reply** (`avatarFieldHtml()`, ~dòng 1097): cùng
  nguyên tắc như ảnh đính kèm — ô nhập URL, để trống thì fallback về hành vi
  cũ (chữ cái đầu tên tô màu `hsl` theo `hueOf(uname)`). `avatarHtml(uname,
  hue, sm, avatarUrl)` nhận thêm tham số `avatarUrl`; nếu có, render
  `<img object-fit:cover>` phủ kín `.avatar` (đã thêm `overflow:hidden` vào
  CSS `.avatar` — nếu không, ảnh vuông sẽ tràn góc ra ngoài viền tròn khi
  `border-radius:50%` chỉ bo tròn phần tử cha mà không cắt nội dung con);
  `onerror` tự thay bằng `<div>` chữ cái đầu tên nếu URL lỗi, không vỡ layout.
  Reply lưu riêng `avatarUrl` (khối 1 — comment gốc) và `avatarUrl2` (khối 2
  — reply), giống hệt cách `imgUrl`/`img2Url` đã tách theo 2 khối.
- **File meme (video/gif/ảnh) và audio hiệu ứng** (`filePathFieldHtml()`):
  đổi thành ô nhập **đường dẫn file tuyệt đối trên máy** (vd
  `/Users/ten-ban/videos/meme1.mp4`) thay vì file picker. Lý do người dùng
  chọn cách này (đã hỏi qua `AskUserQuestion`, không dùng cách chạy thêm 1
  static file server): đơn giản, không cần chạy thêm server, chỉ cần đổi
  cách mở app.

  **Ràng buộc quan trọng — PHẢI nói rõ với người dùng**: trình duyệt không
  cho JavaScript đọc trực tiếp đường dẫn tuyệt đối trên đĩa (giới hạn bảo
  mật chung của mọi trang web). Giải pháp là quy đổi qua `file://` URL
  (`toFileUrl(path)`, ~dòng 1020 — encode từng segment path qua
  `encodeURIComponent`, giữ nguyên `/` phân cách; nhận diện sẵn nếu path đã
  là `http(s)://` hoặc `file://` thì giữ nguyên không convert lại) rồi gán
  vào `<video src=...>`/`<img src=...>`/`new Audio(...)`. **Nhưng
  `file://` chỉ thật sự load được nếu `index.html` được MỞ TRỰC TIẾP bằng
  `file://` (double-click hoặc kéo vào trình duyệt) — KHÔNG hoạt động nếu
  app đang chạy qua 1 http server** (kể cả `python3 -m http.server`, vì đó
  là mixed-content: trang từ origin http không được phép load tài nguyên
  file://). Môi trường test CDP ở đây luôn phải serve qua http server (để
  tránh vướng CORS khi gọi TTS server — mục 5.7), nên **không thể test thật
  việc phát video/audio qua `file://` bằng CDP** — chỉ test được phần logic
  JS (lưu đúng `mediaPath`/chuyển đổi đúng `mediaUrl`/hiện lại đúng khi sửa
  step), còn việc phát hình/tiếng thật qua `file://` cần người dùng tự xác
  nhận bằng cách mở `index.html` trực tiếp (không qua server nào).
  - Step data lưu cả 2: `mediaPath` (đường dẫn gốc, hiển thị lại trong ô
    input khi sửa step) và `mediaUrl` (đã convert sang `file://`, dùng để
    gán vào thẻ `<video>`/`<img>`/`Audio()`). `mediaIsVideo` xác định qua
    đuôi file (regex `\.(mp4|mov|webm|m4v)$`) — thay cho cách cũ dựa vào
    `File.type` (không còn File object để đọc `.type` nữa).
- **Avatar** cũng dùng URL (`avatarFieldHtml()`, xem chi tiết ở đoạn avatar
  phía trên trong mục này).

### Lưu ý quan trọng: nhiều ảnh mạng xã hội (Instagram/Facebook CDN) KHÔNG
### thể load qua `<img src=URL>` dù URL đúng — không phải bug, là chặn CORP

Người dùng thử dán link avatar Instagram thật (dạng
`https://instagram.*.fna.fbcdn.net/...`) và ảnh không hiện. Đã điều tra kỹ
bằng `curl -I` + test trực tiếp qua CDP (`new Image()` với/không
`referrerPolicy`) trước khi kết luận:

- **Không phải do Referer header** — ban đầu nghi ngờ hotlink-block dựa trên
  Referer (cách chặn phổ biến), đã thử thêm `referrerpolicy="no-referrer"`
  vào mọi thẻ `<img>` liên quan tới URL người dùng nhập (đã giữ lại trong
  code vì vô hại, có thể giúp các CDN khác chặn theo kiểu Referer đơn giản
  hơn) — nhưng test lại vẫn lỗi y hệt cả khi có và không có thuộc tính này.
- **Nguyên nhân thật**: response header `Cross-Origin-Resource-Policy:
  same-origin` (xác nhận qua `curl -I` — `curl` tải được ảnh 200 OK vì không
  áp dụng CORP, nhưng trình duyệt thì có). Đây là cơ chế bảo mật trình duyệt
  chặn **cứng** việc bất kỳ trang khác origin nào load resource này qua thẻ
  `<img>`, hoàn toàn không liên quan tới Referer — không có cách nào "vượt"
  từ phía JavaScript/CSS thuần chạy trong trình duyệt, đây là giới hạn nền
  tảng (browser platform), không phải thứ sửa được ở tầng ứng dụng.
- Đã trình bày rõ 2 hướng khắc phục thật (dùng đường dẫn `file://` local
  giống meme, hoặc thêm server proxy tải ảnh hộ) qua `AskUserQuestion`, nhưng
  người dùng chọn hướng khác hẳn: bỏ qua vụ hotlink, chuyển sang tính năng
  che mờ tên/avatar để đỡ phải tìm ảnh/tên thật (xem mục 5.10) — nên **chưa
  cần implement** cách khắc phục CORP này, chỉ ghi lại phát hiện để không ai
  mất công thử lại `referrerpolicy` cho lớp vấn đề này trong tương lai.

### 5.10. Làm mờ tên & avatar trong preview (khỏi cần nghĩ tên/tìm ảnh thật)

Xuất phát từ vụ ảnh Instagram bị chặn CORP ở trên — thay vì cố tìm nguồn ảnh
"an toàn", người dùng muốn 1 tính năng giúp **không cần nghĩ tên thật/tìm
ảnh thật** khi soạn kịch bản: làm mờ (CSS blur — không phải che ô đen/trắng)
tên và avatar ngay trong preview.

- **Phạm vi**: chỉ `.uname` (tên hiển thị) và `.avatar` (avatar tròn — cả
  trường hợp chữ cái đầu tên lẫn ảnh URL thật). **KHÔNG** áp dụng cho ảnh
  đính kèm trong thân bài (`.thr-img-wrap img`) — đã hỏi rõ và người dùng
  xác nhận chỉ cần mờ phần định danh (tên/avatar), ảnh minh hoạ nội dung vẫn
  cần rõ để xem preview có hợp lý không.
- **Cơ chế**: thuần CSS, không đụng vào step data. Class `blur-identity`
  toggle trên `#preview-frame` (không phải trên từng `.tcard` — để 1 lần
  toggle áp dụng cho toàn bộ stack đang render). CSS:
  `#preview-frame.blur-identity .uname { filter: blur(5px) }` và tương tự
  cho `.avatar` (`blur(6px)`, đậm hơn 1 chút vì avatar là vùng ảnh/màu đặc,
  cần mờ hơn để không đoán được nội dung so với chữ mảnh của `.uname`).
  Mức blur **cố định**, không có ô chỉnh — theo yêu cầu người dùng "cố định
  1 mức blur vừa phải", giữ đơn giản.
- **Toggle**: checkbox `#blur-identity-toggle` ở sidebar trái (dưới mục 3
  TTS), **mặc định BẬT sẵn** (gọi `toggleBlurIdentity()` ngay khi script
  load, không đợi user tự bật) — đúng mục đích ban đầu là giúp đỡ nghĩ tên
  khi soạn. Tắt đi khi cần xem/xuất bản thật (rõ tên/avatar) — đã hỏi và
  chốt qua `AskUserQuestion`, người dùng chọn có công tắc thay vì blur cứng
  không tắt được.
- Test qua CDP: xác nhận `getComputedStyle(...).filter` đúng giá trị
  `blur(5px)`/`blur(6px)` khi bật, về `none` khi tắt, bật lại đúng lại, và
  ảnh đính kèm thân bài không bị ảnh hưởng gì (`filter: none` xuyên suốt).

**Cập nhật quan trọng — chỉ CSS blur là KHÔNG ĐỦ, phải thay hẳn data hiển
thị**: người dùng chỉ ra rằng blur đơn thuần vẫn để lộ danh tính qua 2 kênh:
(1) avatar tô màu nền bằng `hsl(hueOf(uname), ...)` — cùng 1 tên thật luôn ra
đúng 1 màu, lặp lại giữa các step vẫn nhận diện được qua màu dù đã blur mờ
chữ; (2) tên dù blur vẫn lộ **độ dài chữ** (số ký tự/từ), người xem tinh mắt
đoán được gần đúng tên thật qua hình dạng vệt mờ. Giải pháp: khi bật blur,
**thay hẳn** tên/avatar hiển thị bằng 1 cặp random từ 2 danh sách cố định
(không liên quan gì tới data thật), RỒI MỚI áp CSS blur lên trên — vậy màu
sắc/độ dài không còn tương quan gì với thông tin thật nữa.

- `RANDOM_IDENTITY_NAMES` (~dòng 1594): 7 tên do người dùng cung cấp sẵn
  (`mdynndeii`, `tuyetnhung_2807`, `_vanhb.bp_`, `jinxthn`, `nt_hongf`,
  `yeu_e.m36`, `kditrh_`).
- `RANDOM_IDENTITY_AVATARS`: 9 ảnh avatar thật (ảnh mạng xã hội sưu tầm) đặt
  ở `threads/avatarthreads/` — người dùng copy vào, có 1 file trùng lặp
  hoàn toàn (`... (1).jpeg` giống hệt `md5` với file gốc) đã bị xoá, còn lại
  9 file duy nhất. Dùng **đường dẫn tương đối** `avatarthreads/ten-file.jpg`
  (không phải `file://` tuyệt đối như mục 5.9) — vì `avatarthreads/` nằm
  cùng thư mục với `index.html`, đường dẫn tương đối tự resolve đúng bất kể
  máy nào mở app, không phụ thuộc đường dẫn tuyệt đối riêng của từng máy.
- `seedHash(str)`: hash chuỗi đơn giản (nhân 31 cộng dồn mã ký tự) → số
  nguyên không âm, dùng làm seed chọn phần tử trong 2 mảng trên. Seed dùng
  `stepId + ':m'` (khối 1) hoặc `stepId + ':m2'` (khối 2 của reply) — đảm
  bảo mỗi "nhân vật" trong mỗi step có 1 cặp tên/avatar giả **cố định**
  riêng, không đổi lung tung mỗi lần gọi `renderAtTime()`/`renderIdleScene()`
  (nếu random thật mỗi lần render, tên/avatar sẽ nhấp nháy đổi liên tục khi
  Play — sai hoàn toàn ý đồ).
- `displayIdentity(seed, realUname, realAvatarUrl)`: hàm trung tâm — nếu
  `blurIdentityOn()` false, trả thẳng data thật (không đổi gì); nếu true,
  bỏ qua data thật hoàn toàn, trả về `{uname, avatarUrl}` giả tính từ seed.
  `buildCardHtml()` gọi hàm này cho từng "nhân vật" (post/comment: 1 lần,
  reply: 2 lần — riêng biệt cho khối 1 và khối 2) **trước khi** truyền vào
  `buildPostHtml()`/`buildCommentRow()` — nên `hueOf(uname)` (màu nền avatar
  fallback chữ cái khi post không có avatar URL) bên trong các hàm build
  cũng tự động tính theo tên GIẢ (vì nhận `uname` đã là tên giả từ tham số),
  không rò rỉ màu theo tên thật. **`s.hue`/`hue2` (màu nền avatar riêng của
  comment/reply) không đổi** — chúng vốn đã là số random độc lập gán 1 lần
  lúc tạo step (`Math.floor(Math.random()*360)`), không tính từ tên nên
  không phải kênh rò rỉ, không cần xử lý gì thêm.
- **Ép re-render khi toggle**: `renderAtTime()`/`renderIdleScene()` chỉ
  rebuild `innerHTML` khi "key" (danh sách step id đang hiển thị, xem mục
  5.5) thay đổi — bật/tắt blur không đổi bộ step id nào, nên nếu không xử
  lý gì thêm, tên/avatar cũ sẽ đứng yên trên màn hình cho tới khi chuyển
  sang step khác. `toggleBlurIdentity()` phải chủ động set `_lastStackKey =
  null` rồi gọi `renderIdleScene()` (khi không `playing`) để ép vẽ lại ngay.
  **Bẫy khi sửa phần này**: `toggleBlurIdentity()` được gọi ngay khi
  `<script>` bắt đầu chạy (để áp trạng thái mặc định `checked` lúc tải
  trang) — nhưng lúc đó các biến `let steps`/`let playing`/hàm
  `renderIdleScene` khai báo ở **phía sau** trong cùng file **chưa được khởi
  tạo** (temporal dead zone của `let`). Gọi `renderIdleScene()` hay đọc
  `steps` lúc này sẽ ném `ReferenceError: Cannot access '...' before
  initialization` — kể cả dùng `typeof steps === 'undefined'` để né cũng
  KHÔNG an toàn (`typeof` chỉ né được biến chưa từng khai báo, không né được
  biến `let` đang trong TDZ, đã verify bằng Node). Giải pháp: cờ boolean
  riêng `_stepEngineReady` (khai báo ngay trên `toggleBlurIdentity`, mặc
  định `false`), chỉ bật `true` ở khối "Khởi tạo" cuối file (sau
  `renderStepList()`) — `toggleBlurIdentity()` kiểm tra cờ này trước, nếu
  chưa sẵn sàng thì chỉ set class CSS rồi return, bỏ qua phần render.
- Test qua CDP: tên/avatar hiển thị hoàn toàn khác data thật khi bật blur,
  avatar trỏ đúng `avatarthreads/...`, ổn định qua nhiều lần render liên
  tiếp (không nhấp nháy đổi), tắt blur hiện đúng tên thật trở lại, bật lại
  ra đúng tên giả cũ (seed ổn định), và xác nhận `step.uname`/`step.avatarUrl`
  gốc trong data hoàn toàn không bị ghi đè — chỉ lớp hiển thị thay đổi.

**Mở rộng: thời gian đăng (`timeLabel`) cũng random khi bật blur** — người
dùng muốn khỏi phải nghĩ số giờ/ngày hợp lý mỗi lần soạn step. Khác với tên/
avatar (cần CSS blur vì bản thân là thứ có "hình dạng" đọc/nhận diện được
ngay cả khi mờ), thời gian đăng KHÔNG cần blur mờ — chỉ cần thay bằng 1 giá
trị random hợp lý khác, vẫn đọc rõ bình thường (đã hỏi & chốt qua
`AskUserQuestion`). `RANDOM_IDENTITY_TIMES` (~dòng 1620): mảng mốc thời gian
thường gặp (`1 giờ`...`21 giờ`, `1 ngày`, `2 ngày`). `displayIdentity()` mở
rộng thêm tham số `realTimeLabel`, trả thêm field `timeLabel` trong kết quả
— seed dùng chung với tên/avatar (`stepId + ':m'`/`':m2'`) nên 1 "nhân vật"
có bộ 3 (tên giả, avatar giả, giờ giả) ổn định cùng lúc, không cần seed
riêng. `buildCardHtml()` giờ truyền `realTimeLabel` vào `displayIdentity()`
rồi dùng `id1.timeLabel`/`id2.timeLabel` (thay vì đọc thẳng
`s.stats.timeLabel`) khi gọi `buildPostHtml()`/`buildCommentRow()`.

### Sự cố tưởng là bug nhưng thực ra không phải — Live Server auto-reload

Người dùng báo "bấm thêm post là bị mất" — điều tra kỹ bằng CDP (form đơn
giản, avatar Instagram bị chặn CORP, theo dõi `steps.length` liên tục
8-10 giây sau submit bằng cả gọi hàm trực tiếp lẫn click chuột/gõ phím
thật) đều **không tái hiện được**, step luôn ổn định. Cuối cùng xác định:
người dùng đang mở app qua **Live Server** (VSCode extension, port 5501),
extension này tự động reload TOÀN BỘ trang mỗi khi phát hiện `index.html`
thay đổi trên đĩa — và trùng đúng lúc AI đang liên tục sửa/lưu file này
trong cùng session để implement các tính năng mới. Người dùng test song
song lúc AI đang lưu file → trang tự reload giữa chừng → mất sạch `steps[]`
trong bộ nhớ JS (chưa kịp lưu ra đâu — chưa có tính năng auto-save). Đã xác
nhận bằng cách người dùng copy path dán trực tiếp vào trình duyệt (không
qua Live Server) — hết hẳn hiện tượng.

**Bài học cho phiên làm việc sau**: nếu người dùng báo "vừa thêm step xong
tự nhiên biến mất" hoặc "trang tự reload", **luôn hỏi trước** có đang dùng
Live Server/dev server tự động reload không, và có đang test SONG SONG lúc
AI đang sửa file không — trước khi đi sâu điều tra bug logic. Không phải
lỗi code, là tác dụng phụ của việc vừa sửa file vừa test cùng lúc qua tool
tự động reload.

### 5.11. Bỏ hẳn ô nhập tên/avatar/thời gian đăng — luôn random tự động

Sau khi loại trừ nghi vấn TTS/reload là nguyên nhân lỗi (thêm hẳn checkbox
`#tts-disabled-toggle` ở mục 3 sidebar để debug — tắt hoàn toàn việc gọi
TTS server, dùng `DEFAULT_DUR` giống Phase 1, giữ lại vĩnh viễn như 1 công
cụ debug hữu ích chứ không gỡ bỏ), người dùng quyết định đơn giản hoá thêm:
bỏ hẳn 3 ô nhập tay trong modal soạn step — tên người đăng/bình luận
(`#m-uname`, `#m-uname2`), avatar URL (`avatarFieldHtml()` — đã XOÁ hoàn
toàn hàm này cùng `onModalAvatarUrlChange()`/`readAvatarUrl()`, không dùng
nữa), và "Thời gian đã đăng" (đã xoá khỏi `statsFieldsHtml()`). Lý do: đỡ
phải nghĩ tên/tìm ảnh/nghĩ giờ hợp lý mỗi lần soạn step — luôn tự động có
sẵn.

- **Khác biệt quan trọng với mục 5.10 (blur)**: mục 5.10 `displayIdentity()`
  chỉ đổi **lớp hiển thị tạm thời** (data gốc `step.uname` vẫn do user gõ,
  bật/tắt blur đổi qua đổi lại giữa 2 trạng thái). Mục này thì NGƯỢC LẠI —
  `randomFakeIdentity(seed)` (~dòng 1213, cùng thuật toán/mảng nguồn với
  `displayIdentity()` nhưng hàm riêng) chạy **1 lần duy nhất lúc tạo step
  mới**, kết quả lưu THẬT vào `step.uname`/`step.avatarUrl`/
  `stats.timeLabel` — không còn "giá trị thật" nào khác đứng sau nữa. Seed
  dùng `stepIdCounter + ':m'`/`':m2'` (id SẮP được gán cho step mới, chưa
  tăng biến đếm tại thời điểm gọi).
- **Sửa step không random lại**: `submitStepModal()` giờ check
  `editingNow = editingStepId != null ? steps.find(...) : null` — nếu đang
  sửa step cũ (`editingNow` có giá trị), giữ nguyên
  `uname`/`avatarUrl`/`stats.timeLabel` đã có, chỉ cập nhật nội dung/ảnh/số
  liệu tương tác vừa sửa; nếu là step mới hoàn toàn, mới gọi
  `randomFakeIdentity()`. Nếu không tách 2 nhánh này, sửa lại 1 step nhiều
  lần sẽ liên tục đổi tên/avatar/giờ mỗi lần bấm "Lưu" — sai hoàn toàn ý đồ
  ổn định danh tính theo step.
- **Công tắc blur ở sidebar vẫn giữ nguyên** (theo yêu cầu người dùng, dù
  không còn ô nhập giá trị thật) — vẫn có tác dụng với các step đã tạo
  TRƯỚC khi tính năng này ra đời (từng nhập tên/avatar/giờ thật tay qua ô
  nhập cũ, data đó vẫn còn nguyên trong `steps[]` nếu đang mở lại project
  cũ/nạp JSON cũ). Với step mới tạo sau thời điểm này, bật/tắt blur không
  còn khác biệt gì về mặt hiển thị (vì `step.uname` vốn đã luôn là tên giả
  random rồi, `displayIdentity()` áp cùng seed sẽ ra đúng lại tên đó) —
  đây là hành vi dư thừa vô hại, không phải bug, chấp nhận được để không
  phải xoá hẳn cơ chế blur (còn dùng cho step cũ).
- Test qua CDP: xác nhận cả 3 ô đã biến mất khỏi DOM modal
  (`document.getElementById('m-uname')` v.v. trả `null`), step mới tự có
  identity đầy đủ ngay khi submit (không cần gõ gì), 2 step khác nhau ra
  identity khác nhau, sửa lại nội dung KHÔNG làm đổi identity đã gán, reply
  có 2 nhân vật độc lập, và công tắc blur không lỗi khi bật/tắt.

### 5.12. Upload media qua kéo-thả — `threads/media_server.py`

Đường dẫn `file://` (mục 5.9) cho meme/audio có 1 hạn chế lớn: **chỉ hoạt
động khi mở `index.html` trực tiếp bằng `file://`**, không hoạt động khi
chạy qua bất kỳ http server nào (Live Server, `python -m http.server`...) —
đúng ngay hoàn cảnh người dùng đang dùng Live Server (port 5501) để dev.
Giải pháp: thêm 1 server upload riêng, kéo-thả file thẳng vào app thay vì
gõ đường dẫn tay.

**Backend**: `threads/media_server.py` — FastAPI server độc lập, cổng
`8010` (8004-8006 là server gốc repo, 8007 là `tts_server.py`, 8008/8009 dự
phòng). Tương tự `tts_server.py` (mục 5.7) là 1 microservice riêng biệt
scoped trong `threads/`, nhưng **không import gì từ gốc repo** (khác
`tts_server.py` — server này chỉ đơn thuần lưu file, không có logic phức
tạp nào cần tái dùng). Endpoint:
- `POST /upload` — nhận `UploadFile` qua multipart form, lưu vào
  `threads/media/` với tên file đã khử ký tự đặc biệt + gắn suffix hex
  ngẫu nhiên (`_safe_filename()`, tránh trùng tên/path traversal), trả về
  `{url, filename, originalName, isVideo, size}`. `isVideo` xác định qua
  đuôi file (regex `\.(mp4|mov|webm|m4v)$`, cùng pattern với `mediaIsVideo`
  ở mục 5.9).
- `GET /media/{filename}` — serve file đã upload qua `FileResponse`, có
  check `MEDIA_DIR not in path.resolve().parents` để chặn path traversal
  (`../../etc/passwd` kiểu tấn công) dù filename đã qua `_safe_filename()`
  khi lưu — phòng thủ 2 lớp.

**Frontend**: `filePathFieldHtml()` (~dòng 1099, cho cả `meme` và `audio`)
giờ ưu tiên hiện **vùng kéo-thả** (`.media-dropzone`, `ondrop`/`ondragover`/
click-để-mở-dialog qua `<input type="file" style="display:none">`) làm
lựa chọn CHÍNH, ô nhập đường dẫn `file://` cũ vẫn giữ nhưng thu gọn vào
`<details>` (lựa chọn phụ, dành cho ai vẫn thích cách cũ khi mở app trực
tiếp không qua server nào).
- `uploadMediaFile(file)`: `FormData` + `fetch(MEDIA_SERVER_URL + '/upload')`,
  lưu kết quả vào biến module-scope `modalUploadedMedia` (reset về `null`
  mỗi lần `openStepModal('meme'|'audio', ...)` mở lại — set theo
  `editing.mediaUrl` nếu step đang sửa từng dùng upload, hoặc `null` nếu
  chưa có/dùng `file://`). Có trạng thái loading/lỗi rõ ràng
  (`#m-upload-status`), báo tên `threads/media_server.py` cụ thể trong
  thông báo lỗi để người dùng biết cần chạy lệnh gì.
- **2 nguồn (upload vs đường dẫn `file://`) loại trừ nhau**: gõ tay vào ô
  path (`onFilePathTyped()`) sẽ xoá `modalUploadedMedia` đang có (huỷ kết
  quả upload trước đó); ngược lại upload thành công cũng xoá trắng ô path.
  Tránh tình trạng cả 2 cùng có giá trị, không biết ưu tiên cái nào.
- `submitStepModal()` nhánh `meme`/`audio`: ưu tiên `modalUploadedMedia`
  nếu có (set `mediaPath = null` vì không phải path local, không cần hiện
  lại trong ô path khi sửa step sau này); fallback dùng `m-file-path` +
  `toFileUrl()` như cũ nếu không có upload nào.
- Test qua CDP: xác nhận dropzone/file-input/path-input đều tồn tại đúng,
  giả lập upload qua `new File([...])` + gọi thẳng `uploadMediaFile()` (vì
  headless không giả lập được thao tác kéo-thả DataTransfer file thật —
  test ở mức logic, không phải UI kéo-thả), xác nhận `isVideo` tự nhận
  diện đúng theo đuôi file, step lưu đúng URL trả về từ `media_server.py`,
  **và quan trọng nhất: fetch trực tiếp URL đó qua HTTP xác nhận file thật
  sự truy cập được** (không chỉ tin JS trả về đúng), mở lại sửa step hiện
  đúng tên file đã upload, và audio/meme dùng chung cơ chế đều hoạt động
  đúng.
- Sidebar mục 3 (TTS) có thêm dòng hint nhắc chạy
  `python3 threads/media_server.py` — đặt cạnh TTS vì đều là "server phụ
  trợ cần bật thêm" người dùng dễ quên.

### 5.13. Audio: mặc định tuần tự, tuỳ chọn song song — kéo-thả sắp xếp step

Sau khi làm xong step `audio` chạy song song (mục 5.8), người dùng phản hồi:
muốn ĐA SỐ trường hợp âm thanh vẫn chiếm thời gian riêng như bình thường
(tuần tự), chỉ khi nào cố ý mới cho nó chạy song song — chứ không phải mọi
step audio đều tự động song song như thiết kế ban đầu.

**Đổi mặc định `audio`**: `isParallelAudio(s) { return s.type === 'audio'
&& s.parallel; }` (~dòng 1591) — chỉ step có cờ `s.parallel === true` mới
bị loại khỏi track chính trong `computeTimeline()`/`computeSceneAt()`/
`renderIdleScene()` (đã sửa cả 3 chỗ, trước đây check `s.type === 'audio'`
thẳng thì giờ đổi thành gọi `isParallelAudio(s)`). Step audio mới tạo mặc
định `parallel` là falsy (checkbox `#m-parallel` trong modal, KHÔNG check
sẵn) — tự chiếm chỗ riêng trên timeline như post/comment, đúng hành vi
Phase 1 trước khi có mục 5.8. Muốn song song thì tick checkbox "⏱ Chạy
song song" khi soạn/sửa step audio.

**UI panel kịch bản — nhóm hàng khi song song**: khi 1 step audio có
`isParallelAudio() === true`, nó được nhóm vào cùng **1 hàng** (`.step-row`,
flexbox ngang) với step track-chính nó đang bám theo — 2 card kề nhau
(card audio chiếm `flex: 0 0 42%`, hẹp hơn card chính) để biểu thị trực
quan "chạy cùng lúc", thay vì xếp dọc như các step khác. `renderStepList()`
(~dòng 1477) duyệt `steps[]`, gom nhóm: mỗi khi gặp step audio song song,
đẩy nó vào cùng mảng con với step gần nhất trước đó (`rows[rows.length-1]`);
gặp step khác (kể cả audio KHÔNG song song), tạo hàng mới. Panel kịch bản
(`.script-panel`, cột phải của layout 3 cột) được nới rộng từ `360px` lên
`460px` (`grid-template-columns` ở `.layout`) để đủ chỗ cho layout 2 cột.

**Kéo-thả thay nút ↑↓**: người dùng yêu cầu "làm nó dạng kéo càng tốt" —
đã bỏ hẳn `moveStep()`/nút ↑↓ cũ, thay bằng HTML5 Drag & Drop API thuần
(`draggable="true"` trên mỗi `.step-card` + `ondragstart`/`ondragover`/
`ondrop`/`ondragend`). Kéo bất kỳ step nào thả vào step khác sẽ chuyển vị
trí trong mảng `steps[]` (`splice` ra rồi `splice` vào đúng chỗ) — **quan
trọng**: khi thứ tự mảng đổi, `computeTimeline()` tự động tính lại đúng
audio song song bám theo step chính MỚI đứng ngay trước nó trong mảng (đã
test: kéo 1 post B chen vào giữa post A và audio song song đang bám A →
audio tự "chuyển" sang bám B, không còn dính A nữa) — hệ quả tự nhiên của
thiết kế mục 5.8 (audio bám theo vị trí trong mảng, không lưu tham chiếu
cố định tới step nào), không cần code gì thêm để xử lý trường hợp này.
Kéo-thả chỉ đổi THỨ TỰ trong danh sách — không dùng để "kéo thả audio vào
step khác để gán song song" (đã hỏi và chốt, người dùng chọn giữ đơn giản:
gán song song vẫn qua checkbox trong modal, không lẫn với thao tác kéo-thả
sắp xếp).

**Bug tiện thể sửa luôn**: nút "Sửa" (✎) trước đây bị ẩn cho `meme`/`audio`
(điều kiện `s.type!=='meme'&&s.type!=='audio'` — sai sót từ trước, có lẽ
sao chép từ thời `meme`/`audio` chưa có nội dung gì để sửa). Từ khi có
dropzone/checkbox parallel/đường dẫn file, cả 2 loại này đều cần sửa lại
được — đã bỏ điều kiện, nút Sửa hiện cho mọi loại step.

- Test qua CDP: checkbox parallel mặc định tắt, audio mới tạo tính đúng
  timing tuần tự (chiếm chỗ sau step trước); bật parallel → nhảy về bám
  đúng `_start` của step trước; thêm step tiếp theo không bị ảnh hưởng bởi
  audio song song xen giữa; DOM nhóm đúng `.step-row` (1 row chứa 2 card);
  giả lập kéo-thả qua gọi thẳng logic `splice` (vì headless Chrome không
  giả lập chuẩn `DataTransfer` kéo-thả thật giữa 2 phần tử DOM) xác nhận
  đổi thứ tự đúng; và test riêng trường hợp biên kéo step chen giữa
  1 nhóm song song — audio tự chuyển sang bám step mới, không lỗi.

### 5.14. Thư viện âm thanh + meme có sẵn — chọn nhanh thay vì upload lại

Người dùng đã copy sẵn 16 file âm thanh hiệu ứng (vine-boom, bonk, among,
cat-laugh, x-files-theme...) vào `threads/audio/`, và ảnh gif meme (vd
`john-jonah-jameson-lol.gif`) vào `threads/gif/` (2 thư mục tự tạo, không
phải `threads/media/` — đó là nơi `media_server.py` LƯU file upload, khác
mục đích). Thay vì phải kéo-thả/upload lại từng file mỗi lần dùng, thêm 1
dropdown "Chọn từ thư viện có sẵn" ngay trong modal soạn step audio VÀ meme.

**Backend** (`threads/media_server.py`): `LIBRARY_DIRS = {"audio":
.../threads/audio, "meme": .../threads/gif}` — map tên category → thư mục
cố định. Lưu ý tên category "meme" trỏ tới thư mục vật lý tên "gif" (không
trùng tên) — làm vậy vì "meme" mô tả đúng mục đích dùng (chọn cho step
loại `meme`) trong khi "gif" chỉ là tên thư mục người dùng đặt, tên
category và tên thư mục KHÔNG cần khớp nhau, kiến trúc `LIBRARY_DIRS` vốn
thiết kế để tách biệt 2 khái niệm này ngay từ đầu. 2 endpoint dùng chung
cho mọi category, không cần sửa gì khi thêm category mới:
- `GET /library/{category}` — liệt kê file trong thư mục tương ứng (lọc
  file ẩn bắt đầu bằng `.`), trả về mảng `{filename, url, isVideo, size}`.
  Không cache gì — đọc thư mục trực tiếp mỗi lần gọi, luôn phản ánh đúng
  file hiện có (người dùng copy thêm file vào thư mục thì lần mở modal kế
  tiếp tự thấy ngay, không cần restart server).
- `GET /library-file/{category}/{filename}` — serve file, cùng kiểu check
  path traversal như `/media/{filename}` (`lib_dir not in
  path.resolve().parents`).

**Frontend**: `filePathFieldHtml()` nhận thêm tham số `libraryCategory` —
nếu có, render `libraryPickerHtml()` (1 `<select>`) ngay phía trên vùng
kéo-thả. `loadLibraryOptions(category)` gọi khi mở modal (`openStepModal()`
— `type === 'meme'` gọi `loadLibraryOptions('meme')`, `type === 'audio'`
gọi `loadLibraryOptions('audio')`, cả 2 dùng chung 1 hàm tổng quát, chỉ
khác category truyền vào) — fetch danh sách, đổ vào `<option>`, mỗi option
gắn `value` = JSON stringify cả object item (để `onLibrarySelected()`
không cần gọi lại API, đọc thẳng từ `value` đã chọn). Chọn 1 item gán
thẳng vào `modalUploadedMedia` — **dùng chung biến/luồng với kết quả
upload** (mục 5.12): không cần phân biệt "media do upload" hay "media từ
thư viện" ở tầng `submitStepModal()`/step data, cả 2 đều ra `mediaUrl` trỏ
tới `media_server.py`, xử lý identical. Chọn thư viện cũng xoá ô path
`file://` giống hệt cơ chế `onFilePathTyped()`/upload (3 nguồn — thư viện,
upload, path — loại trừ lẫn nhau, dùng cái chọn sau cùng).
- Test qua CDP (cho cả 2 category): dropdown load đúng số file thực tế
  trong thư mục; chọn 1 item gán đúng `modalUploadedMedia`; submit lưu
  đúng URL `/library-file/{category}/...`; **fetch trực tiếp URL đó qua
  HTTP xác nhận file thật, đúng kích thước byte gốc** (không chỉ tin
  response JSON, đặc biệt quan trọng với gif — file 4MB, xác nhận không bị
  cắt/hỏng khi serve); mở lại sửa step hiện đúng tên file đã chọn; và xác
  nhận 2 category không ảnh hưởng lẫn nhau (mở modal audio sau khi đã test
  meme vẫn load đúng danh sách audio, không bị lẫn).

### 5.15. Thư viện dạng lưới trực quan — nghe/xem thử trước khi chọn

Người dùng phản hồi dropdown `<select>` (mục 5.14) "giao diện hơi kém" —
phải chọn mù theo tên file, không biết trước âm thanh nghe thế nào hay gif
trông ra sao. Đã hỏi và chốt qua `AskUserQuestion`: đổi sang **lưới (grid)**
— audio có nút ▶ nghe thử ngay tại chỗ (phát trong trang, không cần tải
về/mở app khác), meme/gif hiện thumbnail ảnh thật (gif tự chạy hoạt hình
ngay trong ô nhỏ).

- `libraryPickerHtml()` giờ chỉ render 1 nút toggle (`.lib-toggle-btn`) +
  khung `#m-library-grid` rỗng ẩn sẵn (`display:none`) — tránh chiếm chỗ
  modal khi không cần, người dùng chủ động bấm mở mới thấy lưới. Sau khi
  `loadLibraryOptions()` fetch xong, đổi luôn chữ trên nút toggle thành
  "📂 Chọn từ thư viện có sẵn (N)" để biết trước có bao nhiêu file mà
  không cần mở lưới ra xem.
- **CSS `.lib-grid:has(.lib-item-audio)` / `:has(.lib-item-meme)`**: dùng
  `:has()` để lưới tự đổi layout theo loại nội dung bên trong — audio xếp
  1 cột dọc (mỗi hàng đủ rộng cho tên file dài), meme/gif xếp lưới 3 cột
  ảnh vuông. Một khối CSS chung xử lý cả 2 category, không cần class
  riêng theo category ở tầng `.lib-grid`. Dự án đã dùng `:has()` trước đó
  (`.avatar-wrap:has(.avatar.sm)` — mục 4), nên biết chắc tương thích với
  môi trường thực tế người dùng dùng.
- **Nghe thử audio**: `toggleLibraryPreviewAudio(url, btn)` dùng 1
  `<audio>` element tạo động DÙNG CHUNG cho mọi nút nghe thử trong modal
  (biến module-scope `_libPreviewAudioEl`), không phải 1 `<audio>` riêng
  mỗi item (tránh nhiều audio phát chồng lẫn nhau nếu bấm nhanh nhiều nút).
  Bấm nút đang phát → pause (toggle qua lại), bấm nút khác trong khi 1 nút
  đang phát → tự dừng nút cũ (đổi icon `⏸`→`▶`) rồi phát nút mới — theo
  dõi qua `_libPreviewPlayingBtn` (tham chiếu tới `<button>` DOM đang phát,
  không phải id, vì lưới generate động không có id cố định ổn định giữa
  các lần render). `onended` tự reset icon khi phát hết, không cần chờ
  người dùng bấm dừng tay.
- **Chọn item**: đổi từ `<option>`/`onchange` (mục 5.14) sang click trực
  tiếp vào `.lib-item` (`data-item` chứa JSON của item, đọc qua
  `el.dataset.item` — thay cho đọc `sel.value` cũ). `event.stopPropagation()`
  trên nút play để bấm nghe thử KHÔNG vô tình trigger luôn việc chọn item
  (2 hành động độc lập trên cùng 1 ô, dễ nhầm nếu không chặn bubble).
  Item đã chọn có `.selected` (viền xanh) để biết rõ đang chọn cái nào khi
  mở lại lưới.
- Test qua CDP: grid ẩn mặc định, hiện đúng khi bấm toggle; audio grid có
  đủ nút play cho mọi item; bấm nghe thử → `<audio>` phát đúng URL, icon
  đổi `▶`→`⏸`; bấm lại → pause, icon về `▶`; bấm chọn item (không phải nút
  play) → `.selected` đúng, `modalUploadedMedia` đúng, dropzone hiện đúng
  tên; submit lưu đúng; meme grid hiện đúng `<img>` thumbnail thật trỏ tới
  URL gif (không phải icon giả cho trường hợp không phải video).

### 5.16. Card cũ không bị animation lại khi cộng dồn/sửa step khác

Người dùng chỉ ra: khi KHÔNG tick "xoá nội dung cũ" (gap=0, nội dung cộng
dồn — mục 5.4), lúc step mới (vd step 3) xuất hiện đè lên step cũ (step 2
đã có từ trước), step 2 KHÔNG NÊN có hiệu ứng xuất hiện lại — chỉ step 3
mới mới cần animation. Trước đây `renderAtTime()` luôn ghi lại toàn bộ
`stackEl.innerHTML` mỗi khi "key" (chuỗi nối id các step trong stack) đổi
— destroy hết node DOM cũ rồi tạo lại, khiến CSS `animation: tcard-in`
(mục ~dòng 300, tự kích hoạt mỗi khi phần tử được CHÈN MỚI vào DOM) chạy
lại cho MỌI card kể cả những card không đổi gì.

**Giải pháp — diff theo vị trí, giữ nguyên node không đổi**: thay vì so
sánh "key có đổi không" rồi rebuild tất-cả-hoặc-không-gì, `renderAtTime()`
giờ tìm "tiền tố chung" (common prefix) giữa `ids` (mảng id stack mới) và
`_lastStackIds` (mảng id lần render trước, **theo đúng vị trí**, không
phải tập hợp) — các vị trí đầu giống hệt nhau thì GIỮ NGUYÊN node DOM cũ
(không đụng vào), chỉ `removeChild` + `insertAdjacentHTML('beforeend',
...)` phần đuôi thực sự khác biệt, kể từ vị trí lệch đầu tiên.

- **"Giống hệt" không chỉ là id trùng** — 1 step có thể giữ nguyên id
  nhưng bị SỬA NỘI DUNG (text/avatar/ảnh/stats — qua nút ✎ sửa step ở
  giữa stack, không phải thêm mới). Nếu chỉ so id, trường hợp này sẽ bị bỏ
  sót (tưởng "giống cũ" nên giữ nguyên node, nhưng nội dung hiển thị đã
  đổi, card sẽ hiện sai/cũ). Giải quyết bằng `stepRevision` — 1 `Map<id,
  số lần sửa>`, tăng qua `bumpStepRevision(id)` mỗi khi `submitStepModal()`
  sửa 1 step đã tồn tại (nhánh `editingStepId != null`). So sánh "tiền tố
  chung" giờ kiểm tra CẢ id lẫn revision tại từng vị trí khớp nhau — lệch
  1 trong 2 thì dừng tiền tố chung tại đó. `updateStepField()` (đổi
  `duration`/`gap`/`clearBefore`) KHÔNG bump revision vì các field này chỉ
  ảnh hưởng timeline, không đổi nội dung HIỂN THỊ bên trong card.
- **Bẫy đã gặp khi implement (quan trọng nếu sửa lại đoạn này)**:
  `_lastStackIds.length === 0` KHÔNG được coi là "hợp lệ để append" dù về
  logic thuần `[].every(...)` luôn trả `true` (vacuous truth trong JS) —
  nếu không chặn riêng, code sẽ tưởng "mảng cũ rỗng nên coi như khớp tiền
  tố toàn phần" rồi CHỈ insertAdjacentHTML thêm vào, trong khi DOM thực tế
  vẫn còn card cũ (case: `toggleBlurIdentity()`/`togglePlay()` chủ động
  reset `_lastStackKey/_lastStackIds` để ép render lại, nhưng
  `thr-stack` chưa hề bị xoá) → kết quả là card cũ + card mới hiện TRÙNG
  LẶP cùng lúc. Đã sửa bằng cách chỉ coi là "có thể giữ node cũ" khi
  `stackEl.children.length === _lastStackIds.length` (đối chiếu DOM thật,
  không chỉ tin biến JS) — nếu số con thực tế trong DOM lệch với số liệu
  ghi nhớ, luôn rebuild toàn bộ cho an toàn.
- `toggleBlurIdentity()` reset `_lastStackIds = []` (không chỉ
  `_lastStackKey`) — vì tên/avatar/giờ hiển thị của MỌI card có thể đổi
  hoàn toàn khi bật/tắt blur (mục 5.10/5.11), không có ý nghĩa "giữ nguyên
  1 phần", nên cố ý ép nhánh rebuild-toàn-bộ. `togglePlay()` cũng reset cả
  2 biến (bắt đầu Play luôn nhảy về `t=0`, cảnh khác hẳn cảnh tĩnh trước
  đó — rebuild toàn bộ hợp lý). Ngược lại, `renderIdleScene()` (gọi sau
  mỗi lần thêm/sửa/xoá step khi KHÔNG đang Play — đây chính là kịch bản
  người dùng mô tả) chỉ reset `_lastStackKey` (ép `renderAtTime()` không
  early-return do key giống hệt), **cố ý giữ nguyên `_lastStackIds`** để
  thuật toán diff theo vị trí có dữ liệu so sánh, mới phát huy tác dụng.
- Test qua CDP (đánh dấu `dataset.testMarker` trên node DOM thật để phát
  hiện node có bị tạo lại hay không — cách duy nhất phân biệt được "node
  cũ được giữ" với "node mới có nội dung giống hệt node cũ"): sửa step
  không liên quan (step 3) → card của step 1 giữ nguyên node; thêm step
  mới → các card cũ giữ nguyên, chỉ card mới được tạo; sửa step Ở GIỮA
  stack → card trước vị trí sửa giữ nguyên, card từ vị trí sửa trở đi
  (kể cả card sau nó dù không đổi gì) được rebuild đúng — chấp nhận được
  vì đơn giản hơn nhiều so với diff từng phần tử độc lập, và trường hợp
  "sửa step giữa" ít phổ biến hơn "thêm step mới ở cuối"; bật/tắt blur ép
  rebuild toàn bộ, không còn marker cũ sót lại và không nhân đôi card;
  và mô phỏng Play chạy qua từ step 1 sang step 2 (cộng dồn) xác nhận card
  step 1 không bị animate lại giữa lúc phát.

### 5.17. Ghép & xuất video — kiến trúc FFmpeg + Playwright

Hạng mục lớn cuối cùng còn thiếu (mục 4 sidebar, trước đây là placeholder
"sắp có"). Quyết định kiến trúc đã chốt với người dùng qua `AskUserQuestion`
(dùng `EnterPlanMode` cho hạng mục này vì độ phức tạp lớn, ảnh hưởng nhiều
file): **FFmpeg phía server** (không dùng Canvas/WebCodecs trình duyệt),
convert mỗi thẻ Threads sang PNG bằng **Playwright** (`pip3 install
playwright && playwright install chromium` — cần cài mới, repo trước đó
không có công cụ HTML→ảnh nào; đã research kỹ `content_video.py`/
`translate_video.py` xác nhận chỉ dùng Pillow vẽ trực tiếp, không phù hợp
vì phải vẽ lại UI Threads bằng tay — rủi ro lệch mà CONTEXT.md mục 7.1 đã
cảnh báo tránh).

**4 quyết định nhỏ đã hỏi và chốt:**
- Video nền: **luôn tắt tiếng gốc**, chỉ giữ TTS + nhạc nền + hiệu ứng —
  khớp hành vi preview hiện tại (video nền vốn chỉ để làm hình, mute từ
  trước — mục 5 sidebar).
- Nền ngắn hơn kịch bản → **tự động loop** (`-stream_loop -1`), giống thẻ
  `<video loop>` trong preview.
- Video nền/nhạc nền (đang là `File` blob trong bộ nhớ JS, chưa từng lưu
  ra đĩa) → **tự động upload lên `media_server.py`** ngay khi bấm nút Xuất,
  trước khi gọi `/export` — tái dùng nguyên endpoint `/upload` đã có sẵn từ
  mục 5.12, không cần đổi cách chọn video/nhạc nền hiện tại (vẫn qua
  `<input type=file>`, khác với cách meme/audio đã đổi sang URL/thư viện).
- UI tiến trình: thanh progress + text, poll qua endpoint riêng mỗi 1.5s
  (không dùng WebSocket — đơn giản hơn, đủ dùng cho tool cá nhân).

#### Vấn đề kỹ thuật cốt lõi: "stack cuộn động"

Trong preview, `autoScrollStack()` (mục 5.5) dịch chuyển `.thr-stack` lên
khi nhiều thẻ cộng dồn tràn khung — vị trí Y của 1 thẻ KHÔNG cố định, thay
đổi theo số thẻ đang cộng dồn. Thay vì viết lại thuật toán positioning
bằng Python (phức tạp, dễ sai, trùng lặp logic đã có), giải pháp: với mỗi
"quãng ổn định" (khoảng thời gian giữa 2 mốc `_start`/`_end` liền kề của
mọi step post/comment/reply/meme — trong khoảng đó `computeSceneAt()` cho
kết quả không đổi), chụp **TOÀN BỘ khung `#preview-frame`** (không phải
từng thẻ riêng) làm 1 ảnh PNG nền trong suốt duy nhất, rồi overlay đúng
theo `enable='between(t,start,end)'` trong FFmpeg. Tái dùng 100% logic
render/cuộn đã có, đã test kỹ (mục 5.5, 5.10, 5.11) — không cần biết gì về
vị trí từng thẻ ở tầng Python/FFmpeg.

`compute_breakpoints()` (`export_server.py`) — hợp mọi `_start`/`_end` của
step post/comment/reply/meme (KHÔNG gồm audio — nó không hiện hình, không
ảnh hưởng stack) thành 1 danh sách mốc sắp xếp tăng dần; mỗi đoạn giữa 2
mốc liền kề = 1 ảnh cần chụp.

#### Files mới

- **`threads/render_shared.js`** — tách các hàm build-HTML + tính timeline
  THUẦN (không side-effect DOM ngoài việc build chuỗi HTML/không tự phát
  audio) từ `index.html` ra file dùng chung: `esc`, `fmt`,
  `effectiveDuration`, `isParallelAudio`, `computeTimeline`, `statsRow`,
  `imgWrapHtml`, `avatarHtml`, `buildPostHtml`, `buildCommentRow`,
  `hueOf`, `RANDOM_IDENTITY_*`, `seedHash`, `blurIdentityOn`,
  `displayIdentity`, `buildCardHtml`, `stepRevision`/`bumpStepRevision`,
  `computeSceneAt`, `autoScrollStack`. Cả `index.html` và
  `export_render.html` cùng `<script src="render_shared.js">` — 1 nguồn
  duy nhất cho style/markup/logic timeline, không bao giờ lệch nhau.
  - **Yêu cầu khi dùng**: trang gọi phải tự khai báo `let steps = []`
    TRƯỚC KHI các hàm này chạy (không export/import module — giữ tinh
    thần "không build step" mục 7.6). Các hàm đọc thẳng biến global
    `steps`, không nhận qua tham số — y hệt cách `index.html` vốn viết.
  - `blurIdentityOn()` đọc `document.getElementById('blur-identity-toggle')`
    — phần tử này KHÔNG tồn tại ở `export_render.html`, nên luôn trả
    `false` (el là null) — **đây là hành vi ĐÚNG Ý**: video xuất ra cuối
    cùng luôn hiện tên/avatar THẬT, không mờ, giống người dùng tự tắt blur
    trước khi xuất bản chính thức.
  - **Bẫy đã gặp lúc tách file (rất dễ lặp lại nếu sửa `render_shared.js`
    sau này)**: `renderAtTime()`, `syncTtsAudio()`, `syncSfxAudio()`,
    `ttsClipAt()`, `stopAllSfxAudio()`, `sfxAudioEls` **CỐ Ý KHÔNG** nằm
    trong `render_shared.js` — dù chúng gọi `computeSceneAt`/`buildCardHtml`,
    chúng thao túng DOM `<audio>` thật (`#tts-audio`, `new Audio()` cho
    hiệu ứng) và biến trạng thái `playing`, đặc thù cho trang soạn/preview
    tương tác — `export_render.html` chỉ cần build DOM tĩnh để chụp ảnh,
    không cần phát âm thanh gì. Lần đầu tách file đã **vô tình xoá mất**
    các hàm này (nằm xen giữa `computeSceneAt` và `autoScrollStack` trong
    file gốc, đoạn cắt quá rộng), gây `ReferenceError: renderAtTime is not
    defined` ngay khi submit step đầu tiên — phải khôi phục lại nguyên văn
    vào `index.html`. Bài học: khi cắt 1 khối code lớn để tách file, liệt
    kê ĐẦY ĐỦ tên hàm trong đoạn định cắt trước (`grep -n "^function "`),
    không chỉ tin vào "từ dòng X đến dòng Y".
- **`threads/export_render.html`** — trang nội bộ (không dành cho người
  dùng mở trực tiếp), copy NGUYÊN VĂN toàn bộ `<style>` (CSS `.tcard`/
  `.thr-*`/`.preview-frame`/...) + SVG `<symbol>` icon từ `index.html`
  (không tự vẽ lại — đã verify bằng cách chụp `#preview-frame` ở cả 2 trang
  cùng 1 step, so ảnh cạnh nhau, layout/font/icon/khoảng cách khớp
  pixel-perfect). Khung `#preview-frame` set `width` cố định = độ phân
  giải xuất (1080px, qua query string `?w=1080`) thay vì `max-width:460px`
  của preview — nhờ CSS `container-type:inline-size` có sẵn (mục 5.2), mọi
  kích thước `cqw` bên trong tự động scale đúng tỷ lệ, không cần sửa gì
  thêm. Hàm điều khiển: `setSteps(arr)` gán `window.steps`,
  `renderStackAt(t)` gọi `computeSceneAt`/`buildCardHtml`/`autoScrollStack`
  y hệt preview nhưng bỏ hết phần TTS/audio/Play-state/animation-diff
  (không cần cho ảnh tĩnh — mỗi lần gọi là 1 lần chụp độc lập, luôn rebuild
  toàn bộ `innerHTML`, đơn giản hơn thuật toán diff của `renderAtTime()`).
- **`threads/export_server.py`** — FastAPI server cổng `8011` (8004-8006
  server gốc repo, 8007 `tts_server.py`, 8010 `media_server.py`).
  - Tự mount `StaticFiles(directory=BASE_DIR)` vào `/static/` và trỏ
    `RENDER_BASE_URL = "http://localhost:8011/static"` — Playwright mở
    `http://localhost:8011/static/export_render.html?w=1080` ngay trong
    chính server này. **Ban đầu server yêu cầu chạy thêm riêng `python3 -m
    http.server 8099` trong `threads/` — gây lỗi `net::ERR_CONNECTION_
    REFUSED` mỗi khi người dùng quên bật server đó (dễ quên vì hint UI mục
    4 sidebar không hề nhắc tới port 8099, chỉ nhắc 8010/8011)**. Đã bỏ
    hẳn yêu cầu server rời — chỉ cần chạy `python3 threads/export_server.py`
    là đủ.
  - `POST /export` — nhận `{steps, bgVideoUrl, bgAudioUrl, bgAudioVolume,
    blurIdentity}`, tạo `job_id`, chạy nền qua `asyncio.create_task` (không
    block request), trả `job_id` ngay. `blurIdentity` = khớp đúng trạng
    thái checkbox "🌫 Làm mờ tên & avatar" ở preview lúc bấm Xuất (mục
    5.10) — quyết định lại sau khi người dùng báo bug "video xuất không
    blur": TRƯỚC ĐÂY cố ý luôn hiện thật (lý do cũ: "bản chính thức không
    cần mờ"), giờ đổi lại **khớp checkbox** vì đó là hành vi người dùng
    mong đợi hơn. Truyền qua query `export_render.html?...&blur=1|0`,
    `export_render.html` set `#blur-identity-toggle.checked` + class
    `.blur-identity` trên `#preview-frame` cho khớp (2 cơ chế `render_shared.js`
    đã có: `blurIdentityOn()` đọc checkbox, CSS `.blur-identity` filter).
  - `GET /export-status/{job_id}` — trả `{status, progress, message,
    downloadUrl}`, đọc từ dict in-memory `jobs` (đủ dùng cho tool cá nhân
    chạy local, không cần DB).
  - `GET /export-download/{job_id}` — serve file `.mp4` hoàn chỉnh.
  - `_resolve_local_media(url)` — chuyển URL `http://localhost:8010/media/
    xxx` hoặc `/library-file/category/xxx` thành đường dẫn file thật trên
    đĩa (cùng máy chạy `export_server.py`/`media_server.py`) — tránh phải
    re-fetch qua HTTP khi file đã nằm sẵn, đọc thẳng từ
    `threads/media/`/`threads/audio/`/`threads/gif/`.
    **Bug đã gặp**: hàm cũ đoán đường dẫn thư viện = `BASE_DIR/<category>/
    <file>` (category lấy thẳng từ URL `/library-file/<category>/...`).
    Nhưng `media_server.py:LIBRARY_DIRS` map category `"meme"` → thư mục
    vật lý `threads/gif/`, KHÔNG PHẢI `threads/meme/` — category là tên
    loại nội dung (để hiển thị UI), tên thư mục là quyết định lưu trữ
    riêng, 2 cái không nhất thiết trùng nhau. Bug này không lộ ra khi audio
    hiệu ứng thư viện dùng category `"audio"` (tình cờ trùng tên thư mục
    `threads/audio/`), chỉ lộ khi thêm resolve cho meme (`threads/gif/`).
    Đã sửa: thêm `LIBRARY_DIR_MAP` trong `export_server.py` KHỚP CHÍNH XÁC
    với `media_server.py:LIBRARY_DIRS` — nếu sau này thêm category thư
    viện mới, phải sửa cả 2 nơi cùng lúc (không có cách chia sẻ config
    giữa 2 server Python độc lập, phải tự nhớ đồng bộ tay).
  - Audio: TTS đọc thẳng file local `threads/tts_audio/` (đã tồn tại sẵn
    từ lúc tạo step, không cần download lại), `adelay={start*1000}` đặt
    đúng mốc; nhạc nền `-stream_loop -1` + `atrim` + `volume=`; tất cả gộp
    qua `amix=inputs=N:duration=longest:dropout_transition=0` (theo đúng
    pattern đã research ở `translate_video.py:1050-1114`).
  - Video: `scale=...force_original_aspect_ratio=increase,crop=...` (phủ
    kín khung 1080x1920, khác `content_video.py` dùng `pad` để letterbox —
    ở đây cố ý cắt để lấp đầy khung dọc, đúng tinh thần preview
    `object-fit:cover`).
  - **Meme video/gif chạy ĐỘNG thật** (không còn đóng băng 1 khung hình —
    bug người dùng báo, sửa sau khi tính năng xuất video đã "hoàn thành"
    lần đầu, đúng như mục "CHƯA làm" đã cảnh báo trước). Post/comment/reply
    vẫn dùng cách chụp PNG tĩnh như cũ (đúng vì bản chất tĩnh), nhưng meme
    KHI LÀ video thật/gif động (`isDynamicMeme()` trong `export_render.html`
    — `mediaIsVideo===true` HOẶC đuôi file `.gif`) đi theo nhánh khác hoàn
    toàn:
    1. `renderStackAt(t)` (`export_render.html`) hiện `#meme-overlay` để có
       layout thật, `measureMemeBox()` đo `getBoundingClientRect()` quy đổi
       ra TỈ LỆ (`xRatio/yRatio/wRatio/hRatio`) so với `#preview-frame` —
       dùng tỉ lệ thay vì pixel vì Playwright chụp ở `TARGET_W=1080`, khác
       kích thước CSS px thật lúc đo trong trình duyệt. Ngay sau khi đo,
       **tự ẩn `#meme-overlay`** trước khi trả kết quả — để bước chụp PNG
       card tiếp theo ở Python KHÔNG dính meme vào ảnh tĩnh.
    2. `render_stack_images()` (`export_server.py`) nhận `dynamicMeme`,
       `memeBox`, `memeMediaUrl` từ mỗi lần gọi `renderStackAt`, gom vào
       segment dict `{t0, t1, img_path, dynamic_meme}` (đổi từ tuple cũ
       `(t0,t1,img_path,meme_path)` sang dict — rõ ràng hơn khi thêm field).
    3. `run_export_job()` resolve `dynamic_meme.media_url` → file thật trên
       đĩa qua `_resolve_local_media()` (dùng lại pattern có sẵn, không
       viết logic mới).
    4. `build_ffmpeg_cmd()` — với mỗi segment có `dynamic_meme`, thêm 1
       INPUT RIÊNG là chính file gốc (không phải PNG chụp): video/gif loop
       bằng `-stream_loop -1 -t {duration}` (dùng chung công thức với video
       nền — file gốc có thể ngắn hơn quãng cần hiện), GIF cần thêm
       `-ignore_loop 0` (mặc định ffmpeg chỉ phát gif 1 lần theo loop-count
       nhúng sẵn trong file, cần ép lặp vô hạn). Quy đổi tỉ lệ đo được ra
       pixel (`x_ratio*TARGET_W`...), ép chẵn (`w_px += w_px % 2` — bắt
       buộc cho `libx264`/filter `scale`), `overlay={x}:{y}:enable=
       'between(t,{t0},{t1})'` chồng lên track chính sau các PNG card.
    - Ảnh tĩnh (jpg/png) làm meme vẫn đi đúng đường CŨ (chụp chung PNG với
      `#preview-frame`, `-loop 1`) — không đổi hành vi, đã test lại xác
      nhận không regression.
    - **Bug thật NGHIÊM TRỌNG trong cách verify ban đầu — bài học về
      phương pháp test, không chỉ về code**: lần đầu "verify" bằng cách
      trích 2 khung hình cách nhau 2s trong đoạn meme rồi so MD5, thấy khác
      nhau → kết luận nhầm là "đang chuyển động". SAI: nén H.264 luôn tạo
      ra khác biệt byte nhỏ giữa các khung dù nội dung hình ảnh giống hệt
      nhau (quantization noise) — MD5 khác nhau KHÔNG chứng minh chuyển
      động thật. Người dùng test bằng mắt qua QuickTime báo "vẫn đứng 1
      frame" — đúng, gif KHÔNG hề chuyển động, dù MD5 khác. Cách verify
      ĐÚNG: crop chính xác vùng meme (toạ độ đã biết từ ffmpeg cmd), ghép
      nhiều khung thành lưới ảnh, XEM TRỰC TIẾP bằng mắt qua tool `Read` —
      chỉ cách này mới phát hiện được cả 2 khung "khác MD5" thực chất là
      cùng 1 tư thế y hệt (ông già đang cười, miệng há).
    - **Nguyên nhân thật: thiếu `-itsoffset {t0}` khi overlay input có PTS
      bắt đầu từ 0 nhưng chỉ "hiện" muộn qua `enable=between(t,t0,t1)`**.
      `overlay` filter với `enable` chỉ ẨN/HIỆN kết quả tại từng thời điểm
      — nó KHÔNG dịch PTS của input overlay. Input gif được mở với
      `-ignore_loop 0 -t {dur}` (chỉ `dur` giây dữ liệu, PTS tính từ 0).
      Khi track chính (đồng bộ theo PTS chung của toàn bộ filter graph)
      chạy tới thời điểm `t0` (lúc `enable` mới bật true), input gif —
      cũng tính theo PTS chung đó — đã "hết dữ liệu" từ lâu (chỉ có đúng
      `dur` giây kể từ đầu). FFmpeg xử lý input hết dữ liệu bằng cách giữ
      nguyên FRAME CUỐI CÙNG available → overlay đứng hình y hệt ảnh tĩnh,
      đúng frame cuối thay vì đầu (khớp mô tả người dùng: "chưa fix là
      frame đầu, sửa `_resolve_local_media` xong tưởng đã fix nhưng thực
      ra chỉ đổi sang frame cuối — vì trước đó gif hoàn toàn không đọc
      được file, giờ đọc được nhưng vẫn đứng hình do bug PTS này riêng
      biệt"). **Cách sửa**: thêm `-itsoffset {t0:.3f}` vào input gif/video
      động — dịch input để nó "bắt đầu" đúng lúc `t0` của track chính,
      khớp pha PTS hoàn toàn, overlay đọc đúng frame theo thời gian thực
      thay vì đọc hết veo rồi đứng lại.
    - Verify LẠI đúng cách: tái tạo bug bằng lệnh ffmpeg cô lập (thiếu
      `itsoffset`) — xác nhận tái hiện chính xác hiện tượng đứng hình khi
      có `enable=between` + input bị giới hạn `-t` ngắn; thêm `itsoffset`
      vào cùng lệnh cô lập đó — xác nhận hết bug; áp lại vào
      `export_server.py` thật, xuất video, crop+ghép lưới nhiều khung xem
      bằng mắt — xác nhận gif chuyển động đúng qua toàn bộ pipeline thật.
  - **`-shortest` cắt cụt cả video nếu audio ngắn hơn total_duration (bug
    NGHIÊM TRỌNG, người dùng báo "video không chạy tới đoạn meme" — video
    kết thúc sớm, không phải meme đứng yên)**. `build_ffmpeg_cmd()` cũ có
    `-shortest` ở lệnh output — khi kịch bản KHÔNG có nhạc nền và audio
    hiệu ứng cuối cùng (theo mốc `adelay`) kết thúc SỚM hơn `total_duration`
    (vd chỉ có 1 audio hiệu ứng ngắn 1-2s ở giữa kịch bản, không có gì kéo
    dài tới hết), `amix duration=longest` trả về track chỉ dài bằng audio
    cuối cùng đó — RỒI `-shortest` cắt luôn CẢ VIDEO theo độ dài audio ngắn
    hơn này, mất hẳn phần cuối kịch bản (kể cả meme, TTS đứng sau). Vì
    `duration=longest` chỉ so sánh độ dài GIỮA CÁC AUDIO TRACK với nhau,
    không biết gì về `total_duration` thật (thứ chỉ video biết qua
    `trim=duration=`) — có nhạc nền thì tình cờ không lộ bug vì
    `atrim=duration={total_duration}` luôn kéo dài track đó đủ hết, che
    lấp vấn đề. **Cách sửa: bỏ hẳn `-shortest`** — `-t {total_duration}`
    ở output đã đủ để giới hạn đúng độ dài cho CẢ HAI track (video luôn đủ
    dài vì `bg` đã trim đúng; audio ngắn hơn thì tự động im lặng ở đoạn
    dư, FFmpeg không lỗi gì khi audio input ngắn hơn `-t` yêu cầu). Verify:
    tái tạo đúng case (1 post + 1 audio hiệu ứng ngắn giữa kịch bản + 1
    meme gif cuối, không nhạc nền) — trước khi sửa output dài 1.8s (đúng
    bằng lúc audio hiệu ứng kết thúc), sau khi sửa đúng 5.0s (đủ tới hết
    meme); test lại case không audio nào (chỉ `anullsrc`) và case có nhạc
    nền để xác nhận không tạo ra khoảng lặng dư ở cuối.

#### 2 bug thật phát hiện trong lúc test (không phải do code export gây ra
#### — lộ ra vì đây là lần đầu test kỹ các trường hợp biên này)

1. **`computeSceneAt()` (`render_shared.js`) — meme bị coi là "cầu nối"
   chuỗi cộng dồn**: nếu 1 step `meme` đứng giữa 2 post/comment mà khoảng
   cách thời gian khớp đúng (post kết thúc + gap = lúc meme bắt đầu), vòng
   lặp tìm "mắt xích liên tục" (dòng ~265) duyệt QUA CẢ step meme khi lùi
   ngược tìm chuỗi — coi đó là liên tục rồi tiếp tục lùi qua, khiến post
   trước meme "hồi sinh" cộng dồn cùng content đứng SAU meme, dù giữa
   chúng có nguyên 1 step meme + gap chen ngang. Sai hoàn toàn ý đồ "gap
   là khoảng lặng cắt đứt" (mục 5.4). **Đã hỏi và chốt qua
   `AskUserQuestion`**: sửa bằng cách coi step `meme` luôn cắt đứt chuỗi
   cộng dồn, giống hệt gặp `clearBefore` (`if (s.clearBefore || s.type ===
   'meme') break;`). Phát hiện qua test Playwright thật (không phải suy
   luận đọc code) — gọi `renderStackAt()` tại nhiều mốc t, thấy `stackIds`
   chứa post đã "chết" từ lâu.
2. **`total_duration` (`export_server.py`) — loại trừ MỌI step `audio`
   thay vì chỉ audio SONG SONG**: dòng tính tổng thời lượng kịch bản dùng
   điều kiện `s.type != "audio"` để bỏ qua step audio (đúng cho audio SONG
   SONG — nó không chiếm chỗ track chính, mục 5.13), nhưng vô tình bỏ qua
   LUÔN audio TUẦN TỰ (mặc định) — khiến audio tuần tự đứng cuối kịch bản
   bị cắt cụt hoàn toàn khỏi video xuất (tổng thời lượng tính thiếu). Sửa
   điều kiện thành `not (type=='audio' and parallel)` — khớp đúng
   `isParallelAudio()` bên `render_shared.js`. Phát hiện qua đo
   `ffprobe`/`volumedetect` từng đoạn audio thật trong file `.mp4` xuất
   ra, không chỉ tin log/response JSON.

#### Test đã thực hiện (theo từng lớp, từ đơn giản tới phức tạp)

Test đơn thuần đọc code là KHÔNG đủ cho hạng mục này — mọi khẳng định
dưới đây đều verify bằng cách chạy thật, đo lường thật:
- 1 ảnh PNG thẻ chụp qua Playwright, so cạnh ảnh chụp `#preview-frame` của
  `index.html` thật cùng 1 step — xác nhận pixel-perfect (không tự vẽ lại).
- 1 file `.mp4` case đơn giản (1 post, video nền màu solid test) — mở
  `ffprobe` xác nhận đúng 1080x1920, đúng duration, trích 1 frame giữa
  video bằng `ffmpeg -ss ... -frames:v 1` xác nhận đúng nội dung thẻ.
- 2 step cộng dồn (gap=0) — trích frame ở 2 mốc thời gian khác nhau, xác
  nhận đúng lúc chỉ 1 thẻ, đúng lúc cộng dồn 2 thẻ.
- TTS thật (gọi `tts_server.py` lấy file mp3 thật) — xác nhận audio track
  trong `.mp4` xuất ra không câm (`ffmpeg -af volumedetect`, mean/max
  volume có giá trị thật, không phải toàn `-inf`).
- Nhạc nền ngắn hơn kịch bản — đo `volumedetect` theo từng giây suốt cả
  video, xác nhận không bị im lặng sau khi nhạc gốc "hết" (loop hoạt động).
- Meme overlay xen giữa 2 post có gap — phát hiện + sửa bug 1 ở trên.
- Audio song song (bám step khác) + audio tuần tự (đứng cuối) — phát hiện
  + sửa bug 2 ở trên; sau khi sửa, đo `mean_volume` từng đoạn 1 giây riêng
  biệt (re-encode PCM để tránh lỗi seek/copy stream khi đo — đã từng đo
  sai bằng cách `-ss` vượt ngoài thời lượng 1 file test ngắn, gây kết quả
  giả gây hoang mang, phải làm lại đúng cách trước khi kết luận có bug
  hay không) — xác nhận đúng: có tiếng ở đúng 2 khoảng, im lặng
  (`-73.7dB`, gần tuyệt đối) ở khoảng giữa.
- **End-to-end qua UI thật** (không chỉ gọi API `export_server.py` trực
  tiếp): dùng CDP giả lập chọn video nền qua `<input type=file>` (base64
  → `File` object thật trong JS), soạn 2 step qua modal thật, bấm nút
  "🎬 Xuất video" thật, poll progress qua đúng DOM (`#export-progress-text`,
  không đọc API response), xác nhận link tải xuất hiện, tải file về, trích
  frame xác nhận đúng nội dung — bao gồm cả tên random do blur mặc định
  bật (mục 5.11) hiện đúng trong video xuất ra.

### 5.19. Meme sẵn — gộp gif/video + âm thanh thành 1 bộ tái dùng

Người dùng phải chọn lại đúng cặp gif+audio mỗi lần dùng lại cùng 1 meme
(vd gif "cười" luôn đi kèm 1 tiếng cười cụ thể) — tốn thời gian lặp lại.
Giải pháp: nút "💾 Lưu thành meme sẵn" ngay trong modal soạn step `meme`,
đóng gói gif + audio đã chọn thành 1 bộ lưu trên server, chọn lại 1 lần từ
thư viện là tự động tạo đủ 2 step.

- **Modal meme** (`index.html:openStepModal`, case `'meme'`) thêm
  `memeSoundFieldHtml()` sau ô chọn file meme chính — dropdown `<select>`
  chọn audio kèm theo + nút "💾 Lưu thành meme sẵn". **Quyết định đã hỏi &
  chốt**: ô "âm thanh kèm theo" CHỈ hỗ trợ chọn từ thư viện audio có sẵn
  (`GET /library/audio`) — không kéo-thả/upload riêng cho ô này, vì đa số
  trường hợp dùng lại audio đã có sẵn trong `threads/audio/`, không cần
  thêm 1 dropzone nữa (đơn giản hoá UI, tránh refactor lớn các hàm dropzone
  vốn đang hard-code prefix `m-`).
- **`saveAsMemeready()`** — lấy `modalUploadedMedia` (gif/video đang chọn ở
  ô chính) + `modalMemeSoundUrl` (audio đã chọn ở dropdown), hỏi tên hiển
  thị qua `prompt()`, `POST /memeready` lên `media_server.py`.
- **Sửa lại kiến trúc UI sau khi người dùng test thật (mục 5.22 bên dưới,
  chi tiết lịch sử đầy đủ)**: BAN ĐẦU lưới "chọn từ meme sẵn"
  (`memereadyPickerHtml()`) nhét CHUNG trong modal soạn Meme thường — gây
  luồng rối: người dùng mở modal Meme ĐỊNH LẤY 1 bộ meme sẵn ra dùng, nhưng
  bấm "Lưu thành meme sẵn" lại báo lỗi "cần chọn file trước" (vì đó là nút
  dành cho việc ĐÓNG GÓI, không phải LẤY RA). Đã tách hẳn thành 2 modal độc
  lập — xem mục 5.22.
- **`media_server.py`** — thêm `MEMEREADY_DIR = threads/memeready/`, mỗi bộ
  là 1 thư mục con `memeready/<id>/` chứa gif/video + audio (COPY hẳn, không
  symlink — để không phụ thuộc file nguồn `media/`/`gif/`/`audio/` gốc còn
  tồn tại hay không, tránh vỡ nếu người dùng xoá file gốc sau này) +
  `meta.json` (label, tên file, có/không có audio). Endpoint mới:
  `POST /memeready` (lưu — resolve URL `/media/` hoặc `/library-file/` ngược
  lại thành Path thật qua `_resolve_served_url()`, copy file), `GET
  /memeready` (liệt kê), `GET /memeready-file/{id}/{filename}` (serve file),
  `DELETE /memeready/{id}` (xoá 1 bộ — chưa có nút xoá trong UI, chỉ có API,
  cần tự gọi qua `curl`/devtools nếu muốn dọn).
- **`selectMemereadyItem()`** — khác hẳn `selectLibraryItem()` (chỉ set
  media cho ĐÚNG step đang soạn trong modal): hàm này **tự tạo LUÔN 2 step
  mới** (`type:'meme'` + `type:'audio', parallel:true`) đẩy thẳng vào cuối
  `steps[]` và render lại — không đóng modal đang mở (chọn meme sẵn là 1
  thao tác độc lập, thêm thẳng vào kịch bản, không phụ thuộc modal nào đang
  soạn dở). `parallel:true` mặc định — đúng ý "gắn kèm theo" (âm thanh phát
  đè lên đúng lúc meme hiện, không chiếm thêm thời gian riêng, xem mục
  5.13 `isParallelAudio()`).
- Verify: `POST /memeready` qua `curl` xác nhận copy file đúng + trả đúng
  metadata; test qua CDP thật (chọn gif thư viện → chọn audio thư viện →
  lưu → mở modal mới → chọn từ thư viện meme sẵn) xác nhận 2 step được tạo
  đúng field (`mediaName`, `parallel: true`), không có exception JS.

### 5.20. FLIP animation cho kéo-thả đổi thứ tự step

Thuật toán đổi thứ tự trong `onStepDrop()` (mục 5.13) đã ĐÚNG từ trước —
kéo step 5 thả vào vị trí step 2 cho ra `[1,5,2,3,4]` (chèn-và-đẩy, không
phải hoán đổi 2 vị trí) — nhưng người dùng khó nhận biết đúng/sai bằng mắt
vì `renderStepList()` rebuild thẳng `innerHTML`, các thẻ bị đẩy "nhảy" tức
thời tới vị trí mới, không có chuyển động nào để mắt bám theo (ban đầu
tưởng nhầm là bug logic, hỏi lại mới xác nhận đúng là vấn đề UI/animation).

Giải pháp: kỹ thuật **FLIP (First-Last-Invert-Play)**, không cần thư viện
ngoài:
- `flipCapturePositions()` — TRƯỚC khi `renderStepList()` rebuild DOM, đo
  `getBoundingClientRect()` của mọi `.step-card[data-id]` hiện có, lưu vào
  `Map` theo `data-id` (không theo index — index đổi nhưng id ổn định).
- Rebuild DOM (`renderStepList()` + `renderIdleScene()` như cũ).
- `flipPlay(oldPositions)` — SAU khi rebuild, với mỗi thẻ còn tồn tại đo lại
  vị trí MỚI, nếu khác vị trí CŨ: set `transform: translate(dx,dy)` (dịch nó
  NGƯỢC về đúng chỗ cũ, không lộ ra mắt vì `transition:none` + diễn ra trong
  cùng 1 frame — đây là bước "Invert"), rồi `requestAnimationFrame` sau đó
  mới bật `transition: transform 220ms ease` và xoá `transform` — trình
  duyệt tự animate mượt từ vị trí cũ (giả) về vị trí mới thật (bước "Play").
  Thẻ mới thêm (không có trong `oldPositions`) hoặc thẻ không đổi vị trí thì
  bỏ qua, không set gì — tránh animation thừa.
- Verify qua CDP: giả lập `onStepDrop` trực tiếp (không cần drag event thật,
  browser tự động hoá không giả lập được HTML5 Drag&Drop API đầy đủ), xác
  nhận thứ tự dữ liệu đúng VÀ các thẻ bị dịch chuyển có
  `transition:"transform 220ms"` áp đúng, thẻ không đổi vị trí thì không có
  transition (đúng logic tối ưu).

### 5.21. Điều chỉnh vị trí + xoay cho step hiển thị (offset x/y, rotation)

Người dùng muốn tinh chỉnh bố cục thẻ Threads (post/comment/reply) và meme
trên khung 9:16 — trước đây hoàn toàn tự động (stack tự cuộn cho post/
comment/reply, vị trí cố định giữa khung cho meme). Yêu cầu: dịch chuyển
lên/xuống/trái/phải + xoay 2 hướng, qua NÚT BẤM trong step-card ở panel
phải (không kéo-thả trực tiếp trên khung preview — đã hỏi, xác nhận nút bấm
là đủ, kéo-thả phức tạp hơn nhiều không cần thiết).

**Quyết định đã chốt:**
- Áp dụng cho CẢ 4 loại: post, comment, reply, meme.
- Vị trí (offset x/y) đúng cả preview lẫn xuất video, mọi loại step.
- Riêng GÓC XOAY của meme dạng **video/gif động** (khác ảnh tĩnh): chỉ áp
  dụng ở preview, XUẤT VIDEO bỏ qua (giữ 0°), kèm cảnh báo UI. Lý do:
  FFmpeg `overlay=x:y` không xoay được, thêm filter `rotate=` riêng phức
  tạp hơn nhiều, không cần làm ngay.

**Field mới** (mọi step post/comment/reply/meme): `offsetX`, `offsetY`
(đơn vị `cqw` — tự scale đúng tỉ lệ theo `container-type:inline-size` sẵn
có, khớp cả preview ~460px lẫn export 1080px), `rotation` (độ). Mặc định
`0`, luôn đọc qua `s.offsetX || 0` (tương thích JSON cũ thiếu field). Bước
nhảy mỗi lần bấm: offset **2cqw**, rotation **5deg**. Clamp range:
`offsetX∈[-40,40]`, `offsetY∈[-60,60]`, `rotation∈[-45,45]` — tránh bay ra
khung/xoay quá đà do bấm nhầm.

**`render_shared.js`** (nguồn dùng chung — sửa ở đây trước tiên, đúng tinh
thần file dùng chung mục đầu file):
- `posTransform(s)` — trả `style="transform:..."` hoặc CHUỖI RỖNG nếu cả 3
  field = 0 (đa số step không chỉnh gì, tránh in transform thừa vô ích).
- `buildCardHtml()` — bọc `<div class="tcard-pos"${posAttr}><div
  class="tcard">...</div></div>` cho cả 3 nhánh post/comment/reply. **Lý do
  bọc wrapper NGOÀI `.tcard`, không đặt transform trực tiếp lên `.tcard`**:
  `.tcard` có `animation: tcard-in` (opacity+transform riêng, mục 5.5) —
  nếu gắn thêm transform tĩnh trực tiếp lên chính `.tcard`, animation sẽ
  ghi đè mất offset/rotation trong lúc chạy entrance rồi mới "giật" về
  đúng vị trí khi animation xong. Wrapper `.tcard-pos` không có animation
  riêng nên transform áp ổn định ngay từ đầu.
- Reply: offset/rotation áp NGUYÊN CẢ khối `.tcard-pos` (bọc cả
  `.thread-group`), KHÔNG tách riêng cho comment gốc và reply — vì
  `connector-line` (đường nối dọc) tính theo toạ độ tương đối `left/top`
  cố định, giả định 2 phần không xoay/dịch độc lập nhau; tách riêng sẽ phá
  đường nối.
- `memeOverlayTransform(memeStep)` — trả `translateY(-50%) translate(...)
  rotate(...)` (phần `translateY(-50%)` là căn giữa dọc GỐC, trước đây nằm
  tĩnh trong CSS `.meme-overlay`, giờ chuyển qua JS để cộng thêm offset).
  Dùng chung `index.html`/`export_render.html`.
- `isDynamicMeme(memeStep)` — CHUYỂN từ `export_render.html` sang đây (xoá
  bản cũ ở đó) để `index.html` cũng gọi được cho badge cảnh báo UI trong
  step-card (mục 5.17 lúc đầu chỉ cần dùng ở export, giờ preview cũng cần).
- Xác nhận qua explore: `transform` (translate/rotate) KHÔNG tham gia
  normal flow, không ảnh hưởng `scrollHeight`/kích thước box — an toàn
  100% để thêm transform riêng từng `.tcard-pos` mà không phá
  `autoScrollStack()` (mục 5.5, chỉ đọc `stackEl.scrollHeight` của
  `#thr-stack`, không đọc kích thước từng card con).

**`index.html`**:
- CSS: bỏ `transform: translateY(-50%);` TĨNH khỏi `.meme-overlay` (giờ
  set đủ qua JS mỗi lần render, xem trên); thêm `.sc-position` (khối mới
  trong step-card), `.sc-dpad` (lưới CSS grid 3x3: ▲◀⟲▶▼, nút reset ở
  giữa), `.sc-rotate` (2 nút ↶↷), `.sc-pos-val` (hiện giá trị hiện tại),
  `.sc-pos-warn` (badge cảnh báo).
- `submitStepModal()` (tạo step MỚI) và `selectMemereadyItem()` (mục 5.19):
  set `offsetX:0,offsetY:0,rotation:0` mặc định. Khi SỬA step cũ
  (`Object.assign(s, data)`), field vị trí GIỮ NGUYÊN (không bị reset —
  `data` từ modal nội dung không chứa field này, giống cách `uname`/
  `avatarUrl` được giữ nguyên qua `editingNow`, mục 5.11).
- `stepCardHtml()`: thêm `POSITIONABLE_TYPES = ['post','comment','reply',
  'meme']` — chỉ 4 loại này hiện khối `.sc-position`, KHÔNG cho `audio`
  (không có hình để dịch/xoay). Badge cảnh báo (`sc-pos-warn`) chỉ hiện khi
  ĐỒNG THỜI: `type==='meme'`, `isDynamicMeme(s)` true, VÀ `rotation !== 0`.
- `nudgeStepOffset(id,dx,dy)`, `nudgeStepRotation(id,deltaDeg)`,
  `resetStepPosition(id)` — cập nhật field, clamp qua `clampPos()`, gọi
  `bumpStepRevision(id)` rồi `renderStepList(); renderIdleScene();`.
  **KHÔNG gọi `computeTimeline()`** — offset/rotation không ảnh hưởng
  timeline, chỉ ảnh hưởng hiển thị (khác `updateStepField()` — field
  duration/gap ảnh hưởng timeline nên phải gọi).
- **`bumpStepRevision(id)` BẮT BUỘC, dễ quên nhất khi sửa code này sau
  này**: cơ chế "common prefix" trong `renderAtTime()` (mục 5.16) so khớp
  `id` VÀ `stepRevision` để quyết định có rebuild card hay không — id
  không đổi khi nudge offset, nếu thiếu bump revision thì bấm nút d-pad
  sẽ KHÔNG thấy đổi ngay trên preview (phải đợi step khác kích hoạt render
  mới vô tình thấy đúng).
- **Bug tiềm ẩn đã phát hiện + sửa trong `renderAtTime()`**: code cũ chỉ
  set `memeEl.style.transform`/`innerHTML` bên TRONG nhánh `if (memeKey
  !== _lastMemeKey)` (so theo id memeStep) — nếu chỉ offset/rotation đổi
  (id giữ nguyên, bấm nút d-pad khi meme đang hiện), nhánh này bị BỎ QUA,
  transform không được set lại, preview không phản ánh thay đổi. Đã sửa:
  thêm `if (memeStep) memeEl.style.transform =
  memeOverlayTransform(memeStep);` NGAY SAU (ngoài) nhánh if đó — luôn set
  lại transform mỗi lần `renderAtTime()` chạy, rẻ (chỉ set 1 CSS property,
  không rebuild HTML) nên không cần tối ưu tránh gọi thừa.

**`export_render.html`**:
- Xoá định nghĩa `isDynamicMeme()` cục bộ (dùng bản chung `render_shared.js`).
- `renderStackAt()`: set `memeEl.style.transform =
  memeOverlayTransform(memeStep)` ngay sau `memeEl.style.display='block'`
  — để offset/rotation được áp trước khi đo bounding box qua
  `measureMemeBox()` cho các bước tiếp theo.
- **Với meme ĐỘNG cụ thể**, TRƯỚC khi gọi `measureMemeBox()`: ghi đè lại
  transform BỎ rotation (giữ nguyên offset) — `translateY(-50%)
  translate(${ox}cqw,${oy}cqw)`, không có phần `rotate()`. Lý do:
  `getBoundingClientRect()` của phần tử ĐÃ xoay trả về bounding box hình
  chữ nhật BAO (axis-aligned, "phình" to hơn kích thước thật theo góc
  xoay) — FFmpeg `overlay=x:y` không xoay được, nếu dùng bounding box đã
  phình sẽ sai lệch vị trí/kích thước thật khi overlay. Ép rotation=0 trước
  khi đo đảm bảo `memeBox` phản ánh đúng offset (vị trí luôn đúng) nhưng cố
  ý bỏ qua góc xoay (đúng quyết định đã chốt).
- `export_server.py` KHÔNG CẦN SỬA GÌ — offset/rotation cho post/comment/
  reply/meme TĨNH tự động đúng vì chúng luôn đi qua đường chụp PNG toàn
  `#preview-frame` (CSS đã đúng thì ảnh chụp tự đúng); vị trí meme ĐỘNG tự
  đúng qua tỉ lệ `measureMemeBox()` trả về (rotation đã bị loại trước khi
  trả kết quả về Python).

**Verify đã làm** (qua CDP, `Runtime.evaluate` + `awaitPromise:true`):
1. Offset/rotation áp đúng CSS transform trên `.tcard-pos` (đọc cả style
   inline lẫn `getComputedStyle` trả `matrix(...)` khớp giá trị đã set).
2. Reset về đúng 0, `style` attribute rỗng (`posTransform` trả chuỗi rỗng).
3. Clamp đúng giới hạn khi bấm vượt quá (30 lần bấm offsetX vẫn dừng ở 40).
4. Meme overlay transform đúng công thức đầy đủ (preview).
5. Badge cảnh báo hiện/ẩn đúng điều kiện (meme động + rotation≠0 mới hiện).
6. Audio step KHÔNG có khối `.sc-position` (đúng thiết kế, chỉ 4 loại có
   hình mới cần).
7. **Export thật qua `export_server.py`**: 1 kịch bản có post
   (`offsetX:15,offsetY:10,rotation:-15`) + meme tĩnh
   (`offsetX:-10,rotation:25`) — trích khung hình từ video .mp4 xuất ra,
   xem trực tiếp bằng mắt (không chỉ đo số) xác nhận cả 2 thẻ dịch chuyển
   và xoay ĐÚNG góc/hướng như đã thiết lập, ngay trong video hoàn chỉnh.
8. **Meme động khi export**: so `memeBox` trả về giữa 2 case cùng offsetX
   (rotation=25 vs rotation=0) — kết quả HOÀN TOÀN GIỐNG HỆT NHAU, xác
   nhận rotation đã bị loại bỏ đúng trước khi đo, không làm sai lệch vị
   trí/kích thước overlay khi xuất video.
9. Round-trip JSON: field mới không nằm trong danh sách loại trừ của
   `exportProjectJson()` (`{ _start, _end, audioUrl, ... }`) nên tự động
   được giữ nguyên qua export/import, không cần sửa gì thêm.

**2 bug thật phát hiện sau khi người dùng test UI thật (không lộ ra qua
test tự động ban đầu vì mỗi lần test chỉ nudge 1 lần rồi kiểm tra, không
test bấm liên tiếp/click chọn):**

1. **Bấm nút d-pad không thấy dịch chuyển ngay — phải chuyển sang step
   khác rồi quay lại mới thấy đúng.** Nguyên nhân: điều kiện rebuild DOM
   trong `renderAtTime()` chỉ so `key` (chuỗi nối id của stack hiện tại)
   với `_lastStackKey` — nếu 2 lần gọi liên tiếp có CÙNG bộ id (ví dụ nudge
   nhiều lần trên cùng 1 kịch bản, preview đã đứng đúng tại step đó từ lần
   trước), `key` không đổi dù `stepRevision` đã tăng (do
   `bumpStepRevision()`), khiến TOÀN BỘ khối rebuild (kể cả phần dùng
   `stepRevision` để phát hiện nội dung đổi) bị bỏ qua — logic
   `commonLen`/so revision vốn đúng nhưng nằm BÊN TRONG khối `if` không
   bao giờ chạy tới. Chỉ khi đổi sang step khác (key đổi) mới ép rebuild,
   lúc đó mới đọc đúng data mới nhất — trùng khớp mô tả người dùng.
   **Sửa**: thay điều kiện so sánh từ chỉ `key` sang `revKey = key + '|' +
   revisions.join(',')` (nối thêm mảng revision từng id vào key) — bất kỳ
   revision nào tăng cũng đủ để kích hoạt rebuild dù bộ id giữ nguyên.
   Verify: nudge liên tiếp 3 lần trên cùng step (không đổi step giữa các
   lần) — DOM transform cập nhật đúng ngay mỗi lần (2cqw→4cqw→6cqw), khớp
   data, trước đó bị "kẹt" ở giá trị của lần bấm đầu tiên.
2. **Click chọn xem 1 step (không chỉnh gì) không tự nhảy preview tới lúc
   step đó hiển thị** — trước đây chỉ có nút "✎ Sửa" (mở modal) hoặc bấm
   nút d-pad mới nhảy preview; muốn xem trước 1 step ở giữa kịch bản (để
   quan sát trước khi quyết định có cần chỉnh gì không) phải tự kéo thanh
   Play dò tìm đúng lúc. **Thêm**: `jumpToStep(id)` gọi
   `previewStepPosition(s)` (đã có sẵn từ trước, dùng chung với 3 hàm
   nudge), gắn `onclick="jumpToStep(${s.id})"` vào `.sc-head` và
   `.sc-summary` trong `stepCardHtml()` (2 vùng không chứa nút/input tương
   tác nào khác — an toàn để thêm click toàn vùng, không xung đột
   drag-and-drop reorder đã có, vì click và drag không kích hoạt đồng thời
   trong HTML5 Drag API), thêm `cursor:pointer` CSS gợi ý có thể bấm.

### 5.22. Tách modal "Meme sẵn" độc lập khỏi modal soạn Meme thường

Mục 5.19 ban đầu nhét lưới "chọn từ meme sẵn" VÀO CHUNG modal soạn step
Meme thường (cùng chỗ với ô upload/chọn file meme mới) — người dùng phản
hồi: mở modal Meme ĐỊNH LẤY 1 bộ meme sẵn ra dùng ngay, nhưng không tìm
thấy chỗ, và khi bấm "Lưu thành meme sẵn" lại báo lỗi "✗ Cần chọn file
meme (gif/video) trước" — vì nút đó dành cho việc ĐÓNG GÓI (cần đã chọn
file), không phải LẤY RA. 2 nhu cầu hoàn toàn khác nhau bị trộn vào 1
modal: **Meme thường** = soạn/upload ảnh-gif mới mỗi lần, nội dung hay
biến đổi; **Meme sẵn** = lấy đúng 1 bộ (gif+audio) đã đóng gói cố định từ
trước, dùng lại nhanh, không cần soạn gì. Trộn chung khiến luồng LẤY RA bị
buộc phải đi qua bước "soạn" không liên quan.

**Giải pháp**: tách hẳn thành nút riêng "🎁 Meme sẵn" ngang hàng với
`＋ Post/Comment/Cmt+Reply/Meme/Âm thanh` trong `.add-step-bar` — bấm vào
mở THẲNG 1 modal overlay riêng (`#memeready-modal-overlay`, dùng lại class
CSS `.step-modal-overlay`/`.step-modal` có sẵn nên không cần CSS mới),
KHÔNG qua modal soạn nội dung nào. Chọn 1 item trong lưới là thêm ngay 2
step (meme + audio song song) vào kịch bản và tự đóng modal — không cần
nút "Thêm vào kịch bản" riêng như modal soạn thường (hành động chọn = hành
động thêm luôn, 1 bước).

- `openMemereadyModal()`/`closeMemereadyModal()` — mở/đóng modal riêng,
  gọi `loadMemereadyOptions()` mỗi lần mở (không cache, luôn phản ánh đúng
  danh sách hiện có trên server).
- `loadMemereadyOptions()` đổi target sang `#memeready-modal-grid` (thay
  vì `#m-memeready-grid` cũ nằm trong modal Meme thường — ID cũ đã bị xoá
  hoàn toàn khỏi codebase, không còn tồn tại).
- `selectMemereadyItem()` — thêm `closeMemereadyModal()` ở cuối (trước đây
  KHÔNG đóng modal soạn Meme thường sau khi chọn, vì modal đó có thể đang
  soạn dở nội dung khác; giờ modal Meme sẵn CHỈ để chọn, chọn xong là xong
  việc, tự đóng luôn cho gọn — không còn lý do phải giữ mở).
- Modal soạn Meme thường (case `'meme'` trong `openStepModal()`) chỉ còn
  giữ `memeSoundFieldHtml()` (dropdown audio kèm theo + nút "Lưu thành meme
  sẵn" — mục đích ĐÓNG GÓI, không đổi), đã bỏ hẳn `memereadyPickerHtml()`/
  `toggleMemereadyGrid()` (2 hàm này bị xoá khỏi codebase, không còn dùng ở
  đâu nữa — đã grep xác nhận không sót tham chiếu chết).
- Verify qua CDP: (1) modal Meme sẵn mở độc lập ngay từ nút riêng, không
  cần trạng thái nào trước đó (không có `modalUploadedMedia`), chọn item
  vẫn tạo đúng 2 step + tự đóng modal; (2) modal soạn Meme thường mở lên
  KHÔNG còn chứa gì liên quan tới "meme sẵn" (grep `innerHTML` không thấy
  chuỗi `memeready-grid`/`Chọn từ meme sẵn`), nhưng vẫn giữ đúng dropdown
  âm thanh + nút lưu — không có exception JS ở cả 2 luồng.

**Bug nhỏ phát hiện ngay sau đó (người dùng test thật)**: `save_memeready()`
(`media_server.py`) cố tình đổi tên file khi copy vào `memeready/<id>/`
thành `"gif"+suffix`/`"audio"+suffix` (đơn giản hoá lưu trữ — mỗi bộ 1 thư
mục riêng nên không cần tên độc nhất). Nhưng `meta.json` KHÔNG lưu tên GỐC
— nên `selectMemereadyItem()` (index.html) tạo step với `mediaName:
item.audioFilename` luôn ra `"audio.mp3"` chung chung, khiến người dùng
thấy step tên "audio.mp3" tưởng nhầm là lỗi/nhầm file (không phải sai —
file đúng, chỉ là TÊN HIỂN THỊ bị mất thông tin). **Sửa**: thêm
`gifOriginalName`/`audioOriginalName` vào `meta.json` (đọc từ
`gif_path.name`/`audio_path.name` — biến local giữ tên gốc TRƯỚC khi copy
đổi tên), `selectMemereadyItem()` dùng `item.gifOriginalName ||
item.gifFilename` (fallback cho item cũ lưu trước bản vá, chưa có field
mới). Verify: tạo 1 bộ memeready test với gif+audio có tên gốc rõ ràng,
chọn lại qua UI, xác nhận step tạo ra có `mediaName` đúng tên gốc
(`"john-jonah-jameson-lol.gif"`/`"vine-boom.mp3"`), không còn
`"gif.gif"`/`"audio.mp3"`.

### 5.23. Duration mặc định = độ dài thật của gif/video (đo bằng ffprobe)

Trước đây MỌI step meme (dù chọn gif/video gì) đều mặc định đúng
`DEFAULT_DUR.meme = 2` giây — hằng số cố định, không liên quan gì tới độ
dài thật của file. Người dùng phản hồi: muốn duration mặc định khớp đúng
độ dài thật của gif, và khi có audio chạy song song (`parallel:true`) đi
kèm meme, audio đó cũng nên mặc định dài bằng đúng meme (không phải bằng
độ dài thật của chính file âm thanh) — để tiếng phát đủ suốt lúc gif chạy,
không cắt cụt/dư ra. Cả 2 vẫn chỉnh tay được sau như bình thường.

**Vấn đề kỹ thuật**: trình duyệt KHÔNG có API đọc trực tiếp duration của
ảnh gif tĩnh (khác `<video>` có sẵn `video.duration` sau khi load
metadata) — phải đo phía server bằng `ffprobe` (công cụ đã dùng chung với
`export_server.py`, đáng tin cậy, không cần viết logic đo mới).

- **`media_server.py`**: thêm `_probe_duration(path)` — chạy `ffprobe
  -show_entries format=duration`, trả `None` nếu lỗi/không phải media có
  thời lượng (không raise, không chặn upload chỉ vì đo thất bại). Gắn
  `duration` vào response của `POST /upload` và `GET /library/{category}`
  (đo file lúc trả về danh sách, không cache — luôn phản ánh đúng file
  hiện có). `save_memeready()` thêm `gifDuration`/`audioDuration` vào
  `meta.json` (đo NGAY lúc lưu bộ meme sẵn, không phải đo lại mỗi lần
  `GET /memeready` — tránh gọi ffprobe thừa cho file không đổi).
- **`index.html`**: `modalUploadedMedia` (biến lưu file đang chọn trong
  modal soạn step) giờ có thêm field `duration` — set ở cả 2 nơi tạo ra nó
  (`uploadMediaFile()` đọc `data.duration` từ response `/upload`,
  `selectLibraryItem()` đọc `item.duration` từ response `/library`).
  `submitStepModal()` (nhánh `t === 'meme' || t === 'audio'`): nếu ĐANG TẠO
  MỚI (không phải sửa step cũ — sửa thì giữ nguyên duration đã có, có thể
  người dùng đã tự chỉnh) VÀ có `modalUploadedMedia.duration`, dùng nó thay
  `DEFAULT_DUR[t]`. Sau đó, RIÊNG với `type==='audio'` VÀ `parallel===true`
  VÀ đang tạo mới: ghi đè LẦN NỮA bằng `steps[steps.length-1].duration`
  (duration của step NGAY TRƯỚC nó — đúng step nó "bám theo", theo comment
  sẵn có ở checkbox "song song") — ưu tiên cao hơn cả duration thật của
  chính file audio, vì mục đích song song là phát đủ suốt lúc step trước
  (thường là meme) chạy.
- **`selectMemereadyItem()`** (mục 5.19/5.22): `memeDuration =
  item.gifDuration ?? DEFAULT_DUR.meme` — dùng CHUNG cho cả 2 step tạo ra
  (meme lẫn audio đi kèm), đảm bảo 2 step luôn bằng nhau ngay từ lúc tạo,
  đúng ý "chạy song song thì bằng thời gian nhau".
- Verify qua CDP: (1) API `/library/meme` trả đúng `duration:3.12` khớp
  `ffprobe` chạy tay trên cùng file; (2) `/memeready` (POST) trả đúng
  `gifDuration:3.12`/`audioDuration:1.25` (2 giá trị KHÁC nhau — xác nhận
  đo đúng từng file riêng, không nhầm lẫn); (3) chọn từ Meme sẵn qua UI —
  cả 2 step tạo ra có `duration:3.12` GIỐNG NHAU (không phải audio giữ
  `1.25` của chính nó); (4) soạn RIÊNG 2 step (Meme rồi Âm thanh, tick song
  song) — step meme có `duration:3.12` (đúng gif thật), step audio tự đồng
  bộ theo đúng `3.12` đó dù file gốc chỉ dài `1.25`s.

### 5.24. CRUD đầy đủ cho Meme sẵn + mô tả text (chuẩn bị tự động hoá bằng AI ngoài)

Người dùng muốn: (1) sửa/xoá được 1 bộ meme sẵn đã lưu (trước đây `DELETE
/memeready/{id}` đã có ở server nhưng KHÔNG có nút nào gọi nó trong UI —
tính năng "chết", không dùng được); (2) thêm trường MÔ TẢ text cho cả gif
lẫn audio — không phải để hiển thị cho người xem, mà để **copy-paste gửi
cho 1 công cụ AI khác** (ChatGPT/Claude ngoài app) khi nhờ AI gợi ý/chọn
meme phù hợp với 1 đoạn nội dung cụ thể đang soạn, rồi người dùng tự đối
chiếu gợi ý đó với `label` để chọn tay trong app. **App KHÔNG tự gọi AI
nào** — chỉ đóng vai trò lưu trữ dữ liệu có cấu trúc (mô tả) để người dùng
tự động hoá bước "tìm meme phù hợp" bằng công cụ AI của riêng họ.

- **`media_server.py`**: `MemereadySaveRequest` thêm `gifDescription`/
  `audioDescription` (mặc định rỗng, không bắt buộc). `save_memeready()`
  lưu 2 field này vào `meta.json`. Thêm class `MemereadyUpdateRequest`
  (label/gifDescription/audioDescription đều `| None`, chỉ field nào có
  mặt trong request mới ghi đè — cho sửa từng phần) và endpoint
  `PATCH /memeready/{item_id}` — đọc `meta.json` cũ, cập nhật field được
  gửi, ghi lại. **CHỦ Ý KHÔNG cho đổi file gif/audio qua PATCH** — đổi file
  coi như tạo bộ mới + xoá bộ cũ (giữ đơn giản, không hỗ trợ "thay file
  trong bộ có sẵn" vì phức tạp hoá không cần thiết cho nhu cầu thực tế).
- **`index.html`**: `memeSoundFieldHtml()` (modal soạn Meme thường, phần
  "Lưu thành meme sẵn") đổi từ dùng `prompt()` đơn lẻ cho label sang bọc
  trong `<details>` với 3 ô: input tên gọi (`m-memeready-label`), textarea
  mô tả gif (`m-memeready-gif-desc`), textarea mô tả audio
  (`m-memeready-audio-desc`) — đủ chỗ viết mô tả dài, không bị giới hạn như
  `prompt()`. `saveAsMemeready()` đọc từ 3 ô này thay vì gọi `prompt()`.
- **Modal "🎁 Meme sẵn"** (`memereadyItemHtml()`): mỗi item thêm 2 nút nổi
  góc trên phải (`.lib-item-edit-actions`, CSS `position:absolute`) — "✎
  Sửa" gọi `editMemereadyItem()`, "🗑 Xoá" gọi `deleteMemereadyItem()`. CẢ
  HAI đều `event.stopPropagation()` trong `onclick` — BẮT BUỘC, vì cả item
  cha CÓ SẴN `onclick="selectMemereadyItem(this)"` (click vào bất kỳ đâu
  trong item = chọn dùng ngay, thêm step vào kịch bản); thiếu
  `stopPropagation` sẽ vô tình thêm step mỗi khi chỉ định sửa/xoá.
  `title` của mỗi item đổi từ chỉ `label` sang ghép `gifDescription` +
  `audioDescription` (fallback `label` nếu cả 2 đều rỗng) — hover vào item
  trong lưới là thấy ngay mô tả đầy đủ, không cần mở sửa mới xem được.
- **`editMemereadyItem()`**: dùng 3 `prompt()` liên tiếp (label → mô tả gif
  → mô tả audio, bỏ qua bước 3 nếu item không có audio) — đơn giản, đủ
  dùng vì đây là thao tác không thường xuyên. Cancel ở bước nào thì field
  đó giữ nguyên (không gửi lên server field đó — dựa vào cơ chế PATCH chỉ
  ghi đè field có mặt). `deleteMemereadyItem()` xác nhận qua `confirm()`
  trước khi gọi `DELETE`.
- Verify: (1) qua `curl` xác nhận trọn vẹn luồng POST→PATCH→GET→DELETE,
  PATCH chỉ ghi đè đúng field được gửi (field khác giữ nguyên); (2) qua CDP
  thật — nút sửa/xoá tồn tại, click sửa cập nhật đúng ngay trong lưới
  (không cần reload trang), click sửa/xoá KHÔNG kích hoạt
  `selectMemereadyItem` (modal vẫn mở, `steps.length` không đổi — xác nhận
  `stopPropagation` chặn đúng bubbling), click xoá gỡ đúng item khỏi lưới,
  2 bộ meme sẵn thật khác của người dùng không bị ảnh hưởng.

### 5.25. Mô tả + CRUD cho thư viện gốc (audio/gif độc lập, không qua memeready)

Mục 5.24 chỉ cho mô tả/CRUD file ĐÃ đóng gói trong `memeready/`. Người dùng
muốn thêm: âm thanh ĐỘC LẬP (chưa gắn với meme nào, nằm thẳng trong
`threads/audio/`, dùng cho step Âm thanh thường) cũng cần mô tả + sửa/xoá
được, với cùng mục đích như mục 5.24 — dữ liệu có cấu trúc để dán cho AI
ngoài app tự chọn nội dung phù hợp.

**Quyết định lưu trữ (đã hỏi & chốt)**: 1 file JSON PHẲNG duy nhất
`_meta.json` đặt NGAY TRONG mỗi thư mục thư viện (`threads/audio/_meta.json`,
`threads/gif/_meta.json`), map `tên file → {label, description}`. Lý do
chọn cách này thay vì sidecar file riêng từng item: đơn giản, không đổi cấu
trúc thư mục hiện có (người dùng vẫn tự thả file vào bằng Finder như trước
giờ), dễ mở bằng tay để xem/backup, và đặc biệt dễ để dán NGUYÊN VĂN cho AI
đọc (1 khối JSON phẳng, không phải nhiều file rời rạc). File chưa có entry
trong `_meta.json` vẫn hiện bình thường trong thư viện — chỉ thiếu mô tả,
không bắt buộc phải điền.

- **`media_server.py`**: `LIBRARY_META_FILES` map category → đường dẫn
  `_meta.json` tương ứng. `_load_library_meta(category)`/
  `_save_library_meta(category, meta)` — đọc/ghi JSON, lỗi đọc (file hỏng/
  chưa tồn tại) trả `{}` thay vì raise (không chặn thư viện hoạt động).
  `GET /library/{category}` gộp thêm `label`/`description` vào mỗi item
  (fallback `label = p.stem` nếu chưa có entry); loại trừ TƯỜNG MINH
  `_meta.json` khỏi danh sách file trả về (so `p.name != "_meta.json"`,
  không chỉ dựa vào quy ước "bỏ file bắt đầu bằng dấu chấm" — 2 quy ước
  khác nhau, dễ nhầm nếu chỉ đọc lướt code). Thêm
  `PATCH /library/{category}/{filename}` (sửa label/description, không
  đụng file thật — giống hệt pattern `PATCH /memeready/{id}` mục 5.24) và
  **`DELETE /library/{category}/{filename}`** (xoá HẲN file khỏi ổ đĩa —
  khác `DELETE /memeready/{id}` chỉ xoá bản COPY, đây là file GỐC duy nhất,
  hành động không thể hoàn tác; dọn luôn entry trong `_meta.json` nếu có,
  tránh rác tích luỹ theo thời gian khi file bị xoá).
- **`index.html`**: `libraryItemHtml()` (dùng trong modal soạn step Meme/
  Âm thanh thường — KHÁC modal "Meme sẵn" riêng của mục 5.22) hiện
  `item.label || item.filename` thay tên file thô làm tên hiển thị,
  `title` = `item.description || displayLabel` (hover thấy mô tả đầy đủ).
  Thêm nút sửa/xoá (`editLibraryItem()`/`deleteLibraryItem()`) giống hệt
  pattern `editMemereadyItem()`/`deleteMemereadyItem()` mục 5.24 — đọc
  `item.category` (field mới gắn kèm vào `data-item` JSON lúc build HTML,
  vì `libraryItemHtml()` vốn không tự biết category của chính nó) để gọi
  đúng endpoint. `deleteLibraryItem()` cảnh báo RÕ trong `confirm()`: "File
  thật sẽ bị xoá khỏi ổ đĩa, không thể hoàn tác" — khác cảnh báo chung
  chung của `deleteMemereadyItem()`, vì đây là hành động phá huỷ mạnh hơn
  (không có bản backup nào khác).
- **CSS**: gỡ scope `.lib-item-memeready` khỏi rule `.lib-item-edit-actions`
  (mục 5.24 chỉ áp cho modal Meme sẵn) — đổi thành áp dụng chung cho mọi
  `.lib-item-meme` (grid ảnh, cần `position:absolute` góc trên phải) VÀ
  thêm rule riêng cho `.lib-item-audio` (layout flex hàng ngang có sẵn, nút
  chỉ cần `flex-shrink:0` nằm cạnh phải tên, không cần absolute).
- Verify: (1) `curl` — tạo/sửa/xoá 1 file audio test qua đúng chu trình,
  xác nhận `_meta.json` cập nhật đúng, entry bị dọn sau xoá, 18 file audio
  thật khác không bị ảnh hưởng; (2) CDP thật qua modal soạn Âm thanh — nút
  sửa/xoá hoạt động, sửa cập nhật ngay tên hiển thị + tooltip, không kích
  hoạt chọn nhầm (`steps.length` không đổi), xoá gỡ đúng item VÀ xoá đúng
  file thật khỏi đĩa (`ls` xác nhận file không còn tồn tại sau khi gọi
  DELETE qua UI, không chỉ qua API trực tiếp).

### 5.26. Trang công cụ Cắt/Crop video — `video_tool.html`

Người dùng cần crop khung hình + trim thời gian video (chuẩn bị video
nền/meme) — QuickTime không crop được, không muốn dùng iMovie. Trang MỚI
độc lập, mở tab riêng tại `http://localhost:8010/static/video_tool.html`
(nút "✂️ Cắt / Crop video" trong mục 1 sidebar index.html mở ra).

**Kiến trúc**:
- `media_server.py` mount `StaticFiles` vào `/static/` (copy pattern
  export_server.py — không cần http server rời, bài học ERR_CONNECTION_
  REFUSED mục 5.17) + 2 endpoint mới: `POST /video-process`
  (`{filename, trimStart, trimEnd, crop:{x,y,w,h}|null}` — filename là
  file đã upload qua `/upload` CÓ SẴN; dùng `Path(...).name` chặn path
  traversal; job nền bằng `threading.Thread` vì endpoints server này đều
  sync def, không trộn asyncio như export_server) và
  `GET /video-process-status/{job_id}` (poll pattern giống export).
- FFmpeg: `-ss/-to` TRƯỚC `-i` (seek keyframe nhanh rồi decode chính xác
  — chuẩn ffmpeg hiện đại, quan trọng với video dài); `-vf crop=w:h:x:y`
  nếu có crop; LUÔN re-encode libx264 (crop bắt buộc re-encode, giữ 1
  đường code duy nhất kể cả khi chỉ trim — cắt chính xác từng frame,
  không kẹt keyframe như stream-copy); server clamp/ép chẵn crop LẦN NỮA
  dù client đã làm (không tin số client gửi — có thể lệch do làm tròn
  scale hiển thị). Output lưu THẲNG vào `threads/media/` → URL
  `/media/{out}` vừa tải về vừa dùng ngay trong app làm video nền/meme.
- `video_tool.html` (trang tự chứa, cùng bộ màu app): preview + crop +
  trim chạy HOÀN TOÀN client-side qua `URL.createObjectURL(file)` —
  KHÔNG upload gì cho tới khi bấm Xuất (video dài không tốn băng thông
  lúc đang ướm thử).
- **Trim filmstrip kiểu iPhone/QuickTime** (yêu cầu tường minh của người
  dùng kèm ảnh mẫu — "đừng có mỗi thời gian, khó dùng lắm"): dải ~12
  thumbnail khung hình THẬT (1 `<video>` ẩn cùng source, seek tuần tự →
  `drawImage` vào canvas tile, vẽ kiểu object-fit:cover — thuần client,
  không cần endpoint thumbnail); vùng chọn viền vàng `#f5c518` với 2 tay
  cầm kéo được (border-left/right 14px làm thân tay cầm), vùng ngoài phủ
  tối; kéo tay cầm nào thì video chính `currentTime =` đúng thời điểm đó
  (thấy ngay frame tại điểm cắt); kéo THÂN vùng chọn dịch cả đoạn giữ
  nguyên độ dài; nút phát thử chỉ chạy trong đoạn chọn (tự dừng ở
  trimEnd qua `timeupdate`).
- **Crop kéo-thả**: khung `#crop-box` overlay trên video, 4 góc resize +
  kéo thân di chuyển, dim vùng ngoài bằng `box-shadow: 0 0 0 9999px
  rgba(0,0,0,.55)` (1 element thay vì 4 tấm overlay); preset "9:16" đặt
  khung dọc cao nhất căn giữa; quy đổi px hiển thị → px nguồn qua
  `video.videoWidth / wrapRect.width` (browser và FFmpeg autorotate cùng
  tính rotation metadata nên khớp nhau, không cần xử lý video quay dọc
  riêng); hiện kích thước px NGUỒN thật trong toolbar.

**Bug thật phát hiện qua test CDP (kéo chuột thật bằng
`Input.dispatchMouseEvent`, không phải synthetic JS event)**: vạch trang
trí giữa tay cầm (`#strip-sel::before/::after`, positioned absolute) —
`::after` là "con CUỐI" trong tree order nên vẽ ĐÈ LÊN `.grip.right`
(positioned z-auto: đứa sau vẽ trên), vạch 3px nằm đúng tâm tay cầm phải
CƯỚP click → event target rơi vào `#strip-sel` (body-drag, với vùng chọn
full-width thì không dịch được gì) → kéo tay phải hoàn toàn không ăn;
tay TRÁI lại hoạt động bình thường vì `::before` là "con ĐẦU", thua
`.grip.left` trong painting order — bug bất đối xứng rất khó đoán nếu
không debug bằng `elementFromPoint` + logger pointerdown. Sửa:
`pointer-events: none` cho cả 2 pseudo. Bài học test: viewport headless
mặc định 600px cao — strip nằm dưới fold, toạ độ chuột CDP
(viewport-relative) trượt ra ngoài → phải
`Emulation.setDeviceMetricsOverride` trước khi giả lập kéo.

**Verify**: (API) upload video test 5s → trim [1→3] + crop 200x200@
(100,50) → ffprobe output đúng 2.0s/200x200, frame output khớp pixel với
frame nguồn crop cùng vùng (so cạnh nhau bằng mắt); (UI CDP) nạp video
qua base64→File, filmstrip render đủ 12 tile không trắng, kéo tay phải
→ trimEnd=3.0 + video seek theo, kéo tay trái → trimStart=1.0, crop
preset 9:16 → 270×480 px nguồn, bấm Xuất → poll → link tải hiện, file
tải về đúng 270×480/2.0s, frame giữa là khung dọc crop đúng vùng;
(regression) `/upload`, `/library/*`, `/memeready` vẫn 200 sau khi mount
static.

### 5.27. Meme dạng video có sẵn audio bị mất tiếng khi xuất

Người dùng chuyển hướng: thay vì ghép gif+audio rời (dễ lệch, mục 5.19),
dùng THẲNG 1 file mp4 đã có sẵn tiếng (vd clip cười có tiếng gốc) làm
step meme — đỡ phải khớp 2 file riêng. Nhưng xuất video ra thì đoạn meme
đó câm, không có tiếng dù file gốc chắc chắn có audio track.

**Nguyên nhân**: `build_ffmpeg_cmd()` xử lý meme ĐỘNG (video/gif — mục
5.17) chỉ dùng `[{idx}:v]` (video stream) của input để overlay hình,
HOÀN TOÀN không đụng tới audio stream của chính file đó. Việc thu thập
`audio_tracks` (vòng lặp trong `run_export_job()`) chỉ có nhánh cho
`post`/`comment`/`reply` (TTS) và `type=='audio'` (hiệu ứng) — thiếu hẳn
nhánh cho `type=='meme'`. Đây là lỗ hổng thiết kế có từ lúc viết tính
năng meme động (mục 5.17), không phải do các lần sửa gần đây — chỉ lộ ra
khi người dùng thực sự dùng 1 mp4 có tiếng làm meme (trước đó luôn test
bằng gif, vốn không có audio nên không thể lộ bug này).

**Sửa** (`export_server.py`):
- Thêm `has_audio_stream(path)` — `ffprobe -select_streams a` kiểm tra có
  audio track không; trả `False` ngay cho `.gif` (không cần probe — gif
  chưa từng có audio, đỡ 1 lệnh subprocess thừa cho trường hợp phổ biến
  nhất).
- Vòng lặp thu thập `audio_tracks` trong `run_export_job()` thêm nhánh
  `elif s.get("type") == "meme" and s.get("mediaIsVideo")`: resolve
  `mediaUrl` → path thật, nếu `has_audio_stream()` true thì thêm vào
  `audio_tracks` với offset = `s["_start"]` **của STEP GỐC** (không phải
  `t0` của segment — 1 step meme có thể bị `compute_breakpoints()` chia
  thành NHIỀU segment nếu chồng lấp thời gian với step khác đang cộng
  dồn, dùng nhầm `t0` sẽ lặp audio nhiều lần hoặc offset sai).
- Kết quả trong `build_ffmpeg_cmd()`: file input mp4 xuất hiện ĐÚNG 2 LẦN
  trong lệnh FFmpeg — 1 lần với `-stream_loop -1 -itsoffset ... -t ...`
  cho nhánh video overlay (đã có từ mục 5.17), 1 lần input thường cho
  nhánh `[{idx}:a]adelay=...` audio riêng (bug thật đã gặp lúc đầu tưởng
  nhầm là "trùng input lãng phí" nhưng đây chính là cách đúng — FFmpeg
  filter_complex xử lý video/audio của cùng 1 input theo 2 luồng độc lập,
  không có cách "tách kênh" từ input đã bị `-itsoffset` riêng cho video
  mà không ảnh hưởng track audio còn nguyên timeline gốc).
- Verify qua export thật: kịch bản 1 post (0-2s) + 1 meme video có tiếng
  gốc (2-5s) — `volumedetect` đoạn 0-2s gần như im lặng tuyệt đối
  (`-70.3dB`), đoạn 2-5s có tín hiệu rõ ràng (`-24.4dB` mean, `-3.2dB`
  max) — xác nhận audio đúng vị trí, không lệch/không phát toàn video.

**Bug thứ 2, NGHIÊM TRỌNG HƠN, phát hiện ngay sau khi người dùng test bản
vá trên vẫn báo "vẫn không có tiếng"**: người dùng không dùng "＋ Meme"
thường (test ban đầu của tôi dùng đường này) mà dùng **Meme sẵn** (mục
5.19 — file lưu trong `threads/memeready/<id>/`, URL dạng
`/memeready-file/{id}/{filename}`). `_resolve_local_media()` — hàm DUY
NHẤT chuyển URL HTTP thành path thật trên đĩa, dùng cho CẢ audio track
(bug 1 vừa sửa ở trên) LẪN video overlay của meme động (`dm["_local_path"]`,
mục 5.17) — chỉ nhận dạng 2 kiểu URL: `/media/` và `/library-file/`.
KHÔNG CÓ nhánh nào cho `/memeready-file/` — vì tính năng "meme sẵn"
(mục 5.19) được thêm SAU KHI `export_server.py` đã viết xong, chưa từng
được cập nhật để biết về thư mục `memeready/`. Hậu quả: `_resolve_local_media()`
luôn trả `None` cho mọi file trong memeready → không chỉ audio bị bỏ qua,
mà `build_ffmpeg_cmd()` (dòng `if not dm or not dm.get("_local_path"):
continue`) còn bỏ qua LUÔN CẢ OVERLAY VIDEO — nghĩa là hình ảnh của bất kỳ
"meme sẵn" nào dạng video/gif động lẽ ra CŨNG không hiện trong video xuất,
không chỉ riêng thiếu tiếng (may mắn người dùng chỉ để ý thấy mất tiếng vì
video nền + card che gần hết khung, có thể hình meme thật ra vẫn từng bị
thiếu mà chưa ai nhận ra).

**Sửa**: thêm `MEMEREADY_DIR = BASE_DIR / "memeready"` và nhánh thứ 3 vào
`_resolve_local_media()` — parse `/memeready-file/{id}/{filename}`, ghép
`MEMEREADY_DIR / item_id / fname`, kiểm tra tồn tại + path nằm đúng trong
`MEMEREADY_DIR` (chặn path traversal, cùng pattern các nhánh khác). Verify
qua export thật với ĐÚNG bộ meme sẵn thật của người dùng
(`/memeready-file/b85e588260/gif.mp4`, mp4 có tiếng gốc): ffmpeg command
giờ resolve đúng path `threads/memeready/b85e588260/gif.mp4` (thay vì bị
`continue` bỏ qua) cho cả overlay video lẫn track audio;
`volumedetect` xác nhận đúng im lặng/có tiếng đúng đoạn; trích frame giữa
đoạn meme xem trực tiếp bằng mắt xác nhận hình hiện đúng, đúng vị trí.

### 5.28. Meme video vẫn câm trong PREVIEW (khác 2 bug export ở mục 5.27)

Sau khi sửa cả 2 bug xuất video (mục 5.27), người dùng vẫn báo mất tiếng —
hoá ra lần này KHÔNG liên quan tới export, mà là **preview trong app**
(trước khi bấm Xuất). Thẻ `<video>` render meme trong `renderAtTime()`
(`index.html`) luôn gắn cứng thuộc tính `muted` — cố ý từ lúc thiết kế
ban đầu (mục 5.5: video meme chỉ để xem hình, âm thanh hiệu ứng xử lý
riêng qua step Âm thanh) — nhưng giờ người dùng hay dùng thẳng 1 file mp4
đã có sẵn tiếng làm meme (thay vì ghép gif+audio rời, mục 5.19: "gif với
audio nó không khớp nên dùng file mp4 có âm thanh sẵn luôn"), `muted`
khiến preview câm bất kể nguồn file nào (kéo-thả, thư viện, hay chọn từ
Meme sẵn — cùng đi qua đúng 1 dòng code này).

**Sửa**: bỏ `muted` khỏi `<video>` meme (`index.html`, dòng render
`memeEl.innerHTML`) — giữ nguyên `autoplay loop playsinline`. Lưu ý
KHÔNG sửa `export_render.html` (trang Playwright chụp ảnh, không phát âm
thanh gì nên `muted` ở đó vô hại, sửa cũng không ảnh hưởng).

Verify qua CDP: tạo step meme trỏ tới file mp4 có tiếng thật, đợi
autoplay, đọc thuộc tính `<video>` — `muted:false`, `paused:false`,
`currentTime` tăng dần theo thời gian thực (0 → 0.35s sau ~1s) — xác
nhận video đang thực sự phát, không bị autoplay policy trình duyệt chặn
âm thầm (Chrome nới lỏng chính sách này sau khi trang có tương tác người
dùng thật, đúng trải nghiệm thực tế khi thao tác qua nhiều bước mở
modal/chọn file trước khi tới lúc xem preview).

### 5.30. Step "Ảnh" — ảnh phủ giữa khung + TTS phát đúng lúc ảnh hiện

Người dùng muốn 1 loại step mới: ảnh tĩnh phủ giữa khung 9:16 (như meme)
kèm ô nhập chữ để TTS đọc đúng lúc ảnh hiện lên — không phải card
Threads, không phải meme hài, mà dùng cho nội dung kiểu "ảnh minh hoạ +
lời thuyết minh". Đã hỏi & chốt: hiển thị/vị trí/xoay dùng CHUNG hẳn cơ
chế overlay đã có cho meme (mục 5.17/5.21), không thiết kế riêng.

**Model**: `type: 'image'` — có `mediaUrl/mediaPath/mediaName/mediaIsVideo`
(giống meme, chọn qua `filePathFieldHtml` category `'meme'` — dùng chung
thư viện ảnh/gif/video `threads/gif/`) VÀ `text/ttsText/voiceIdx/audioUrl/
ttsDuration` (giống post, 1 khối duy nhất — KHÔNG có ô "nội dung hiển thị"
tách biệt như post/comment vì ảnh không hiển thị text nào, chỉ có đúng 1
ô `m-text` vừa là nội dung vừa là script đọc). `DEFAULT_DUR.image = 3`.
Bỏ trống chữ đọc là hợp lệ (giống meme cho phép bỏ trống file để test
thời gian) — chỉ hiện ảnh câm.

**Nguyên tắc thiết kế cốt lõi — TÁI DÙNG, không viết logic mới**: vì
"image" và "meme" đều là "overlay phủ giữa khung, cắt đứt chuỗi cộng
dồn", mọi chỗ trong codebase kiểm tra `s.type === 'meme'` để quyết định
"đây có phải overlay không" đều cần mở rộng thành
`(s.type === 'meme' || s.type === 'image')` — KHÔNG đổi tên biến/field
(`memeStep`, `#meme-overlay`, `hasMeme`...) dù giờ mang ý nghĩa rộng hơn
"overlay phủ giữa khung nói chung", tránh renaming lan rộng không cần
thiết qua nhiều file (`index.html`, `render_shared.js`,
`export_render.html`, `export_server.py`).

- `render_shared.js:computeSceneAt()` — 2 chỗ: điều kiện cắt chuỗi cộng
  dồn (`if (s.clearBefore || s.type === 'meme' || s.type === 'image')
  break;`) và điều kiện tìm `memeStep` sống tại `t`. Sau khi sửa,
  `export_render.html`/`renderStackAt()` tự động hoạt động đúng cho
  `image` MÀ KHÔNG CẦN SỬA GÌ THÊM — nó chỉ đọc `memeStep` (biến đã
  generic) qua `isDynamicMeme()`/`memeOverlayTransform()`, không kiểm tra
  `.type` trực tiếp; ảnh luôn `mediaIsVideo=false` + không phải `.gif`
  nên `isDynamicMeme()` tự trả `false`, tự động đi đúng đường "chụp PNG
  tĩnh" như ảnh meme tĩnh trước đây.
- `export_server.py:compute_breakpoints()` — thêm `"image"` vào tuple
  loại step lấy mốc `_start`/`_end`.
- `export_server.py:run_export_job()` (thu thập `audio_tracks`) — thêm
  `"image"` vào điều kiện đầu (`type in ("post","comment","image")`) để
  TTS của step ảnh cũng được đọc từ `TTS_AUDIO_DIR` và ghép vào audio
  track khi xuất video.
- `index.html`: `TYPE_LABEL`/`DEFAULT_DUR` thêm entry `image`; nút "＋
  Ảnh" trong `.add-step-bar`; case `'image'` trong `openStepModal()`
  (kết hợp `filePathFieldHtml` + ô `m-text` + `voiceFieldHtml`, KHÔNG
  dùng `ttsFieldHtml`/`bindTtsAutoSync` vì không có 2 ô tách biệt để
  đồng bộ — chỉ 1 ô, `readTtsText('m','m-text')` tự fallback đúng khi
  không có `m-tts-text`); `submitStepModal()` thêm `'image'` vào nhánh
  đọc media (`t==='meme'||t==='audio'||t==='image'`) và nhánh gọi TTS
  (`t==='post'||...||t==='image'`, có biến `skipImageTts` để không gọi
  TTS khi bỏ trống chữ); `stepSummary()`/`POSITIONABLE_TYPES`/`typeCls`
  thêm `image` để hiện đúng trong panel step + có d-pad chỉnh vị trí/xoay
  (mục 5.21) + không bị `renderStepList` bỏ sót.

**Bug 1 phát hiện qua test (không phải do image gây ra — lộ ra lỗi có
sẵn từ mục 5.23)**: logic "duration mặc định = độ dài thật của file" chỉ
được viết cho video/gif (giả định ffprobe trả `None` cho ảnh tĩnh "không
có thời lượng"). Thực tế `ffprobe` trả duration ~0.04s cho JPG/PNG (không
phải `None` — là "container duration" gần-0 vô nghĩa của định dạng ảnh).
Áp dụng vô điều kiện sẽ ép `duration` step meme/image dùng ảnh tĩnh về
gần 0 thay vì `DEFAULT_DUR` hoặc TTS duration thật — **bug tồn tại từ
trước với chính step Meme**, chỉ chưa ai gặp vì trước giờ luôn test bằng
gif/video, chưa ai chọn ảnh JPG làm meme. **Sửa**: thêm biến
`isTimedMedia` — `type==='audio'` luôn coi hợp lệ (file âm thanh thật);
`meme`/`image` CHỈ coi hợp lệ khi `mediaIsVideo` hoặc đuôi `.gif`, ảnh
tĩnh khác bị loại, duration rơi về DEFAULT_DUR/TTS như mong đợi.

**Bug 2 phát hiện SAU KHI xuất video đã đúng — preview trong app vẫn
câm**: `ttsClipAt()` (điều khiển audio phát trong preview tương tác,
KHÁC hẳn `audio_tracks` của export) chỉ có nhánh `type==='post'||
type==='comment'` và `type==='reply'` — thiếu hẳn `image`, nên dù step
đã có `audioUrl` hợp lệ (TTS tạo thành công, hiện đúng duration),
`syncTtsAudio()` không bao giờ tìm thấy clip nào để gán vào
`#tts-audio`, preview phát câm hoàn toàn trong khi export lại đúng (2 cơ
chế hoàn toàn tách biệt, sửa export không tự động sửa preview). **Sửa**:
thêm `type === 'image'` vào nhánh 1-khối-audio của `ttsClipAt()` (dùng
chung với post/comment). Cũng thêm `image` vào điều kiện lọc của
`generateAllTts()` ("Tải tất cả TTS còn thiếu", mục 5.6) để nút đó áp
dụng được cho step ảnh chưa có giọng đọc.

**Verify**: (1) CDP — modal "ẢNH" mở đúng có đủ ô TTS + chọn file; tạo
step với TTS tắt → `duration=DEFAULT_DUR.image=3`; `computeSceneAt` nhận
diện `image` là overlay; panel hiện đúng summary + d-pad vị trí; (2) TTS
thật + ảnh tĩnh JPG (duration file ~0.04s) → step duration đúng bằng độ
dài giọng đọc (1.872s), KHÔNG bị ép về 0.04s; (3) export thật — ảnh hiện
đúng giữa khung theo đúng vị trí, `volumedetect` xác nhận có tiếng đúng
lúc; (4) preview thật qua `togglePlay()` — `ttsClipAt()` trả đúng clip,
`#tts-audio` có `src` đúng + `currentTime` tăng dần theo thời gian thực
(0.72s → 1.44s sau ~0.8s), xác nhận đang phát thật, không chỉ có data mà
im lặng.

### 5.31. Nút "Dọn cache" — xoá file dùng-1-lần, giữ lại thư viện tái dùng

Dự án làm nặng máy dần theo thời gian vì 3 thư mục tích rác không tự dọn:
`threads/media/` (722M lúc phát hiện — ảnh/video upload qua dropzone +
output của video-process/cắt-crop, mục 5.26), `threads/exports/` (274M — mỗi
lần xuất video tạo 1 thư mục job riêng, cố ý không tự xoá để tránh mất file
chưa kịp tải, xem TODO cũ mục 6), `threads/tts_audio/` (9.6M — mp3 sinh mỗi
lần soạn step có TTS). Người dùng xác nhận **không cần giữ 3 thư mục này**
vì làm xong 1 dự án video là bỏ luôn, không tái dùng — khác hẳn
`threads/memeready/` (bộ meme đóng gói), `threads/audio/` + `threads/gif/`
(thư viện gốc), `threads/avatarthreads/` (ảnh avatar giả cố định, mục 5.10)
— 4 thư mục này PHẢI giữ nguyên, không được đụng vào bởi tính năng dọn cache.

- **Backend** (`media_server.py`): hằng số `CACHE_DIRS` (~dòng 63) map tên
  → Path của đúng 3 thư mục cần dọn (`media`, `exports`, `tts_audio`) —
  thêm/bớt thư mục nào vào diện "dọn được" thì sửa Ở ĐÂY, đừng hardcode rải
  rác. `GET /cache-size` — cộng dồn `stat().st_size` mọi file trong 3 thư
  mục (kể cả file lồng trong thư mục con của `exports/<job_id>/`), trả tổng
  bytes để hiện cho người dùng biết đang chiếm bao nhiêu trước khi xoá.
  `DELETE /cache` — duyệt từng thư mục trong `CACHE_DIRS`, xoá sạch mọi
  entry bên trong (`shutil.rmtree` cho thư mục con như `exports/<job_id>/`,
  `unlink()` cho file lẻ), rồi `mkdir(exist_ok=True)` lại thư mục gốc (giữ
  thư mục tồn tại rỗng, không xoá luôn `media/`/`exports`/`tts_audio/` vì
  các route khác — `MEDIA_DIR`, `TTS_AUDIO_DIR` trong `tts_server.py`/
  `export_server.py` — giả định thư mục này luôn tồn tại sẵn). Trả về số
  lượng entry đã xoá (`removed`) để frontend báo lại người dùng.
- **Frontend** (`index.html`): mục sidebar mới, số 5 "Dọn cache" (sau mục 4
  "Ghép & xuất video"). `refreshCacheSize()` gọi `/cache-size` lúc trang vừa
  load (trong khối "Khởi tạo" cuối file, cùng chỗ gọi `renderStepList()`) và
  hiện dung lượng dạng người đọc được (`fmtBytes()` — B/KB/MB/GB tự chọn đơn
  vị); nếu `media_server.py` chưa chạy, hiện thông báo lỗi rõ ràng thay vì
  để trống hoặc crash. `clearServerCache()` bắt buộc `confirm()` trước khi
  gọi `DELETE /cache` (hành động không thể hoàn tác, xoá thật trên đĩa) —
  dialog cảnh báo rõ: video xuất chưa kịp tải hoặc TTS của step đang soạn dở
  sẽ mất, phải tạo lại nếu vẫn cần. Sau khi xoá xong, gọi lại
  `refreshCacheSize()` để cập nhật số hiển thị về 0 (hoặc gần 0 nếu vừa có
  job export/tts chạy song song).
- **Verify**: test logic xoá (duyệt + `rmtree`/`unlink` + `mkdir` lại) trên
  thư mục giả lập ngoài `threads/` (không đụng dữ liệu thật của người dùng)
  — xác nhận file lẻ và thư mục con (`exports/<job_id>/`) đều bị xoá sạch,
  3 thư mục gốc vẫn còn tồn tại (rỗng) sau khi xoá. `GET /cache-size` test
  qua server thật (port tạm, tránh đụng `media_server.py` đang chạy sẵn của
  người dùng ở 8010) — trả đúng tổng bytes khớp `du -sh` thủ công (~1GB lúc
  test, khớp `media` 722M + `exports` 274M + `tts_audio` 9.6M). **Chưa** gọi
  `DELETE /cache` trên dữ liệu thật trong phiên làm việc thêm tính năng này
  — để người dùng tự bấm nút khi sẵn sàng, tránh xoá nhầm file đang cần.

**Cập nhật quan trọng — Dọn cache xoá THẬT, kể cả file đang được step trong
kịch bản hiện tại tham chiếu, KHÔNG có cách cứu qua JSON**: người dùng gặp
thật đúng tình huống này — dùng tính năng "Cắt nhiều vùng từ 1 ảnh gốc"
(mục 5.31 cũ/nay đổi số, xem phần "Cắt ảnh" trong `index.html`) tạo step
`image`, ảnh crop được upload lên `threads/media/`, rồi bấm "🗑️ Xoá cache"
— ảnh đó nằm trong diện bị xoá (không có cơ chế "giữ lại file đang dùng",
ĐÚNG THIẾT KẾ, người dùng xác nhận muốn dọn sạch vô điều kiện, không muốn
logic "thông minh" giữ lại có chọn lọc). Xuất JSON TRƯỚC lúc xoá rồi nạp
LẠI SAU khi xoá — người dùng thắc mắc tại sao preview vẫn hiện đúng ảnh dù
file đã mất trên server.

**Giải thích (không phải bug, là cache trình duyệt đánh lừa)**: JSON chỉ
lưu `mediaUrl` (URL trỏ tới file trên `media_server.py`, xem mục 5.9/8) —
KHÔNG lưu ảnh gốc, KHÔNG lưu toạ độ vùng crop, không có cách "dựng lại" ảnh
từ JSON. `importProjectJson()` chỉ gán `<img src=mediaUrl_cũ>` vào DOM,
không kiểm tra file server còn tồn tại không. Ảnh vẫn hiện được trong tab
đó vì Chrome cache HTTP response theo URL (FastAPI `FileResponse` — mục
5.12 — không set `Cache-Control` tường minh nhưng heuristic cache của
Chrome/`Last-Modified` vẫn có thể phục vụ lại từ đĩa cục bộ mà không hỏi
lại server) — đây là cache PHÍA MÁY NGƯỜI DÙNG, không phải server phục hồi
được gì. Bằng chứng file đã mất thật: `curl http://localhost:8010/media/
<tên file>` trả 404 dù preview trong tab vẫn hiện ảnh bình thường.

**Hệ quả thực tế quan trọng nhất — vì sao XUẤT VIDEO vẫn mất ảnh dù preview
vẫn thấy**: `export_server.py` dùng Playwright mở `export_render.html`
trong 1 **Chromium instance HOÀN TOÀN RIÊNG** (mục 5.17) — không chia sẻ
cache HTTP với tab trình duyệt người dùng đang thao tác. Playwright luôn
phải fetch thật từ `media_server.py`, gặp 404, ảnh/step đó mất khi xuất —
đúng đúng triệu chứng người dùng báo ("preview có, xuất video mất").

**Kết luận, không có fix code nào cho việc này** (đây là giới hạn thiết kế
đã biết của cơ chế JSON export/import — mục 8 "KHÔNG có cách nào mang theo
file thật kèm theo nó", áp dụng luôn cho ảnh crop chứ không riêng video/
nhạc nền): nếu đã bấm Dọn cache, MỌI step tham chiếu file trong `media/`
(ảnh crop, ảnh/video/audio đã upload qua dropzone) coi như mất vĩnh viễn,
phải tạo lại thủ công (chọn lại ảnh gốc, kéo lại vùng crop) — dù JSON có
nạp lại "thành công" và preview có vẻ ổn do cache trình duyệt, ĐỪNG tin
vào preview sau khi đã Dọn cache — luôn xuất thử video thật để xác nhận.

### 5.32. Bug: meme dạng video (mp4) xuất ra bị "dẹp" — sai tỉ lệ khung hình

Người dùng báo: video xuất thật (không phải preview) đúng ổn ở mọi phần
(card Threads, video nền...) TRỪ meme dạng mp4 — bị bóp/dẹp hẳn so với hình
dạng thấy trong preview. Đây là bug MỚI, khác 2 bug cũ đã sửa ở mục 5.27
(mất tiếng)/5.28 (câm trong preview) — 2 bug đó về audio, bug này về hình.

**Nguyên nhân xác nhận bằng test trực tiếp** (script Playwright độc lập gọi
`setSteps`/`computeTimeline`/`renderStackAt` với step meme mp4 thật trong
`memeready/`, so tỉ lệ đo được với tỉ lệ gốc qua `ffprobe`): `waitImagesLoaded()`
(`export_render.html`, dùng để đợi ảnh load xong trước khi đo/chụp — mục
5.17) chỉ `querySelectorAll('img')` — **hoàn toàn bỏ qua `<video>`**. Với
meme dạng mp4, `renderStackAt()` chèn `<video autoplay muted loop>` vào
`#meme-overlay`, nhưng `Promise.all([waitImagesLoaded(stackEl),
waitImagesLoaded(memeEl), waitAnimationsDone(stackEl)])` không có gì đợi
`<video>` — `measureMemeBox()` (đo `getBoundingClientRect()` ngay sau đó để
tính `wRatio`/`hRatio` cho FFmpeg overlay) chạy khi `video.readyState` còn 0
(chưa có metadata), trình duyệt chưa biết `videoWidth`/`videoHeight` thật
nên box đo được sai lệch nặng — test thực đo được `hRatio=0.078` (kỳ vọng
`~0.284` cho video 16:9), tỉ lệ w:h tính ra `6.48` thay vì đúng `1.78`.
`build_ffmpeg_cmd()` (`export_server.py`) dùng đúng số liệu sai này để
`scale={w_px}:{h_px}` ép cứng khung video overlay → kết quả xuất ra bị bóp
dẹp theo chiều cao, khớp chính xác mô tả "dẹp" của người dùng.

**Sửa**: thêm `waitVideosMetadata(container)` (`export_render.html`, ngay
sau `waitAnimationsDone`) — duyệt mọi `<video>` trong container, đợi
`readyState >= 1` (`HAVE_METADATA`, đủ để có `videoWidth`/`videoHeight`,
KHÔNG cần đợi `canplay`/đủ frame để phát — nhanh hơn nhiều) qua event
`loadedmetadata`. Gọi thêm trong mảng `Promise.all` của `renderStackAt()`
cùng chỗ với `waitImagesLoaded`/`waitAnimationsDone`.

**Verify**: test lại đúng script trên sau khi sửa — `hRatio` ra `0.2848`
(kỳ vọng `0.2847`), tỉ lệ `1.7770` khớp gần tuyệt đối `1.7778` (16:9 gốc,
sai lệch 0.06% do làm tròn pixel bình thường, không phải bug). Test thêm 2
file mp4 khác trong `memeready/` (400×226 và 518×362, tỉ lệ khác hẳn nhau)
— cả 2 đều khớp tỉ lệ gốc trong sai số <0.1%. Chưa chạy full export bằng
FFmpeg thật để xem video cuối cùng (chỉ verify phần đo tỉ lệ — đầu vào của
`build_ffmpeg_cmd()` đã đúng thì scale theo đó cũng đúng, logic scale/overlay
mục 5.17 không đổi) — người dùng nên tự xuất thử 1 video có meme mp4 để xác
nhận hình ảnh cuối cùng đúng như preview.

### 5.33. Auto random số liệu tương tác (tim/cmt/repost/share)

Người dùng lười tự nghĩ số liệu hợp lý cho mỗi step (mục 5.6 vốn có 4 ô
nhập tay likes/comments/reposts/shares) — muốn chỉ chọn 1 mức độ rồi bấm 1
nút, số tự sinh ra hợp lý. Đã hỏi kỹ qua nhiều vòng `AskUserQuestion` để
chốt đúng công thức (ban đầu người dùng cho vài mốc rời rạc kiểu ước lượng
miệng, không phải khoảng chính xác — phải hỏi lại nhiều lần mới ra số cụ
thể không mâu thuẫn nhau).

**Công thức đã chốt** — `RANDOM_STATS_LEVELS` (`index.html`, ngay trên
`statsFieldsHtml`): 4 mức DÙNG CHUNG 1 thang cho post/comment/reply (không
có thang riêng nhỏ hơn cho comment — người dùng xác nhận rõ ràng ở bước
chốt cuối, dù ban đầu có nói comment "nên thấp hơn post nhiều"; quyết định
cuối cùng thắng quyết định đầu vì hỏi lại sau khi phát hiện mâu thuẫn giữa
các câu trả lời):
- Rất ít: 1.000 – 3.000 tim
- Ít: 3.000 – 10.000 tim
- Trung bình: 10.000 – 30.000 tim
- Cao: 30.000 – 50.000 tim

`rollRandomStats(levelKey)`: random tim đều trong khoảng của mức
(`randInt`), rồi suy ra 3 số còn lại theo đúng tỉ lệ người dùng mô tả:
- `comments = likes * random(7%, 9%)` — người dùng nói "cmt dựa vào tim mà
  ra, cỡ khoảng 8%" nên random nhẹ quanh 8% thay vì cố định cứng, tránh mọi
  step cùng mức ra đúng 1 tỉ lệ y hệt nhau (nhìn giả tạo).
- `reposts = comments * 1.4` — "đăng lại nhiều hơn cmt 40%".
- `shares = comments * random(1.10, 1.15)` — "share nhiều hơn cmt 10-15%".

`fmtStatNumber(n)`: format số > 1000 thành dạng `"12,3K"` (dấu phẩy thay
`.` cho phần thập phân, giống định dạng số Việt/Threads thật hiển thị vd
"1,2K" đã thấy trong mock — mục 4), số nguyên nếu là số tròn nghìn (vd
30000 → "30K" không phải "30,0K").

**UI**: `statsFieldsHtml(prefix, v)` (mục 5.6, dùng chung cho post/comment/
reply qua `prefix` — `m` cho khối 1, `m2` cho khối 2 của reply) thêm 1 hàng
mới phía trên 4 ô cũ: dropdown chọn mức (`#${prefix}-random-level`) + nút
"🎲 Random" (`onclick="rollAndFillStats('${prefix}')"`). Bấm nút chỉ ghi
đè GIÁ TRỊ 4 ô input hiện có (`#${prefix}-likes` etc.) — không đổi cấu trúc
lưu step, không thêm field mới vào `stats`/`stats2`, tương thích ngược
100% với step cũ đã tạo trước khi có tính năng này (mở sửa step cũ, 4 ô
vẫn hiện đúng giá trị đã lưu, dropdown mức mặc định ở lựa chọn đầu tiên
nhưng không tự ý random gì nếu không bấm nút). Vì mỗi khối `m`/`m2` của
reply có `<select>`/nút riêng biệt (id theo prefix), random khối comment
gốc và khối reply độc lập nhau — đã verify qua Playwright: chọn mức khác
nhau cho 2 khối, bấm random riêng, ra đúng khoảng của từng mức, không đụng
nhau.

**Verify**: Playwright mở `index.html` thật (qua `python3 -m http.server`,
không phải CDP giả lập) — (1) modal `post`: chọn mức "Cao" → likes ra
34,4K (trong khoảng 30-50K), comments/reposts/shares theo đúng tỉ lệ; chọn
"Rất ít" → 2,5K (trong khoảng 1-3K); (2) modal `reply`: 2 nút random độc
lập tìm đúng bằng `page.locator("button:has-text('🎲 Random')").nth(0/1)`
— khối `m` (mức "Trung bình") ra 11,9K, khối `m2` (mức "Cao") ra 38,4K,
đúng khoảng tương ứng, không lẫn lộn giữa 2 khối; (3) chụp ảnh modal xác
nhận layout dropdown + nút random không vỡ (dùng CSS `.row2`/`.btn-ghost`/
`select` có sẵn từ mục 5.6, không cần thêm CSS mới).

### 5.34. Bug: âm thanh video xuất ra nhỏ hẳn (phải bật max loa mới nghe)

Người dùng báo video xuất thật nghe nhỏ hơn hẳn preview, phải bật max loa
mới nghe rõ. Đo bằng `ffmpeg -af volumedetect` trên 1 video đã xuất trước
đó (`exports/a6b6fa1ffea7/output.mp4`): `mean_volume: -27.9dB` — thấp bất
thường.

**Nguyên nhân**: `build_ffmpeg_cmd()` (`export_server.py`) gộp mọi track
audio (TTS từng step + audio hiệu ứng + nhạc nền, mỗi cái 1 input riêng
qua `adelay`) bằng filter `amix`, KHÔNG set tường minh option `normalize` —
mặc định FFmpeg `amix` có `normalize=true`, tự động **chia biên độ output
theo TỔNG SỐ INPUT khai báo** (`inputs=N`) để chống clip, bất kể tại một
thời điểm có bao nhiêu track thực sự đang "kêu" cùng lúc. Kịch bản nhiều
step (mỗi step TTS/audio = 1 input riêng dù nhờ `adelay` đặt đúng lúc nên
hiếm khi 2 track chồng nhau thật) bị chia âm lượng rất mạnh dù không cần
thiết — test xác nhận bằng `ffmpeg -f lavfi sine=...` + `volumedetect`: mix
2 track với `normalize=1` (mặc định) ra `-21.1dB`, cùng 2 track với
`normalize=0` ra `-15.1dB` (chênh 6dB = giảm còn nửa biên độ nghe được).
Test lại trên audio giọng nói TTS thật (2 file `tts_audio/*.mp3` thật của
dự án, `adelay` lệch nhau 0.5s) cho kết quả tương tự: `-22.3dB` (cũ) →
`-18.8dB` (mới, +3.5dB).

**Sửa**: thêm `normalize=0` vào filter `amix` (giữ nguyên biên độ gốc từng
track, không tự chia theo N) + nối thêm `alimiter=limit=0.97` ngay sau
(chặn an toàn cho trường hợp hiếm — nhiều track cùng lúc thực sự chồng
nhau vượt quá full-scale, tránh clip méo tiếng thay vì tự động giảm âm
lượng cả video như `normalize=1` từng làm).

**Verify**: test filter riêng qua `ffmpeg -f lavfi` (tone sine, không cần
file thật) — cú pháp `amix=inputs=2:...:normalize=0,alimiter=limit=0.97`
chạy không lỗi, `max_volume` sau mix ở mức an toàn (-1.5dB tới -11.8dB tuỳ
test case, không vượt 0dB clip). Test trên audio TTS thật xác nhận tăng
đúng hướng (+3.5dB). **Chưa** chạy full export bằng job thật (chỉ test
filter riêng lẻ + trên input thật, không dựng lại toàn bộ pipeline
Playwright+FFmpeg tốn thời gian) — người dùng nên tự xuất thử 1 video để
xác nhận nghe đủ to mà không cần bật max loa.

### 5.35. Điều chỉnh lại tỉ lệ random số liệu — POST và COMMENT khác nhau

Sau khi dùng thử mục 5.33, người dùng phản hồi 2 vòng liên tiếp để tinh
chỉnh tỉ lệ cho sát thực tế Threads hơn (ban đầu 1 công thức chung 8% cho
mọi loại, hoá ra sai — post và comment cần tỉ lệ cmt/tim khác hẳn nhau vì
bản chất tương tác khác nhau nhiều). **Công thức CUỐI CÙNG** (đã chốt qua
2 lần chỉnh, không phải mục 5.33 gốc nữa — LUÔN đọc mục này thay vì 5.33
nếu có mâu thuẫn về con số):

- **Post** (`kind='post'`): `comments = likes × random(0.8%, 1.2%)` (1%
  ±0.2%).
- **Comment/Reply** (`kind='comment'`, dùng cho CẢ comment đơn lẫn 2 khối
  của reply — không có thang riêng nhỏ hơn nữa, chỉ tỉ lệ % khác):
  `comments = likes × random(0.17%, 0.23%)` (0.2% ±0.03%) — thấp hơn hẳn
  post, đúng thực tế 1 comment hiếm khi có nhiều reply bằng 1 post có
  nhiều comment.
- **Repost** (CẢ post lẫn comment/reply, không phân biệt): đổi hẳn cách
  tính — từ `comments × 1.4` (mục 5.33 cũ) sang **`likes × random(5%,
  5.5%)`** (tính thẳng theo TIM, không qua cmt nữa).
- **Share** (CẢ 2): vẫn tính theo cmt như cũ nhưng nới khoảng —
  `comments × random(1.10, 1.20)` (mục 5.33 cũ chỉ tới 1.15).

**Cách phân biệt `kind`**: `statsFieldsHtml(prefix, v, kind)` (mục 5.6)
nhận thêm tham số thứ 3 — do KHÔNG thể suy ra kind từ `prefix` (post và
comment đơn đều dùng chung prefix `'m'`, không có `'m2'`), phải truyền
tường minh từ `openStepModal()`: nhánh `type==='post'||type==='comment'`
gọi `statsFieldsHtml('m', ..., type==='post'?'post':'comment')`; nhánh
`type==='reply'` gọi CẢ 2 lần (`'m'` cho comment gốc, `'m2'` cho reply)
đều với `'comment'` — vì bản chất reply cũng là 1 dạng comment, tương tác
thấp như comment thường, không phải post.

**Verify**: gọi trực tiếp `rollRandomStats('high', 'post')` và `('high',
'comment')` qua `page.evaluate()` (Playwright, không qua UI) để đọc số
thô trước khi format — post ra tỉ lệ cmt/likes 2.9-3.1%... (đã sửa lại
lần 2 xuống đúng khoảng 0.8-1.2%, xem lại bằng Node standalone chạy đúng
công thức cuối: 3 lần roll `kind='post'` ra 0.800%/1.047%/0.806%, đều
trong khoảng kỳ vọng; 3 lần roll `kind='comment'` ra 0.190%/0.214%/0.193%,
đều trong khoảng 0.17-0.23%). Chưa test lại qua UI thật lần 2 (chỉ verify
logic hàm bằng Node) — về mặt code là cùng 1 hàm `rollRandomStats` UI gọi
trực tiếp, không có lớp trung gian nào có thể làm sai lệch thêm.

### 5.36. Cho phép crop lại ảnh khi SỬA step "Ảnh" + thêm 4 handle cạnh

Người dùng gặp đúng tình huống mất ảnh crop do Dọn cache (mục 5.31) và
muốn sửa lại step đó bằng cách crop ảnh MỚI — nhưng khối "✂️ Cắt nhiều
vùng" (mục 5.31 gốc — số bị trùng, xem ghi chú đầu mục 5.31) TRƯỚC ĐÂY chỉ
hiện khi TẠO step mới (`if (stepId == null)`  trong `openStepModal()`), mở
SỬA step ảnh cũ chỉ thấy `filePathFieldHtml` (chọn thẳng 1 file có sẵn,
không crop được). Đồng thời phát hiện thêm: khung crop chỉ có 4 handle GÓC
(`nw`/`ne`/`sw`/`se`) — muốn chỉnh riêng 1 cạnh (chỉ đổi rộng HOẶC chỉ đổi
cao) không có cách nào, phải kéo góc nên luôn đổi cả 2 chiều cùng lúc.

**Sửa 1 — hiện khối crop cả khi sửa step**: bỏ điều kiện `if (stepId ==
null)`, khối `<details>✂️ Cắt nhiều vùng` giờ luôn render. Nút bấm đổi
nhãn tuỳ ngữ cảnh (`addBtnLabel` — "💾 Tạo step từ vùng này" khi tạo mới,
"💾 Cập nhật ảnh cho step này" khi sửa) để không gây hiểu lầm về việc bấm
xong sẽ xảy ra chuyện gì.

`addImageCropStep()` (hàm xử lý khi bấm nút) tách 2 nhánh dựa vào
`editingStepId` (biến module-scope có sẵn, set bởi `openStepModal()`):
- `editingStepId == null` (tạo mới): giữ nguyên hành vi cũ — `steps.push()`
  thêm step mới vào cuối.
- `editingStepId != null` (đang sửa): **CẬP NHẬT** đúng step đang sửa
  (`steps.find(x => x.id === editingStepId)`, `Object.assign(s, data)`) —
  KHÔNG tạo thêm step nào. Giữ nguyên `offsetX`/`offsetY`/`rotation`/`gap`/
  `clearBefore` đã có của step đó, chỉ ghi đè `mediaUrl`/`mediaName`/`text`/
  `ttsText`/`voiceIdx`/`audioUrl`/`duration` (field liên quan ảnh+TTS).

Cả 2 nhánh đều KHÔNG gọi `closeStepModal()` — giữ đúng hành vi gốc "khung
crop giữ nguyên vị trí sau mỗi lần tạo, kéo rộng thêm rồi tạo tiếp cho
vùng kế" (mục 5.31), hữu ích cả khi sửa (có thể muốn crop tiếp 1 vùng khác
cho step khác ngay sau khi vừa cập nhật xong step hiện tại).

**Sửa 2 — thêm 4 handle cạnh giữa (n/s/e/w)**: HTML thêm 4 `<div class="m-ch
n/s/e/w">` (định vị bằng CSS — `n`/`s` là thanh ngang cao 14px chiếm hết
chiều rộng box trừ padding 12px 2 đầu, đặt ở mép trên/dưới; `e`/`w` tương
tự nhưng thanh dọc). Logic resize (`pointermove` listener) đã viết đủ
TỔNG QUÁT từ trước — dùng `.includes('w')`/`.includes('n')` etc. để check
từng hướng độc lập, vốn dùng cho combo 2 ký tự (`'nw'.includes('w')` →
true) nhưng cũng đúng luôn cho ký tự đơn — KHÔNG cần sửa gì phần tính toán
resize, chỉ cần sửa danh sách `mode` hợp lệ ở `pointerdown` listener
(dòng tìm `[...e.target.classList].find(c => [...].includes(c))`) từ
`['nw','ne','sw','se']` thành thêm `'n','s','e','w'` vào danh sách.

**Verify**: Playwright test trên `index.html` thật (qua http.server, có
`media_server.py` thật ở port 8010 để `/upload` hoạt động) — (1) tạo 1
step `image` giả qua JS trực tiếp (`steps.push(...)`, `mediaUrl:
'http://example.com/old.png'`); (2) `openStepModal('image', stepId)` mở
SỬA step đó — xác nhận `#m-crop-src-input` tồn tại trong DOM (trước đây
hoàn toàn không có khi sửa) và nút hiện đúng nhãn "💾 Cập nhật ảnh cho
step này"; (3) xác nhận cả 8 handle (4 góc + 4 cạnh mới) đều tồn tại; (4)
tạo ảnh test 400×300px thật (`ffmpeg -f lavfi color=red`) làm "ảnh gốc",
gọi `onImageCropSrcChosen(file)` qua `page.evaluate` — box mặc định đúng
= full ảnh (400×300); (5) kéo handle `w` (cạnh trái) vào trong 50px bằng
`page.mouse` thật (move→down→move→up, không gọi hàm JS trực tiếp) — box
đổi đúng CHỈ `x`/`w` (`x: 0→50, w: 400→350`), `h` giữ nguyên 300 — xác
nhận kéo cạnh không còn đụng chiều kia; (6) kéo tiếp handle `s` (cạnh
dưới) lên 30px — box đổi đúng CHỈ `h` (300→270), `w`/`x` giữ nguyên từ
bước trước; (7) bấm nút cập nhật — `steps.length` vẫn là 1 (không tạo
step mới), `steps[0].text`/`steps[0].mediaUrl` đổi đúng giá trị mới, ảnh
crop thật được upload thành công lên `media_server.py` (`mediaUrl` trả về
dạng `http://localhost:8010/media/crop_xxx.png`).

### 5.37. Bug: Meme/Ảnh luôn xoá nội dung cũ dù KHÔNG tick "Xoá nội dung cũ"

Người dùng phát hiện: checkbox "Xoá nội dung cũ" (`clearBefore`, mỗi step-
card đều có, mục 5.6) không có tác dụng thật với step `meme`/`image` — dù
để trống (không tick), 1 step meme/ảnh chạy xong vẫn luôn xoá sạch nội
dung Threads (post/comment) đứng trước nó, không cộng dồn tiếp với step
sau như post/comment/reply vẫn làm khi cùng điều kiện (gap=0, không tick).

**Nguyên nhân**: `computeSceneAt()` (`render_shared.js`, thuật toán "cộng
dồn ngược" mô tả ở mục 5.4) có dòng `if (s.clearBefore || s.type ===
'meme' || s.type === 'image') break;` — HARDCODE step `meme`/`image` LUÔN
cắt đứt chuỗi cộng dồn, bất kể giá trị thật của `s.clearBefore`. Đây là
quyết định cũ từ mục 5.17 (lúc code export, lý do ghi khi đó: "meme không
phải nội dung Threads, không thể làm cầu nối") — nhưng để lộ ra ngoài
người dùng: dù bấm untick checkbox trên UI, hành vi KHÔNG đổi, gây hiểu
lầm checkbox bị hỏng/vô dụng với 2 loại step này.

**Đã hỏi lại rõ qua `AskUserQuestion`** trước khi sửa (đây là hành vi đã
tồn tại có chủ đích ban đầu, không phải lỗi hiển nhiên — cần xác nhận
hướng đúng): người dùng xác nhận muốn checkbox là NGUỒN QUYẾT ĐỊNH DUY
NHẤT, giống hệt post/comment/reply — nhưng vẫn muốn giá trị MẶC ĐỊNH khi
TẠO MỚI step meme/ảnh là ĐÃ TICK SẴN (gọi là "tiên cơ" — đa số trường hợp
muốn xoá, nên tick sẵn cho tiện, chỉ cần chủ động BỎ tick khi nào muốn
card cũ + meme/ảnh cùng hiện).

**Sửa 2 phần**:
1. **Logic** (`render_shared.js`, `computeSceneAt`): bỏ hẳn
   `s.type === 'meme' || s.type === 'image'` khỏi điều kiện cắt — giờ CHỈ
   còn `if (s.clearBefore) break;`, đối xử y hệt mọi loại step khác. File
   này dùng CHUNG giữa `index.html` và `export_render.html` (mục 5.17) nên
   fix áp dụng đồng thời cho cả preview lẫn video xuất, không cần sửa 2 nơi.
2. **Mặc định lúc tạo mới** (`index.html`) — 2 chỗ tạo step meme/image:
   - `submitStepModal()` (modal thường, dùng chung mọi loại step qua biến
     `t`): `clearBefore: (t === 'meme' || t === 'image')` — TRUE cho 2 loại
     này, FALSE cho post/comment/reply/audio (giữ nguyên như cũ).
   - `addImageCropStep()` (luồng "Cắt nhiều vùng", mục 5.31/5.36): đổi
     thẳng `clearBefore: false` → `clearBefore: true` cho nhánh tạo step
     MỚI (nhánh cập nhật step đang sửa không đụng field này — giữ nguyên
     `clearBefore` cũ của step, đúng comment "giữ nguyên id, gap,
     clearBefore, hue" đã có từ trước ở `submitStepModal()`).
   - `selectMemereadyItem()` (chọn từ "Meme sẵn", mục 5.19) đã sẵn
     `clearBefore: true` từ trước — không cần sửa, giờ giá trị đó mới THẬT
     SỰ có tác dụng (trước đây luôn true dù giá trị field là gì).

**Verify**: Playwright gọi thẳng `computeSceneAt()`/`computeTimeline()`
qua `page.evaluate` với 2 kịch bản dựng tay (Post A → Meme → Comment B,
gap=0 xuyên suốt, không dùng UI để test nhanh phần lõi logic trước) —
(1) `meme.clearBefore=false` (không tick): stack tại thời điểm Comment B
đang sống chứa CẢ `[1,3]` (Post A + Comment B cùng hiện) — đúng fix; (2)
`meme.clearBefore=true` (có tick): stack chỉ còn `[3]` (Post A bị xoá đúng
như hành vi cũ) — xác nhận không phá vỡ trường hợp người dùng chủ động
muốn xoá. Test thêm qua `submitStepModal()` thật (không giả lập) cho step
mới: step `meme` mới tạo có `clearBefore: true` mặc định, step `audio`
mới tạo vẫn `clearBefore: false` — xác nhận mặc định chỉ áp dụng đúng 2
loại meme/image, không lan sang loại khác.

### 5.38. Đánh dấu cảnh báo 5 giọng TTS chất lượng chưa tốt (không xoá)

Người dùng đánh giá 5/11 giọng CapCut (`CAPCUT_VOICES_VI`, gốc repo
`translate_video.py`, mục 5.7) nghe không tốt: "Giọng Gái Mới Lớn", "Ban
Mai", "Bản Tin Nữ", "Giọng Nam Trầm", "Alex Đại Đế" — nhưng KHÔNG muốn xoá
khỏi danh sách (có thể vẫn cần dùng), chỉ muốn đánh dấu trực quan (nền/màu
vàng) trong dropdown chọn giọng để tự nhắc tránh chọn nhầm.

**Không sửa `translate_video.py`/`CAPCUT_VOICES_VI`** — đúng quy tắc mục 2
(không đụng file gốc repo). Việc đánh dấu thuần tuý là UI, xử lý hoàn toàn
ở tầng hiển thị trong `index.html`.

- `WARN_VOICE_LABELS` (Set, ~cạnh khai báo `ttsVoices`): so khớp theo
  LABEL của giọng (không phải index) — vì thứ tự trong
  `CAPCUT_VOICES_VI` có thể đổi/thêm giọng mới sau này mà không muốn phải
  sửa lại danh sách cảnh báo theo.
- `voiceOptionHtml(v, i, selected)`: hàm dùng CHUNG cho mọi nơi build
  `<option>` giọng — nếu label khớp `WARN_VOICE_LABELS`, thêm prefix
  `⚠️ ` + suffix `(chất lượng chưa tốt)` vào text hiển thị, và set
  `style="color:#eab308;background:#1c1917"` TRỰC TIẾP trên thẻ `<option>`
  (không qua class CSS — `<option>` có hỗ trợ rất hạn chế với CSS class
  thông thường tuỳ trình duyệt, nhưng đa số trình duyệt hiện đại tôn trọng
  `style` inline trên `<option>`, đặc biệt `color`/`background`, nên chọn
  cách này thay vì cố style qua stylesheet).
- Áp dụng ở CẢ 2 nơi build dropdown giọng (trước đây build riêng lẻ, dễ
  quên sửa 1 trong 2 nếu không gộp hàm): `loadTtsVoices()` (dropdown chung
  sidebar `#tts-voice`) và `voiceFieldHtml()` (dropdown riêng từng step
  trong modal soạn, mục 5.7 "chọn giọng riêng theo từng step").

**Verify**: Playwright mở `index.html` thật (có `tts_server.py` thật chạy
ở port 8007 để `/voices` trả đúng 11 giọng) — đọc `options` của cả
`#tts-voice` (sidebar) và `#m-voice` (mở qua `openStepModal('post')`) qua
`.style.color`/`.textContent` — xác nhận ĐÚNG 5 giọng khớp
`WARN_VOICE_LABELS` có `⚠️` + màu `rgb(234, 179, 8)` (= `#eab308`), 6 giọng
còn lại hoàn toàn không đổi (color rỗng, text nguyên gốc không suffix) —
khớp chính xác ở cả 2 dropdown, không lệch nhau.

### 5.39. Kịch bản Messenger — nhập cả hội thoại, sinh N step cộng dồn

Thêm loại step `mess` chạy song song về mặt tính năng với Post/Comment,
nhưng hiển thị một cửa sổ chat Messenger dark mode nổi trên video nền.
Nút `＋ Messenger` mở modal rộng chia 2 cột: bên trái là bộ soạn tin, bên
phải preview đoạn chat ngay lập tức.

- Hàng đầu có 2 textarea loại trừ nhau: `Đối phương` (bubble trái) và `Tôi`
  (bubble phải). Gõ bên nào thì ô bên kia tự xoá để xác định người gửi.
- Ô TTS phía dưới tự đồng bộ với nội dung hiển thị cho đến khi người dùng tự
  sửa; mỗi tin có select giọng riêng. `＋ Thêm tin nhắn` chỉ thêm vào bản
  nháp/preview và KHÔNG đóng modal để tiếp tục nhập.
- Bấm `Tạo kịch bản Messenger` với N tin sẽ tạo N step `mess` độc lập. Step
  thứ i lưu `messHistory` gồm tin 1..i, nhưng `text`/`ttsText`/`voiceIdx` và
  audio TTS chỉ thuộc tin i. Vì vậy mỗi cảnh hiển thị hội thoại cộng dồn,
  còn giọng đọc chỉ đọc tin mới xuất hiện.
- Mỗi chuỗi dùng `conversationId` + `messIndex`. Sửa một card `mess` mở lại
  modal với lịch sử tới card đó; lưu sẽ tạo lại TTS riêng card đang sửa và
  cập nhật nội dung tương ứng trong mọi `messHistory` phía sau cùng cuộc
  hội thoại. Xoá một card cũng bỏ tin đó khỏi các cảnh sau và giảm index.
- `clearBefore:true` mặc định cho mọi step `mess`, để cảnh mới thay ảnh chat
  cũ thay vì stack nhiều cửa sổ Messenger. Audio/meme vẫn có thể chèn giữa
  các step như mọi loại kịch bản khác.
- Markup được build duy nhất bởi `buildMessengerHtml()` trong
  `render_shared.js`; CSS Messenger phải giữ đồng bộ ở `index.html` và
  `export_render.html`. `export_server.py` coi `mess` là step có hình tại
  `compute_breakpoints()` và là step có TTS khi trộn audio.

## 6. Trạng thái hiện tại — đã làm / chưa làm

### Đã hoàn thành và test kỹ (Phase 1 + các bổ sung)

- [x] 4 mẫu Threads UI mock, chốt pixel-perfect.
- [x] Khung preview 9:16 responsive bằng `cqw`.
- [x] Chọn video nền (upload, preview, xoá).
- [x] Chọn âm thanh nền (upload, chỉnh âm lượng, đồng bộ play/pause với
      video, tự mute tiếng gốc video khi có nhạc nền).
- [x] Step engine: thêm/sửa/xoá/đổi thứ tự step, tính timeline tương đối.
- [x] 5 loại step: post, comment, reply (comment+reply gộp 1 step), meme,
      audio.
- [x] Cơ chế gap đúng (nội dung biến mất trong khoảng lặng, cộng dồn khi
      gap=0, `clearBefore` xoá sạch).
- [x] Auto-scroll stack khi tràn khung.
- [x] Thanh Play/Pause xem trước bằng `requestAnimationFrame` + thanh tiến
      trình.
- [x] Ảnh đính kèm cho post/comment/reply.
- [x] Số liệu tương tác (tim/comment/repost/share) tuỳ chỉnh mỗi step.
- [x] Thời gian đăng tuỳ chỉnh mỗi step.
- [x] **TTS thật (CapCut TTS)** — xem mục 5.7 bên dưới.
- [x] **Step `audio` chạy song song + phát tiếng thật** — xem mục 5.8 bên dưới.
- [x] **Ảnh/media/avatar đính kèm dùng URL/đường dẫn file** thay vì upload — mục 5.9.
- [x] **Xuất/Nạp kịch bản ra file JSON** — xem mục 8 bên dưới.
- [x] **Làm mờ tên & avatar (blur) trong preview**, bật/tắt qua sidebar — mục 5.10.
- [x] **Tên/avatar/giờ đăng tự động random**, không cần nhập tay — mục 5.11.
- [x] **Upload meme/audio qua kéo-thả** (`threads/media_server.py`) — mục 5.12.
- [x] **Audio mặc định tuần tự, tuỳ chọn song song + kéo-thả sắp xếp step** — mục 5.13.
- [x] **Thư viện âm thanh + meme có sẵn** (`threads/audio/`, `threads/gif/`), chọn nhanh không cần upload — mục 5.14.
- [x] **Thư viện dạng lưới, nghe/xem thử trước khi chọn** — mục 5.15.
- [x] **Card cũ không animate lại khi cộng dồn/sửa step khác** — mục 5.16.
- [x] **Ghép & xuất video ra file .mp4 hoàn chỉnh** (FFmpeg + Playwright, `export_server.py`) — mục 5.17.

Toàn bộ các mục trên đã qua kiểm thử bằng script tự động (giả lập gọi hàm
JS qua iframe trong Chrome headless — không dùng Playwright, môi trường này
không có sẵn) để xác nhận đúng logic trước khi báo cáo hoàn thành. Cách test
tiêu biểu: mở `index.html` trong iframe, gọi trực tiếp các hàm JS đã định
nghĩa (`openStepModal`, `submitStepModal`, `updateStepField`, `renderAtTime`,
`computeSceneAt`...) để giả lập thao tác người dùng, rồi đọc DOM/giá trị trả
về để so với kỳ vọng. Với TTS (mục 5.7), test còn được nâng cấp thêm một
bước: dùng Chrome headless thật với `--remote-debugging-port` + Python
`websocket-client` để điều khiển qua Chrome DevTools Protocol (CDP) — gọi
`Runtime.evaluate` với `awaitPromise:true` để chờ `submitStepModal()` (giờ
là hàm `async`) chạy xong round-trip HTTP thật tới `tts_server.py`, thay vì
chỉ gọi hàm JS đồng bộ như trước. Phải serve `threads/` qua
`python3 -m http.server` (không mở trực tiếp bằng `file://`) để tránh vướng
CORS khi `fetch()` gọi sang `http://localhost:8007`.

### CHƯA làm (Phase tiếp theo — chưa được yêu cầu code, chỉ mới bàn hướng)

- [x] ~~Dọn dẹp job cũ trong `threads/exports/`~~ — đã có nút "Dọn cache"
      thủ công (mục 5.31, sidebar mục 5), xoá `media/`/`exports/`/
      `tts_audio/`. Vẫn không có cơ chế TỰ ĐỘNG/định kỳ (chỉ thủ công theo
      ý người dùng bấm nút) — cố ý, tránh xoá nhầm file đang cần.
- [ ] Preview cho step `meme`/`audio` hiện dùng placeholder caro xám khi
      chưa chọn file — giữ nguyên tinh thần "test thời gian trước, nội dung
      sau" mà người dùng đã đồng ý ở Phase 1.

## 7. Quy tắc làm việc rút ra được (áp dụng khi tiếp tục)

1. **Không tự vẽ lại UI Threads** — luôn tham chiếu `threads_ui_mock.html`,
   copy nguyên cấu trúc, chỉ quy đổi đơn vị theo công thức mục 5.2.
2. **Verify bằng ảnh chụp thật**, không chỉ đọc code rồi tin là đúng — dùng
   Chrome headless `--screenshot` để so sánh trực quan trước khi báo hoàn
   thành, đặc biệt với các thay đổi CSS/layout.
3. **Test cơ chế logic (timeline, gap, stack) bằng script tự động** trước
   khi báo hoàn thành, không chỉ dựa vào đọc code. Môi trường headless ở đây
   thiếu codec giải mã video/audio thật — nên với phần liên quan tới
   `<video>`/`<audio>` load file thật, cần nói rõ với người dùng đó là hạn
   chế môi trường test, không phải bug, và nhờ họ tự xác nhận bằng tay.
4. **Khi người dùng mô tả một cơ chế mơ hồ ("gap là...")**, hỏi lại rõ bằng
   `AskUserQuestion` với ví dụ cụ thể trước khi code — tránh code sai rồi
   phải sửa lại (đã xảy ra 1 lần với chính cơ chế gap).
5. Đơn vị thời gian trong toàn bộ engine là **giây** (số thực), timing luôn
   là **tương đối** (không lưu mốc tuyệt đối cố định) để việc chèn/xoá/sắp
   xếp lại step không đòi hỏi tính toán tay.
6. File `index.html` hiện tự chứa toàn bộ (không có build step, không có
   framework) — giữ nguyên tinh thần này trừ khi người dùng yêu cầu đổi kiến
   trúc (vd sang React) một cách tường minh.
7. **3 server Python (`media_server.py`/`export_server.py`/`tts_server.py`)
   KHÔNG tự nạp lại code khi file .py thay đổi trên đĩa** (không chạy
   `--reload`, chạy nền bằng `nohup .../disown` hoặc mở tay) — sau khi SỬA
   XONG bất kỳ file server nào, PHẢI `kill` process cũ rồi chạy lại (hoặc
   nhắc người dùng tự làm) thì thay đổi mới có hiệu lực thật. Bài học từ
   sự cố thật: sửa xong 2 tính năng (dọn cache mục 5.31, fix âm lượng mục
   5.34) nhưng KHÔNG restart server — người dùng test thấy "vẫn y hệt lỗi
   cũ" dù code đã đúng, tưởng nhầm là code chưa sửa được. Cách phát hiện
   nhanh: so `ps -p <pid> -o lstart=` (giờ server khởi động) với
   `stat -f "%Sm" <file>.py` (giờ sửa file lần cuối) — nếu server khởi
   động TRƯỚC lúc sửa file, chắc chắn đang chạy code cũ.

## 8. Xuất / Nạp kịch bản ra file JSON

Người dùng muốn có cách lưu lại toàn bộ "data của dự án" ra file, để lỡ mất
(đóng nhầm tab, máy crash...) thì nạp lại chứ không phải làm lại từ đầu.
Trước khi làm tính năng này, phải dọn sạch mọi `File`/blob object khỏi step
data trước (mục 5.9) — JSON chỉ chứa được text/số/boolean, không chứa được
đối tượng File hay `blob:` URL (mất khi reload nên vốn không có ý nghĩa để
lưu lại).

- **`exportProjectJson()`** (~dòng 1350): đóng gói `{_format:
  'threads-short-video-project', _version: 1, exportedAt, steps, stepIdCounter,
  ttsServerUrl, ttsVoiceIdx, bgAudioVolume}` thành file `.json` tải về qua kỹ
  thuật `Blob` + `<a download>` ẩn (click bằng JS). Tên file tự sinh theo
  timestamp (`threads-project-<ISO date>.json`). **Không lưu `_start`/`_end`**
  của mỗi step (mốc tính toán tạm bợ, `computeTimeline()` tự tính lại khi
  nạp — tương tự nguyên tắc "timing luôn tương đối" ở mục 7.5, không nên lưu
  giá trị suy ra được).
- **`importProjectJson(input)`** (~dòng 1385): đọc file qua `FileReader`,
  `JSON.parse`, validate có đúng `_format`/`steps` là mảng không (báo lỗi rõ
  ràng bằng tiếng Việt nếu sai, không throw crash). Nếu kịch bản hiện tại
  đang có step, `confirm()` cảnh báo sẽ **thay thế hoàn toàn** trước khi ghi
  đè `steps[]` — tránh mất dữ liệu đang làm dở do bấm nhầm nút Nạp. Sau khi
  gán `steps`/`stepIdCounter`, phải gọi lại `loadTtsVoices()` rồi mới set
  được `ttsVoiceIdx` đã lưu (vì `<select id="tts-voice">` cần danh sách
  option đã tồn tại trước, load bất đồng bộ từ TTS server).
- **KHÔNG xuất/nạp được video nền và nhạc nền** — 2 file này vẫn dùng
  `<input type="file">` + blob URL (chưa đổi sang URL/path như ảnh/meme ở
  mục 5.9, vì người dùng không yêu cầu đổi phần này). Sau khi nạp JSON,
  `alert()` nhắc rõ người dùng phải tự chọn lại 2 file đó ở sidebar trái.
- Nếu kịch bản nạp vào có step meme/audio dùng `mediaPath` (đường dẫn file
  local), `alert()` sau khi nạp cũng nhắc: đường dẫn đó thuộc máy đã xuất
  JSON — mở trên máy khác hoặc file đã bị di chuyển thì phải sửa lại đường
  dẫn thủ công trong từng step (JSON không có cách nào "mang theo" file thật
  kèm theo nó).
- Test: vì `exportProjectJson()` trigger tải file thật qua `<a download>`
  (khó bắt trong CDP headless), test tách làm 2 phần — (1) build trực tiếp
  object JSON giống hệt logic export rồi kiểm tra cấu trúc (không còn
  `_start`/`_end`, không còn field `blob:` nào sót), (2) test
  `importProjectJson()` thật 100% qua UI bằng cách tạo `File` +
  `DataTransfer` giả gán vào `<input type="file">` rồi gọi hàm — bao gồm cả
  case file JSON hợp lệ, thiếu `_format`, và syntax JSON lỗi, đều xử lý đúng
  không crash.
