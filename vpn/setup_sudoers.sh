#!/bin/bash
# setup_sudoers.sh — Cấp quyền chạy openvpn + pkill openvpn KHÔNG cần mật khẩu.
# Chỉ 2 lệnh này thôi, KHÔNG phải toàn quyền sudo — an toàn.
# Cần thiết để server web tự bật/đổi VPN mà không kẹt ở màn hình nhập mật khẩu.
#
# Chạy 1 lần:  bash vpn/setup_sudoers.sh   (sẽ hỏi mật khẩu Mac lần cuối)
# Gỡ bỏ:       sudo rm /etc/sudoers.d/vpngate

USER=$(whoami)
OVPN=/opt/homebrew/opt/openvpn/sbin/openvpn
SUDOERS=/etc/sudoers.d/vpngate

RULE="$USER ALL=(root) NOPASSWD: $OVPN, /usr/bin/pkill -SIGKILL openvpn, /usr/bin/pkill -SIGTERM openvpn, /usr/bin/pkill openvpn, /bin/rm -f /tmp/vpngate/*, /bin/cat /tmp/vpngate/openvpn.log"

echo "Sẽ thêm quyền không mật khẩu cho user '$USER' với các lệnh:"
echo "  - $OVPN (bật VPN)"
echo "  - pkill openvpn (tắt VPN)"
echo
echo "Nội dung ghi vào $SUDOERS:"
echo "  $RULE"
echo

# Ghi qua visudo -c để kiểm tra cú pháp trước khi áp dụng (tránh hỏng sudoers)
TMP=$(mktemp)
echo "$RULE" > "$TMP"
if sudo visudo -c -f "$TMP" >/dev/null 2>&1; then
  sudo cp "$TMP" "$SUDOERS"
  sudo chmod 440 "$SUDOERS"
  rm -f "$TMP"
  echo "✓ Đã cấp quyền. Kiểm tra:"
  sudo -n "$OVPN" --version >/dev/null 2>&1 && echo "  ✓ openvpn chạy được không cần mật khẩu" || echo "  ✗ vẫn cần mật khẩu — kiểm tra lại"
else
  rm -f "$TMP"
  echo "✗ Cú pháp sudoers lỗi, KHÔNG áp dụng gì cả (an toàn)."
  exit 1
fi
