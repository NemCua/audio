const fs = require('fs');
const path = require('path');

let nextId = 1;
const steps = [];
const imageVideo = 'YTSave_YouTube_Free-memes-NO-COPYRIGHT-Enjoy-guys-Can-b_Media_OFiLG1WWBeE_001_10_71836acb_cut_4cab81a4.mp4';
const imageVideo2 = 'YTSave_YouTube_Free-memes-NO-COPYRIGHT-Enjoy-guys-Can-b_Media_OFiLG1WWBeE_001_10_2b75a1c4_cut_25e92a44.mp4';

function add(type, data = {}) {
  const id = nextId++;
  steps.push({
    id, type, duration: data.duration ?? ({ post: 4, comment: 3, reply: 3, text: 3, note: 4, image: 3, sticker: 2, audio: 2, mess: 2 }[type]),
    gap: data.gap ?? 0.15, clearBefore: data.clearBefore ?? false,
    hue: data.hue ?? 0, offsetX: data.offsetX ?? 0, offsetY: data.offsetY ?? 0, rotation: data.rotation ?? 0,
    layerMode: 'timeline', ...data,
  });
  return id;
}

function tts(text, voiceIdx = 0, echo = false, emphasisBubble = false, gapAfter = 0.3) {
  return { text, voiceIdx, echo, emphasisBubble, gapAfter, duration: 0 };
}

function social(type, text, options = {}) {
  return add(type, {
    text, ttsText: options.ttsText || text.split('\n')[0], voiceIdx: options.voiceIdx ?? 0,
    echo: !!options.echo, emphasisBubble: !!options.emphasisBubble,
    extraTtsSegments: options.extraTtsSegments || [], appearance: options.appearance || 'normal',
    progressiveReveal: !!options.progressiveReveal, progressiveRevealMode: options.progressiveRevealMode || 'basic',
    progressivePause: 0.3, progressiveGapAfterFirst: 0.3,
    platform: options.platform || 'threads', isAuthor: !!options.isAuthor,
    hasReplyCount: !!options.hasReplyCount, replyCount: options.hasReplyCount ? (options.replyCount || 24) : null,
    uname: options.uname || 'kiem_thu_card', avatarUrl: 'avatarthreads/358389238_577583710976396_4095180163388838913_n.jpg',
    identityBlurMode: options.identityBlurMode || '', imgUrl: options.imgUrl || null,
    stats: { likes: options.likes || '1,2K', comments: options.comments || '34', reposts: options.reposts || '8', shares: options.shares || '150', timeLabel: options.timeLabel || 'vừa xong' },
  });
}

// SOCIAL: đủ Threads/TikTok, tác giả, ảnh, 8 kiểu xuất hiện và hai kiểu chia đoạn.
const postAnchor = social('post', 'BỘ KIỂM THỬ CARD V2\nMỗi card bên dưới đều có mục tiêu riêng.\nNghe câu đọc tiếng Việt có dấu để kiểm tra giọng đọc.', {
  uname: 'phong_lab', appearance: 'drop-bounce', progressiveReveal: true, progressiveRevealMode: 'advanced',
  ttsText: 'Bộ kiểm thử card phiên bản hai.', extraTtsSegments: [
    tts('Mỗi card bên dưới đều có mục tiêu riêng.', 0, false, false, 0.45),
    tts('Nghe câu đọc tiếng Việt có dấu để kiểm tra giọng đọc.', 0, true, true),
  ], likes: '0', comments: '0', reposts: '0', shares: '0', timeLabel: '1 phút', offsetY: -2,
});
social('post', 'Post Threads hiện dần cơ bản\nDòng thứ hai đọc sau một khoảng nghỉ\nDòng cuối có hiệu ứng vang.', {
  appearance: 'fly-left', progressiveReveal: true, progressiveRevealMode: 'basic',
  extraTtsSegments: [tts('Dòng thứ hai đọc sau một khoảng nghỉ.', 0, false, false, 0.25), tts('Dòng cuối có hiệu ứng vang.', 0, true, true)],
  uname: 'threads_kiem_thu', identityBlurMode: 'clear', offsetX: -3,
});
social('post', 'Post thường có ảnh minh họa để kiểm tra bố cục card xã hội.', { appearance: 'fly-top', uname: 'anh_co_anh', imgUrl: 'https://images.unsplash.com/photo-1499750310107-5fef28a66643?auto=format&fit=crop&w=800&q=80' });

social('comment', 'Bình luận Threads bay từ phải vào. Nội dung có dấu và dấu câu đầy đủ.', { appearance: 'fly-right', uname: 'binh_luan_threads', likes: '245', comments: '8', offsetX: 3 });
social('comment', 'Đây là bình luận của chính tác giả bài viết để kiểm tra badge tác giả.', { appearance: 'bounce-left', uname: 'phong_lab', isAuthor: true, identityBlurMode: 'blur' });
social('comment', 'Bình luận TikTok có dòng xem câu trả lời ở phía dưới.', { platform: 'tiktok', appearance: 'bounce-right', uname: 'tiktok_vui', hasReplyCount: true, replyCount: 86, likes: '9,8K' });
social('reply', 'Trả lời Threads rơi xuống rồi nảy nhẹ, vẫn giữ đúng kiểu card trả lời.', { appearance: 'drop-bounce', uname: 'reply_threads' });
social('reply', 'Trả lời TikTok của nhà sáng tạo để test nhãn tác giả trên TikTok.', { platform: 'tiktok', appearance: 'bounce-up', uname: 'chu_kenh', isAuthor: true });
social('reply', 'Trả lời TikTok xuất hiện bình thường, dùng nhiều đoạn giọng đọc không hiện dần.', {
  platform: 'tiktok', appearance: 'normal', uname: 'nguoi_xem',
  extraTtsSegments: [tts('Đây là đoạn đọc bổ sung thứ hai.', 0, false, false, 0.2), tts('Đây là đoạn đọc bổ sung cuối cùng.', 0, true, false)],
});

// TEXT: font, viền, màu, có voice/không voice và các chuyển động còn lại.
add('text', { text: 'TEXT: Chữ lớn, có viền, bay từ dưới lên.', ttsText: 'Chữ lớn, có viền, bay từ dưới lên.', voiceIdx: 0, hasVoice: true, appearance: 'fly-bottom', textFont: 'display', textFontSize: 7.2, textColor: '#fff4b8', textStrokeColor: '#24120a', textStrokeWidth: 0.42, clearBefore: true });
add('text', { text: 'TEXT: Font serif màu hồng, rơi xuống rồi nảy.', ttsText: 'Font serif màu hồng, rơi xuống rồi nảy.', voiceIdx: 0, hasVoice: true, appearance: 'drop-bounce', textFont: 'serif', textFontSize: 5.4, textColor: '#ffd1e8', textStrokeColor: '#4a102a', textStrokeWidth: 0.2, clearBefore: true });
add('text', { text: 'TEXT: Không đọc, chỉ kiểm tra hiển thị và thời lượng thủ công.', hasVoice: false, appearance: 'bounce-right', textFont: 'rounded', textFontSize: 4.5, textColor: '#bdf7ff', textStrokeColor: '#07323e', textStrokeWidth: 0.3, clearBefore: true, duration: 2.4 });
add('text', { text: 'TEXT: Mono, xuất hiện từ trái.\nDòng này kiểm tra xuống hàng.', ttsText: 'Text mono xuất hiện từ trái. Dòng này kiểm tra xuống hàng.', voiceIdx: 0, hasVoice: true, appearance: 'fly-left', textFont: 'mono', textFontSize: 4.1, textColor: '#d5ffb7', textStrokeColor: '#12310b', textStrokeWidth: 0.18, clearBefore: true });

// NOTE: toàn bộ preset, TTS, subtitle và các thông số typography.
for (const [preset, label, appearance, subtitles] of [
  ['black', 'NOTE nền đen, chữ trắng, có phụ đề.', 'normal', true],
  ['paper', 'NOTE nền giấy, font serif và padding rộng.', 'fly-right', false],
  ['sunset', 'NOTE nền hoàng hôn, hiệu ứng bay từ trên xuống.', 'fly-top', true],
  ['ocean', 'NOTE nền đại dương. Đây là câu đọc có dấu đầy đủ.', 'bounce-up', false],
  ['lavender', 'NOTE nền tím nhạt, viền bo lớn hơn.', 'bounce-left', true],
  ['glass', 'NOTE nền kính, chữ đậm và thời lượng riêng.', 'bounce-right', false],
  ['custom', 'NOTE nền tùy chỉnh để kiểm tra ô màu thủ công.', 'drop-bounce', true],
]) {
  add('note', { text: label, ttsText: label, voiceIdx: 0, hasVoice: true, appearance, clearBefore: true,
    noteBgPreset: preset, noteBgUrl: null, cardBgColor: preset === 'custom' ? '#19324a' : '#050505', textColor: preset === 'paper' ? '#2b2118' : '#ffffff',
    textFont: preset === 'paper' ? 'serif' : preset === 'glass' ? 'rounded' : 'mono', textFontSize: preset === 'glass' ? 3.2 : 2.7,
    textFontWeight: preset === 'glass' ? 800 : 500, notePadding: preset === 'paper' ? 3.5 : 1.5, cardRadius: preset === 'lavender' ? 4 : 1.2,
    noteTtsSpeed: 1, noteSubtitles: subtitles, subtitleWords: [], subtitleError: null,
  });
}

// IMAGE / VIDEO / STICKER: opacity, xoay, TTS nhiều đoạn và video đứng yên một frame.
add('image', { mediaPath: null, mediaUrl: null, mediaName: imageVideo, mediaIsVideo: true, mediaOpacity: 0.82, intrinsicMediaDuration: 1,
  text: 'Ảnh video thứ nhất. Kiểm tra khung hình đầu và nút phát một lượt.', ttsText: 'Ảnh video thứ nhất.', voiceIdx: 0, extraTtsSegments: [tts('Kiểm tra khung hình đầu và nút phát một lượt.', 0, false, true)], clearBefore: true, offsetX: 5, offsetY: -10, rotation: 4 });
add('image', { mediaPath: null, mediaUrl: null, mediaName: '1.mp4', mediaIsVideo: true, mediaOpacity: 0.58, intrinsicMediaDuration: 1,
  text: 'Video thứ hai có độ trong suốt thấp hơn và vị trí lệch sang trái.', ttsText: 'Video thứ hai có độ trong suốt thấp hơn và vị trí lệch sang trái.', voiceIdx: 0, clearBefore: true, offsetX: -8, offsetY: 9, rotation: -3 });
add('sticker', { mediaPath: null, mediaUrl: `http://localhost:8010/library-file/image/${encodeURIComponent(imageVideo2)}`, mediaName: imageVideo2, mediaIsVideo: true, mediaOpacity: 0.9, clearBefore: true, offsetX: 12, offsetY: 4, rotation: 8, duration: 2 });
add('sticker', { mediaPath: null, mediaUrl: `http://localhost:8010/library-file/image/${encodeURIComponent(imageVideo)}`, mediaName: imageVideo, mediaIsVideo: true, mediaOpacity: 0.72, clearBefore: false, offsetX: -14, offsetY: -7, rotation: -8, duration: 2 });

// AUDIO: file tuần tự, file song song, TTS thuần và echo.
add('audio', { mediaPath: null, mediaUrl: null, mediaName: 'ack.mp3', mediaIsVideo: false, intrinsicMediaDuration: 0, audioTtsMode: false, clearBefore: false, duration: 1.2 });
add('audio', { mediaPath: null, mediaUrl: null, mediaName: 'correct.mp3', mediaIsVideo: false, intrinsicMediaDuration: 0, audioTtsMode: false, clearBefore: false, parallel: true, parallelAnchorStepId: postAnchor, parallelAnchorPartIndex: 1, duration: 1.2 });
add('audio', { mediaPath: null, mediaUrl: null, mediaName: 'among.mp3', mediaIsVideo: false, intrinsicMediaDuration: 0, audioTtsMode: false, clearBefore: false, parallel: true, parallelAnchorStepId: postAnchor, parallelAnchorPartIndex: 2, duration: 1.2 });
add('audio', { audioTtsMode: true, text: 'Âm thanh giọng đọc độc lập, không hiện chữ trên màn hình.', ttsText: 'Âm thanh giọng đọc độc lập, không hiện chữ trên màn hình.', voiceIdx: 0, echo: true, audioTtsSpeed: 1, mediaPath: null, mediaUrl: null, mediaName: 'Giọng đọc độc lập có dấu', mediaIsVideo: false, clearBefore: false, duration: 2.5 });

function messengerConversation(platform, name, header, footer, lines) {
  const conversationId = `regression-${platform}-${name.toLowerCase().replace(/\s+/g, '-')}`;
  const history = [];
  for (const line of lines) {
    history.push({ side: line.side, text: line.text, ttsText: line.ttsText || line.text, voiceIdx: line.side === 'mine' ? 22 : 0, voiceOverride: !!line.voiceOverride, echo: !!line.echo, emphasisBubble: !!line.emphasisBubble, imageUrl: null, imageName: null, imageType: 'image', imageDesktopPath: null });
    const latest = history.at(-1);
    add('mess', { conversationId, groupId: conversationId, messIndex: history.length - 1, messPlatform: platform, messShowHeader: header, messShowFooter: footer,
      messPartnerName: name, messPartnerAvatar: 'messenger-default-avatar.png', messSideVoices: { theirs: 0, mine: 22 }, messTtsSpeed: 1,
      messHistory: history.map(item => ({ ...item })), text: latest.text, ttsText: latest.ttsText, voiceIdx: latest.voiceIdx, echo: latest.echo, emphasisBubble: latest.emphasisBubble, clearBefore: true,
    });
  }
}

// Mỗi nền tảng chat có tin liên tiếp hai bên, echo, nhấn mạnh và header/footer khác nhau.
messengerConversation('messenger', 'Minh Anh', true, true, [
  { side: 'theirs', text: 'Bạn ơi, bộ test mới đã có đủ card chưa?', ttsText: 'Bạn ơi, bộ kiểm thử mới đã có đủ card chưa?' },
  { side: 'theirs', text: 'Nhớ kiểm tra cả thời gian dưới tin nhắn cuối nhé.', ttsText: 'Nhớ kiểm tra cả thời gian dưới tin nhắn cuối nhé.', emphasisBubble: true },
  { side: 'mine', text: 'Có rồi, mình đang đi từng chức năng một cách kỹ hơn.', ttsText: 'Có rồi, mình đang đi từng chức năng một cách kỹ hơn.' },
  { side: 'mine', text: 'Tin này có hiệu ứng vang để kiểm tra âm thanh.', ttsText: 'Tin này có hiệu ứng vang để kiểm tra âm thanh.', echo: true },
]);
messengerConversation('instagram', 'Gia Hân', true, false, [
  { side: 'theirs', text: 'Instagram cũng cần test bong bóng chat liên tiếp.', ttsText: 'Instagram cũng cần kiểm tra bong bóng chat liên tiếp.' },
  { side: 'mine', text: 'Đúng rồi, avatar chỉ nên hiện ở tin đầu của cùng một bên.', ttsText: 'Đúng rồi, avatar chỉ nên hiện ở tin đầu của cùng một bên.' },
  { side: 'mine', text: 'Mình đã thêm một tin để kiểm tra quy tắc đó.', ttsText: 'Mình đã thêm một tin để kiểm tra quy tắc đó.', emphasisBubble: true },
  { side: 'theirs', text: 'Ổn nhé, giờ nhìn rất giống giao diện thật.', ttsText: 'Ổn nhé, giờ nhìn rất giống giao diện thật.' },
]);
messengerConversation('zalo', 'Bích Ngọc', true, true, [
  { side: 'theirs', text: 'Zalo bên mình có echo.', ttsText: 'Zalo bên mình có hiệu ứng vang.', echo: true },
  { side: 'theirs', text: 'Bong bóng Zalo phải vuông hơn và có giờ phía dưới.', ttsText: 'Bong bóng Zalo phải vuông hơn và có giờ phía dưới.' },
  { side: 'mine', text: 'Đã có rồi, tin nhắn cuối của mỗi bên sẽ hiện giờ và phút.', ttsText: 'Đã có rồi, tin nhắn cuối của mỗi bên sẽ hiện giờ và phút.' },
  { side: 'mine', text: 'Đây là tin cuối để kiểm tra định dạng thời gian.', ttsText: 'Đây là tin cuối để kiểm tra định dạng thời gian.', emphasisBubble: true },
]);

const project = {
  _format: 'threads-short-video-project', _version: 1, exportedAt: new Date().toISOString(),
  steps, stepIdCounter: nextId, ttsVoiceIdx: 0, bgAudioVolume: 0.3,
  blurIdentity: false, absoluteLayerEnabled: false, emphasisZoomMode: 'overlay', cardMoveMode: 'drag', gridSnapEnabled: false,
  bgVideoLibrary: null, bgAudioLibrary: null,
};

const output = path.join(__dirname, 'full_card_regression_test_v2.json');
fs.writeFileSync(output, JSON.stringify(project, null, 2) + '\n');
const counts = steps.reduce((total, step) => ((total[step.type] = (total[step.type] || 0) + 1), total), {});
console.log(JSON.stringify({ output, cardCount: steps.length, counts }, null, 2));
