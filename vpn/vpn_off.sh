#!/bin/bash
# vpn_off.sh — TẮT KHẨN CẤP, đưa máy về IP gốc.
# Kill mọi tiến trình openvpn → route tự khôi phục về mặc định.
# Chạy: bash vpn/vpn_off.sh   (sẽ hỏi mật khẩu sudo)

echo "IP trước khi tắt:"
curl -s -m 8 https://api.ipify.org; echo

echo "→ Đang kill mọi tiến trình openvpn..."
sudo pkill -SIGTERM openvpn 2>/dev/null
sleep 2
# Còn sống thì kill cứng
sudo pkill -SIGKILL openvpn 2>/dev/null
sleep 1

# Dọn file trạng thái nếu có
rm -f /tmp/vpngate_current.ovpn /tmp/vpngate.pid 2>/dev/null

# Flush route thừa openvpn hay để lại (an toàn: chỉ xóa route qua tun)
for iface in $(ifconfig -l | tr ' ' '\n' | grep '^utun'); do
  sudo route -n flush 2>/dev/null >/dev/null
  break
done

echo "→ Đã tắt. IP hiện tại:"
curl -s -m 8 https://api.ipify.org; echo
echo "Nếu IP vẫn chưa về gốc, chờ 5s rồi chạy lại script này."
