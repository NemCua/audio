#!/bin/bash
# vpn_test_on.sh — TEST bật VPN Gate 1 server, xem IP có đổi không.
# Chạy trên Terminal của bạn:  bash vpn/vpn_test_on.sh
# (sẽ hỏi mật khẩu Mac vì openvpn cần quyền sửa route)

OVPN=/opt/homebrew/opt/openvpn/sbin/openvpn
CFG=/tmp/vpngate_test.ovpn

if [ ! -f "$CFG" ]; then
  echo "Chưa có config. Chạy lại bước tải danh sách VPN Gate trước."
  exit 1
fi

echo "IP GỐC hiện tại:"
curl -s -m 8 https://api.ipify.org; echo
echo

echo "→ Kết nối VPN Gate (nhập mật khẩu Mac nếu được hỏi)..."
sudo pkill -SIGKILL openvpn 2>/dev/null; sleep 1
sudo "$OVPN" --config "$CFG" \
  --connect-retry-max 3 --connect-timeout 15 \
  --writepid /tmp/vpngate.pid --log /tmp/vpngate.log --daemon

echo "→ Đợi VPN bắt tay (tối đa 30s)..."
for i in $(seq 1 30); do
  sleep 2
  if grep -q "Initialization Sequence Completed" /tmp/vpngate.log 2>/dev/null; then
    echo "✓ VPN đã kết nối!"
    break
  fi
  if grep -qi "error\|cannot\|failed" /tmp/vpngate.log 2>/dev/null; then
    echo "✗ Có lỗi khi kết nối. Xem /tmp/vpngate.log"; break
  fi
  printf "."
done
echo

echo "IP SAU KHI BẬT VPN (phải KHÁC ip gốc):"
curl -s -m 12 https://api.ipify.org; echo
echo
echo "→ Nếu IP đã đổi = thành công. Tắt VPN về gốc bằng:  bash vpn/vpn_off.sh"
