# Fix loi da xu ly

## 1. Cue dau bi bat dau sai o 0.0s

### Hien tuong

- Subtitle/dub tieng Viet bat dau tu `00:00:00,000`.
- Kiem tra bang cong cu ngoai thi cau noi dau tien that ra bat dau khoang giay thu 3.
- Sau khi render, tieng Viet bi vao som hon hinh/ngu am goc o doan dau.

### Nguyen nhan

- Groq Whisper co luc tra segment dau tien bat dau tu `0.0s`, du audio that chua co loi noi.
- Khi app dung moc nay de tao JSON/SRT va TTS, doan TTS dau bi dat qua som.

### Cach khac phuc da lam

- Trong `translate_video.py`, ham STT da yeu cau timestamp theo `word` va `segment`.
- Neu cue dau tien van bat dau gan `0s` va keo dai bat thuong hon `3s`, app tu uoc luong lai start time cua cue dau theo do dai cau.
- Voi case hien tai, cue dau da ve khoang `2.9s`, khop hon voi thoi diem noi that.

### Luu y

- Job cu da nhan dang truoc khi sua code se van giu timestamp cu.
- Can chay lai buoc nhan dang/dich de JSON/SRT moi an fix nay.

## 2. Electron bao loi EPIPE: broken pipe

### Hien tuong

Popup loi:

```text
A JavaScript error occurred in the main process
Uncaught Exception:
Error: EPIPE: broken pipe, write
...
at console.log
...
electron/main.js:57:77
```

### Nguyen nhan

- Electron main process dang `console.log` log cua server Python ra terminal.
- Khi terminal bi dong hoac pipe stdout bi mat, lenh ghi log vao stdout nem loi `EPIPE`.
- Loi nay khong phai loi render/TTS, ma la loi ghi log cua Electron.

### Cach khac phuc da lam

- Them handler cho `process.stdout`, `process.stderr` va `uncaughtException` trong `electron/main.js` de bo qua rieng loi `EPIPE`.
- Doi `safeLog()` thanh no-op, khong ghi log server ra stdout nua.
- Log server van duoc giu trong bien `serverLog` noi bo de hien khi server that su thoat loi.
