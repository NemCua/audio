#!/bin/bash
# vpn_diag.sh — chạy openvpn ở foreground để XEM lỗi thật (không daemon).
# Chạy:  bash vpn/vpn_diag.sh
# Bấm Ctrl+C để dừng khi đã thấy đủ log. Máy tự về IP gốc khi dừng.

OVPN=/opt/homebrew/opt/openvpn/sbin/openvpn
CFG=/tmp/vpngate_test.ovpn

echo "IP gốc:"; curl -s -m 8 https://api.ipify.org; echo; echo
echo "→ Chạy openvpn foreground (Ctrl+C để dừng). Log hiện trực tiếp bên dưới:"
echo "------------------------------------------------------------------"
sudo pkill -SIGKILL openvpn 2>/dev/null; sleep 1
sudo "$OVPN" --config "$CFG" --connect-retry-max 3 --connect-timeout 20 --verb 4
