# Hilight Chat

## Bối cảnh

Mục tiêu là tạo hiệu ứng nhấn mạnh cho câu gây hài/punchline trong video. Hiện app đã có voice TTS và hiệu ứng echo, nhưng chỉ nghe echo thì đôi khi chưa đủ lực thị giác. Người xem thường cần một điểm nhấn trực quan ở đúng câu đó.

Ban đầu có hướng nghĩ tới hiệu ứng zoom vào đúng vùng chữ trong ảnh/chat. Tuy nhiên cách này khó ổn định vì:

- Nếu câu nằm trong ảnh screenshot thì app không biết text nằm ở đâu.
- Muốn zoom đúng câu sẽ cần OCR hoặc bắt người dùng chọn vùng thủ công.
- Zoom toàn cảnh card dễ crop sai, lộ vùng không mong muốn, hoặc làm người dùng phải chỉnh nhiều.
- Với nhiều giao diện chat như Messenger, Instagram, Zalo, vị trí bubble thay đổi theo nội dung nên auto zoom cần xử lý riêng từng layout.

## Hướng tốt hơn

Không zoom trực tiếp vào ảnh/card gốc. Thay vào đó tạo một lớp "bong bóng chat phóng to" nổi đè lên preview/video tại thời điểm step đó chạy.

Ý tưởng:

- Card gốc vẫn render như bình thường.
- Đến đúng step có punchline, app tạo thêm một bubble lớn ở trên cùng.
- Bubble lấy chính nội dung text của step/message đó.
- TTS và echo vẫn dùng logic cũ, không thay đổi pipeline âm thanh.
- Bubble tồn tại 100% thời lượng của card đang được nhấn mạnh.
- Qua card sau thì bubble tự biến mất và màn hình trở lại bình thường.
- Không ảnh hưởng Timeline, Layer riêng, hoặc các overlay ảnh/sticker hiện có.

## Vì sao hướng này hay hơn zoom

- Không cần OCR.
- Không cần chọn vùng thủ công.
- Câu gây hài luôn đọc được rõ.
- Hiệu ứng hợp với echo: âm thanh vang lên, đồng thời câu đó được phóng lớn trước mắt người xem.
- Dùng được cho Messenger, Instagram, Zalo, text, post/comment, và cả ảnh screenshot có TTS.
- Ít rủi ro phá layout card gốc.

## Thiết kế UI đề xuất

Trong modal tạo/sửa step, gần phần echo/TTS thêm tuỳ chọn:

- `Phóng to câu gây hài`

Không tự bật theo echo. Echo và highlight nên độc lập, vì có câu cần echo nhưng không cần phóng to.

Nếu cần mở rộng sau này, có thể thêm:

- `Kiểu highlight`: Bong bóng chat / Caption lớn / Zoom card
- `Vị trí`: Giữa màn / Dưới màn / Theo bên người gửi
- `Màu`: Auto theo platform / Đen / Xanh / Tím
- `Cỡ chữ`

Nhưng MVP chỉ cần checkbox `Phóng to câu gây hài`.

## Hành vi render

Khi step bật highlight:

- Tạo layer riêng, ví dụ `#emphasis-layer`.
- Layer nằm trên card gốc và dưới watermark/dev overlay nếu có.
- Nội dung layer là bubble chat lớn.
- Bubble hiện trong khoảng `step._start` đến `step._end`.
- Nếu step có appearance delay, có thể bắt đầu sau delay đó để khớp lúc câu thật sự hiện.
- Không kéo dài qua `gap` và không tồn tại sang step kế tiếp.

Với message card:

- Nếu message side là `mine`, bubble dùng style bên mình.
- Nếu message side là `theirs`, bubble dùng style đối phương.
- Với Instagram có thể dùng gradient tím/hồng cho tin của mình.
- Với Messenger dùng xanh.
- Với Zalo dùng xanh nhạt/trắng theo side.

Với post/comment/text/image:

- Dùng bubble đen chữ trắng mặc định.
- Nếu là ảnh có TTS, bubble lấy `step.text`.

## Data model dự kiến

```js
emphasis: {
  enabled: true,
  mode: 'bubble',
  placement: 'center'
}
```

Có thể để phẳng hơn nếu muốn đơn giản:

```js
emphasisBubble: true
```

MVP nên ưu tiên đơn giản, tránh dựng quá nhiều cấu hình sớm.

## Các file có thể cần sửa khi làm

- `threads/render_shared.js`
  - Thêm helper build emphasis bubble HTML.
  - Thêm logic tính step/message nào đang bật emphasis tại thời điểm `t`.

- `threads/index.html`
  - Thêm UI checkbox trong modal.
  - Thêm `#emphasis-layer` trong preview.
  - Render/hide layer theo `computeSceneAt(t)`.

- `threads/export_render.html`
  - Thêm cùng `#emphasis-layer` để export video khớp preview.

- `threads/export_server.py`
  - Chỉ cần sửa nếu export hiện tại chụp PNG không bao gồm layer mới hoặc cần overlay động riêng. Nếu layer nằm trong DOM preview được Playwright screenshot thì có thể không cần sửa nhiều.

## MVP đề xuất khi bắt tay làm

1. Thêm field `emphasisBubble` boolean cho các step có text/TTS.
2. Thêm checkbox `Phóng to câu gây hài` cạnh echo trong modal.
3. Preview render bubble lớn ở giữa/dưới khung trong đúng thời lượng step.
4. Export render cùng bubble.
5. Sau khi ổn mới tính auto style theo platform.

