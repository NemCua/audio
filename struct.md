# Threads Video Maker — JSON spec cho AI

Mục tiêu: từ yêu cầu người dùng, trả về **JSON thuần** nạp trực tiếp vào Kịch bản. Không Markdown, không giải thích, không ```.

## Luật cứng

- Chỉ dùng type: `post|comment|reply|mess|text|note|image`.
- Cấm: `audio|meme|sticker`; không bịa media/file local.
- Root bắt buộc: `_format:"threads-short-video-project"`, `_version:1`, `steps:[...]`, `stepIdCounter:max(id)+1`.
- `id`: số nguyên dương, duy nhất; `steps` xếp đúng thứ tự phát.
- Không sinh field tạm: `_start,_end,audioUrl,audioDesktopPath,ttsDuration,subtitleWords,subtitleError,audioServerUrl,ttsServerUrl`.
- Mọi step có base: `id,type,duration,gap,clearBefore,hue,offsetX,offsetY,rotation`.
- Mặc định: `gap:0,hue:0,offsetX:0,offsetY:0,rotation:0`; `duration>=0`.
- `gap` là thời gian chờ sau khi card kết thúc rồi card tuần tự kế tiếp mới xuất hiện.
- `appearance`: `normal|fly-left|fly-right|fly-top|fly-bottom|drop-bounce|bounce-up|bounce-left|bounce-right`; mặc định `normal`.
- `text` là chữ hiển thị, có thể viết tắt tự nhiên. `echo:true` chỉ dùng cho câu chốt/plot twist.

### Luật tạo `ttsText` an toàn

- Hiểu ngữ cảnh rồi mở rộng toàn bộ viết tắt: `t`→`tao/tôi`, `m`→`mày`, `r`→`rồi`, `k/ko/kh`→`không`, `đc`→`được`, `ny`→`người yêu`... Không thay máy móc làm sai vai hoặc nghĩa câu.
- Phiên âm mọi từ/cụm tiếng Anh sang cách người Việt đọc; không giữ chính tả Anh. Ví dụ: `deadline`→`đét lai`, `crush`→`cờ rớt`, `sorry`→`so ri`, `AI`→`ây ai`.
- Chỉ cho phép chữ, số, khoảng trắng, dấu phẩy và dấu chấm. Cấm emoji cùng các ký tự đặc biệt như `@ # $ % ^ & * ( ) [ ] { } _ - + / \\ | = : ; ! " '`.
- Dấu hỏi trong câu bình thường phải bỏ khỏi `ttsText`. Chỉ khi toàn bộ câu là `?`, `??` hoặc `???` thì dùng `ttsText:"hỏi chấm"`.
- Chuẩn hóa khoảng trắng và chuỗi dấu câu; không đưa biểu tượng cảm xúc hoặc chuỗi trang trí vào lời đọc.
- Với progressive reveal, số đoạn không rỗng trong chữ hiển thị và lời đọc phải khớp 1–1.
- Bảy giọng Tin nhắn được whitelist nhận tối đa 700 ký tự mỗi tin/đoạn; các giọng khác tối đa 390. Không rõ giới hạn thì giữ dưới 390.
- Không rõ giọng thì dùng `voiceIdx:0`.

## Root

```json
{"_format":"threads-short-video-project","_version":1,"exportedAt":"2026-08-25T00:00:00.000Z","steps":[],"stepIdCounter":1,"ttsVoiceIdx":"0","bgAudioVolume":"0.25","bgVideoLibrary":null,"bgAudioLibrary":null}
```

`exportedAt` dùng ISO-8601. Các field root ngoài `_format,_version,steps,stepIdCounter` có thể giữ đúng mặc định trên.

## Base step

```json
{"id":1,"type":"post","duration":3,"gap":0,"clearBefore":true,"hue":0,"offsetX":0,"offsetY":0,"rotation":0}
```

## `post`

Thêm vào base:

```json
{"appearance":"normal","text":"chữ hiển thị","ttsText":"Lời đọc đầy đủ","voiceIdx":0,"echo":false,"uname":"ten.gia","avatarUrl":"","identityBlurMode":null,"imgUrl":null,"stats":{"likes":"1,2K","comments":"34","reposts":"8","shares":"150","timeLabel":"2 giờ"},"progressiveReveal":false,"progressiveRevealMode":"basic","extraTtsSegments":[]}
```

- `identityBlurMode`: `null` theo công tắc toàn app; `blur` luôn mờ; `clear` luôn rõ.
- `imgUrl`: URL ảnh bài đăng hoặc `null`. Không có avatar thì `avatarUrl:""`; AI tự đặt `uname` giả phù hợp.
- `stats` luôn là chuỗi để giữ dạng `1,2K`.

## `comment` / `reply`

Hai card độc lập; thêm vào base:

```json
{"platform":"threads","appearance":"normal","text":"chữ hiển thị","ttsText":"Lời đọc đầy đủ","voiceIdx":0,"echo":false,"uname":"ten.gia","avatarUrl":"","identityBlurMode":null,"isAuthor":false,"imgUrl":null,"hasReplyCount":false,"replyCount":null,"stats":{"likes":"842","comments":"12","reposts":"0","shares":"3","timeLabel":"1 giờ"},"progressiveReveal":false,"progressiveRevealMode":"basic","extraTtsSegments":[]}
```

- `platform`: `threads|tiktok`.
- `isAuthor:true`: card thuộc tác giả bài gốc.
- `hasReplyCount/replyCount` chỉ dùng cho `comment+tiktok`, chỉ hiện “Xem N câu trả lời”. Trường hợp khác dùng `false/null`.
- Mặc định `comment.clearBefore:true`; `reply.clearBefore:false` để giữ comment trước, trừ khi mở cảnh mới.

## Progressive reveal cho `post|comment|reply`

`text` có N dòng không rỗng. Lời đọc dòng 1 ở `ttsText`; N-1 dòng còn lại nằm đúng thứ tự trong `extraTtsSegments`:

```json
{"progressiveReveal":true,"progressiveRevealMode":"basic","text":"Đoạn 1\nĐoạn 2\nĐoạn 3","ttsText":"Lời đọc đoạn 1","voiceIdx":0,"echo":false,"progressivePause":0.3,"progressiveGapAfterFirst":0.3,"extraTtsSegments":[{"text":"Lời đọc đoạn 2","voiceIdx":0,"echo":false,"gapAfter":0.3},{"text":"Lời đọc đoạn 3","voiceIdx":0,"echo":true,"gapAfter":0}]}
```

Ràng buộc: `số dòng text = 1 + extraTtsSegments.length`. Chỉ đoạn cuối hiện footer. Dùng mode `advanced` để app hiện Box CONTENT chỉnh từng đoạn; cấu trúc khác giữ nguyên.

## `text`

Thêm vào base:

```json
{"appearance":"normal","text":"3 tiếng sau...","hasVoice":false,"textFont":"display","textFontSize":6,"textColor":"#ffffff","textStrokeColor":"#000000","textStrokeWidth":0.18,"extraTtsSegments":[]}
```

- `textFont`: `system|serif|rounded|mono|display`.
- `textFontSize`: 2..12; `textStrokeWidth`: 0..1.5, là viền ôm ký tự.
- Nếu `hasVoice:true`, thêm `ttsText,voiceIdx,echo`.

## `note`

Thêm vào base:

```json
{"appearance":"normal","text":"Nội dung kể chuyện...","hasVoice":true,"ttsText":"Nội dung đọc đầy đủ","voiceIdx":0,"echo":false,"textFont":"mono","textFontSize":2.6,"textFontWeight":200,"textColor":"#ffffff","notePadding":6.2,"cardBgColor":"#050505","cardRadius":1.2,"noteBgPreset":"black","noteBgUrl":null,"noteSubtitles":true,"extraTtsSegments":[]}
```

- `textFontWeight`: 200..900; `notePadding`: 0..14; `cardRadius`: 0..10.
- `noteBgPreset`: `black|paper|sunset|ocean|lavender|glass|custom`.
- Chỉ preset `custom` mới dùng `noteBgUrl`; preset khác bắt buộc `null`.
- Không cần đọc: `hasVoice:false`, bỏ `ttsText,voiceIdx,echo`, dùng `noteSubtitles:false`.

## `image`

Thêm vào base:

```json
{"mediaPath":null,"mediaUrl":"https://example.com/anh.jpg","mediaName":"anh.jpg","mediaIsVideo":false,"text":"","extraTtsSegments":[]}
```

- Chỉ dùng URL ảnh trực tiếp do prompt cung cấp. Chưa có ảnh: `mediaUrl:null,mediaName:null`; không bịa URL.
- `mediaPath:null`, `mediaIsVideo:false`; không sinh dữ liệu crop.
- `text` là nội dung đọc chứ không phải caption. Có TTS thì thêm `ttsText,voiceIdx,echo`; không đọc thì để `text:""`.

## Song song

Trong spec này chỉ `image|text` được song song. Step song song nằm ngay sau card chính (hoặc sau media cùng nhóm), giữ duration riêng và thêm:

```json
{"parallel":true,"parallelAnchorStepId":1}
```

Neo vào CONTENT progressive nâng cao: thêm `parallelAnchorPartIndex` (0 là CONTENT đầu):

```json
{"parallel":true,"parallelAnchorStepId":3,"parallelAnchorPartIndex":1}
```

Không đặt parallel cho `post|comment|reply|note|mess`.

## `mess` — Box Messenger/Zalo

Cuộc trò chuyện N tin phải tạo N step liên tiếp. Mỗi step có history tích lũy từ tin 1 đến tin hiện tại.

- `messPlatform`: `messenger|zalo`; `side`: `theirs|mine`.
- Cả Box dùng chung `conversationId` và `groupId`; `messIndex` chạy 0..N-1.
- `messTtsSpeed`: 0.5..1.5, mặc định 1.
- `text,ttsText,voiceIdx,echo` cấp step phải giống tin cuối trong `messHistory` của chính step đó.
- Message không ảnh: `imageUrl,imageName,imageDesktopPath:null`, `imageType:"image"`.
- Tin nhắn chỉ được dùng các giọng nữ: `Nhỏ Ngọt Ngào` (`0`), `Giọng Nữ Phổ Thông` (`1`), `Giọng Bé` (`2`), `Cô Gái Hoạt Ngôn` (`3`), `Mai` (`7`); hoặc giọng nam: `Thanh Niên Tự Tin` (`22`), `Alex Đại Đế` (`23`).
- Tên nữ phải dùng giọng nữ, tên nam phải dùng giọng nam. Nếu giới tính chưa rõ, AI tự quyết định hợp lý rồi giữ tên/giọng nhất quán.
- Mỗi phía giữ một giọng xuyên suốt; `messSideVoices.theirs/mine` và mọi `voiceIdx` của message tương ứng phải khớp giọng phía đó. Không dùng index khác.

Mẫu Box 2 tin:

```json
[
  {"id":7,"type":"mess","conversationId":"mess-box-1","groupId":"mess-box-1","messIndex":0,"messPlatform":"messenger","messShowFooter":true,"messPartnerName":"Linh","messPartnerAvatar":"","messSideVoices":{"theirs":0,"mine":0},"messTtsSpeed":1,"messHistory":[{"side":"theirs","text":"ê tối nay rảnh kh","ttsText":"Ê tối nay rảnh không","voiceIdx":0,"voiceOverride":false,"echo":false,"imageUrl":null,"imageName":null,"imageType":"image","imageDesktopPath":null}],"text":"ê tối nay rảnh kh","ttsText":"Ê tối nay rảnh không","voiceIdx":0,"echo":false,"duration":2,"gap":0,"clearBefore":true,"hue":0,"offsetX":0,"offsetY":0,"rotation":0},
  {"id":8,"type":"mess","conversationId":"mess-box-1","groupId":"mess-box-1","messIndex":1,"messPlatform":"messenger","messShowFooter":true,"messPartnerName":"Linh","messPartnerAvatar":"","messSideVoices":{"theirs":0,"mine":0},"messTtsSpeed":1,"messHistory":[{"side":"theirs","text":"ê tối nay rảnh kh","ttsText":"Ê tối nay rảnh không","voiceIdx":0,"voiceOverride":false,"echo":false,"imageUrl":null,"imageName":null,"imageType":"image","imageDesktopPath":null},{"side":"mine","text":"rảnh, có chuyện gì z","ttsText":"Rảnh, có chuyện gì vậy","voiceIdx":0,"voiceOverride":false,"echo":false,"imageUrl":null,"imageName":null,"imageType":"image","imageDesktopPath":null}],"text":"rảnh, có chuyện gì z","ttsText":"Rảnh, có chuyện gì vậy","voiceIdx":0,"echo":false,"duration":2,"gap":0,"clearBefore":true,"hue":0,"offsetX":0,"offsetY":0,"rotation":0}
]
```

## Cách chọn card

- Mở chủ đề: `post`; người mới tham gia: `comment`; phản hồi comment: `reply`.
- Hội thoại riêng: `mess`; tiêu đề/chuyển cảnh: `text`; kể chuyện dài trên nền chữ: `note`.
- Chỉ tạo `image` khi prompt có URL hoặc yêu cầu card trống để người dùng chọn ảnh.

## Checklist output

JSON parse được; dấu nháy kép; không dấu phẩy thừa; đúng `_format`; type hợp lệ; ID duy nhất; `stepIdCounter>max(id)`; không field/type cấm; progressive khớp số dòng; Messenger history tích lũy/index liên tục; parallel anchor tồn tại; mọi duration/gap/tọa độ/góc là số.

## Mẫu dự án tối thiểu

```json
{"_format":"threads-short-video-project","_version":1,"exportedAt":"2026-08-25T00:00:00.000Z","steps":[{"id":1,"type":"post","duration":3,"gap":0,"clearBefore":true,"hue":210,"offsetX":0,"offsetY":0,"rotation":0,"appearance":"normal","text":"có ai từng quyết tâm ngủ sớm chưa","ttsText":"Có ai từng quyết tâm ngủ sớm chưa","voiceIdx":0,"echo":false,"uname":"chuyen.dem","avatarUrl":"","identityBlurMode":null,"imgUrl":null,"stats":{"likes":"1,2K","comments":"34","reposts":"8","shares":"150","timeLabel":"2 giờ"},"progressiveReveal":false,"progressiveRevealMode":"basic","extraTtsSegments":[]}],"stepIdCounter":2,"ttsVoiceIdx":"0","bgAudioVolume":"0.25","bgVideoLibrary":null,"bgAudioLibrary":null}
```
