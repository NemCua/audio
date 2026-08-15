# Phát hành Threads Video Maker cho macOS và Windows

GitHub Actions tạo hai bản:

- `arm64`: Mac Apple Silicon (M1/M2/M3/M4 và mới hơn).
- `x64`: Mac Intel.
- `Setup x64`: bộ cài Windows 64-bit.
- `Portable x64`: bản Windows 64-bit chạy trực tiếp, không cần cài.

## Tạo bản v1.0.0

Sau khi branch `threads_all_browser` đã được đẩy lên GitHub và build thường chạy thành công:

```bash
git tag desktop-v1.0.0
git push origin desktop-v1.0.0
```

Workflow sẽ tạo GitHub Release và đính kèm DMG/ZIP để tải. Không dùng lại một
tag đã phát hành; bản tiếp theo phải tăng version trong `package.json` và dùng
tag mới.

## Trạng thái chữ ký macOS

Workflow hiện đặt `CSC_IDENTITY_AUTO_DISCOVERY=false`, vì repository chưa có
chứng thư Developer ID và thông tin notarization. Người thử nghiệm có thể cần
nhấp chuột phải vào app và chọn **Open**. Trước khi bán rộng rãi cần bổ sung ký
Developer ID, notarization và icon `.icns` riêng.
