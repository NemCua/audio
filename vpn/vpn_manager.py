#!/usr/bin/env python3
"""
vpn_manager.py — Quản lý VPN Gate để xoay IP khi CapCut chặn.

Chỉ dùng server TCP/443 (giả dạng HTTPS, ISP VN không chặn).
openvpn cần sudo — xem vpn/setup_sudoers.sh để cấp quyền không mật khẩu.

Dùng trong code:
    from vpn.vpn_manager import vpn
    vpn.ensure_list()          # tải danh sách server (cache 1h)
    vpn.rotate()               # đổi sang server 443 kế tiếp, chờ kết nối
    vpn.disconnect()           # tắt, về IP gốc
    ip = vpn.current_ip()      # IP công cộng hiện tại
    vpn.status()               # dict trạng thái
"""

import base64
import csv
import io
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

OPENVPN_BIN = "/opt/homebrew/opt/openvpn/sbin/openvpn"
VPNGATE_API = "https://www.vpngate.net/api/iphone/"
STATE_DIR   = Path("/tmp/vpngate")
LIST_CSV    = STATE_DIR / "list.csv"
PID_FILE    = STATE_DIR / "openvpn.pid"
LOG_FILE    = STATE_DIR / "openvpn.log"
CFG_FILE    = STATE_DIR / "current.ovpn"

LIST_TTL       = 3600     # cache danh sách 1h
CONNECT_TIMEOUT = 45      # chờ 1 server bắt tay tối đa (giây)


def _patch_cfg(cfg: str) -> str:
    """Thêm tương thích OpenVPN 2.7 + bỏ pull DNS."""
    # KHÔNG ignore redirect-gateway: cần route toàn máy qua VPN thì IP mới đổi
    return cfg + (
        "\n# --- compat 2.7 ---\n"
        "data-ciphers AES-128-CBC:AES-256-CBC:AES-128-GCM:AES-256-GCM\n"
        "data-ciphers-fallback AES-128-CBC\n"
        'pull-filter ignore "block-outside-dns"\n'
    )


class VPNManager:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._lock        = threading.Lock()
        self._rotate_lock = threading.Lock()   # chống gọi rotate chồng lấn
        self._servers     = []     # list[(speed, cc, ip, port, cfg_patched)]
        self._list_time   = 0.0
        self._cur_index   = -1
        self._connected   = False
        self._cur_server  = None   # (cc, ip, port)
        self._origin_ip   = None   # IP gốc (đo trước khi bật VPN lần đầu)
        self._current_vpn_ip = None  # IP thật hiện tại khi đang bật VPN

    # ── Danh sách server ──────────────────────────────────────────
    def ensure_list(self, force=False):
        with self._lock:
            if not force and self._servers and (time.time() - self._list_time) < LIST_TTL:
                return len(self._servers)
            raw = self._download_list()
            servers = self._parse_443(raw)
            if not servers:
                raise RuntimeError("Không tìm được server VPN Gate TCP/443 nào")
            self._servers   = servers
            self._list_time = time.time()
            self._cur_index = -1
            return len(servers)

    def _download_list(self) -> str:
        req = urllib.request.Request(VPNGATE_API, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode("utf-8", "replace")
        LIST_CSV.write_text(data, encoding="utf-8")
        return data

    def _parse_443(self, raw: str):
        lines = [l for l in raw.splitlines(keepends=True)
                 if not l.startswith(("*", "#"))]
        reader = csv.reader(io.StringIO("".join(lines)))
        out = []
        # Chỉ TCP + port phổ biến (443 HTTPS, 995 POP3S) → ít bị ISP chặn,
        # nhưng gom nhiều subnet hơn hẳn so với riêng 443.
        ALLOWED_PORTS = {443, 995, 992, 465}
        for row in reader:
            if len(row) < 15:
                continue
            try:
                speed = int(row[4]); cc = row[6]; ip = row[1]
                cfg = base64.b64decode(row[14]).decode("utf-8", "ignore")
                if "proto tcp" not in cfg.lower():
                    continue
                m = re.search(r"remote\s+\S+\s+(\d+)", cfg)
                port = int(m.group(1)) if m else 0
                if port not in ALLOWED_PORTS:
                    continue
                out.append((speed, cc, ip, port, _patch_cfg(cfg)))
            except Exception:
                continue
        # ĐA DẠNG SUBNET: CapCut chặn cả subnet /24, nên không xếp toàn bộ
        # server cùng dải (vd 219.100.37.x) cạnh nhau. Xen kẽ theo subnet để
        # mỗi lần rotate nhảy sang dải IP khác hẳn.
        out.sort(key=lambda s: -s[0])   # nhanh nhất trước
        by_subnet: dict[str, list] = {}
        for s in out:
            subnet = ".".join(s[2].split(".")[:3])
            by_subnet.setdefault(subnet, []).append(s)
        # Round-robin: lấy 1 server từ mỗi subnet mỗi vòng
        buckets = list(by_subnet.values())
        interleaved = []
        while buckets:
            for b in buckets:
                interleaved.append(b.pop(0))
            buckets = [b for b in buckets if b]
        return interleaved

    # ── Kết nối / đổi / ngắt ──────────────────────────────────────
    def _kill_openvpn(self):
        subprocess.run(["sudo", "-n", "pkill", "-SIGKILL", "openvpn"],
                       capture_output=True)
        time.sleep(1.5)

    def disconnect(self):
        with self._lock:
            self._kill_openvpn()
            self._connected  = False
            self._cur_server = None
            self._current_vpn_ip = None
            # LOG_FILE/PID_FILE do root tạo → xóa qua sudo; CFG_FILE do user
            subprocess.run(["sudo", "-n", "/bin/rm", "-f", str(LOG_FILE), str(PID_FILE)],
                           capture_output=True)
            try: CFG_FILE.unlink()
            except FileNotFoundError: pass

    def _read_log(self) -> str:
        """Đọc log openvpn. File do root tạo → đọc qua sudo cat nếu cần."""
        try:
            return LOG_FILE.read_text(errors="replace")
        except (PermissionError, FileNotFoundError):
            r = subprocess.run(["sudo", "-n", "/bin/cat", str(LOG_FILE)],
                               capture_output=True, text=True)
            return r.stdout or ""

    def connect_index(self, idx: int) -> bool:
        """Kết nối server thứ idx trong list. True nếu bắt tay thành công."""
        if idx < 0 or idx >= len(self._servers):
            return False
        speed, cc, ip, port, cfg = self._servers[idx]
        CFG_FILE.write_text(cfg, encoding="utf-8")
        self._kill_openvpn()
        # File log/pid có thể do root tạo lần trước → xóa qua sudo
        subprocess.run(["sudo", "-n", "/bin/rm", "-f", str(LOG_FILE), str(PID_FILE)],
                       capture_output=True)

        subprocess.run([
            "sudo", "-n", OPENVPN_BIN,
            "--config", str(CFG_FILE),
            "--connect-retry-max", "2", "--connect-timeout", "12",
            "--writepid", str(PID_FILE),
            "--log", str(LOG_FILE),
            "--daemon",
        ], capture_output=True)

        # Chờ "Initialization Sequence Completed"
        t0 = time.time()
        while time.time() - t0 < CONNECT_TIMEOUT:
            time.sleep(2)
            log = self._read_log()
            if "Initialization Sequence Completed" in log:
                self._connected  = True
                self._cur_index  = idx
                self._cur_server = (cc, ip, port)
                return True
            if "AUTH_FAILED" in log:
                break
        return False

    def rotate(self, max_try=8) -> dict:
        """Đổi sang server khác subnet. IP mới phải KHÁC cả IP gốc lẫn IP hiện tại."""
        # Chống gọi chồng: nếu đang rotate thì bỏ qua (pipeline gọi liên tục)
        if not self._rotate_lock.acquire(blocking=False):
            print("  [VPN] Đang rotate rồi, bỏ qua lệnh chồng lấn")
            return self.status()
        try:
            with self._lock:
                if not self._servers:
                    raise RuntimeError("Chưa tải danh sách server (gọi ensure_list)")
                if self._origin_ip is None:
                    self._origin_ip = self._raw_ip()
                prev_ip = self._current_vpn_ip or self._origin_ip
                n = len(self._servers)
                start = (self._cur_index + 1) % n
                for k in range(min(max_try, n)):
                    idx = (start + k) % n
                    cc, ip, port = self._servers[idx][1], self._servers[idx][2], self._servers[idx][3]
                    subnet = ".".join(ip.split(".")[:3])
                    print(f"  [VPN] Thử server {idx}: {cc} {ip}:{port} (subnet {subnet})...")
                    if not self.connect_index(idx):
                        print(f"  [VPN] Server {idx} không bắt tay được, thử tiếp")
                        continue
                    # Đợi route ổn định rồi đo IP THẬT (retry để tránh IP cache cũ)
                    new_ip = self._wait_new_ip(exclude={self._origin_ip, prev_ip})
                    if new_ip:
                        self._current_vpn_ip = new_ip
                        print(f"  [VPN] ✓ IP mới → {new_ip} ({cc})")
                        return self.status()
                    print(f"  [VPN] IP chưa đổi thật sau khi nối server {idx}, thử server khác")
                raise RuntimeError(f"Thử {min(max_try,n)} server đều không đổi được IP")
        finally:
            self._rotate_lock.release()

    def _wait_new_ip(self, exclude: set, timeout=15) -> str | None:
        """Đợi IP công cộng đổi sang giá trị KHÔNG nằm trong exclude."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            ip = self._raw_ip()
            if ip and ip not in exclude:
                return ip
            time.sleep(2)
        return None

    def ensure_connected(self) -> dict:
        """Nếu chưa có VPN thì rotate lần đầu."""
        if not self._connected:
            self.ensure_list()
            return self.rotate()
        return self.status()

    # ── Trạng thái ────────────────────────────────────────────────
    def _raw_ip(self) -> str | None:
        # cache-buster ?t= tránh CDN/keep-alive trả IP cũ khi route vừa đổi
        cb = int(time.time() * 1000)
        for url in (f"https://api.ipify.org?t={cb}",
                    f"https://ifconfig.me/ip?t={cb}",
                    f"https://icanhazip.com?t={cb}"):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "curl/8",
                                  "Cache-Control": "no-cache", "Connection": "close"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    ip = r.read().decode().strip()
                    if ip and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                        return ip
            except Exception:
                continue
        return None

    def current_ip(self) -> str | None:
        return self._raw_ip()

    def status(self) -> dict:
        alive = False
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                os.kill(pid, 0)
                alive = True
            except (ProcessLookupError, ValueError, PermissionError):
                alive = bool(subprocess.run(["pgrep", "openvpn"],
                             capture_output=True).stdout.strip())
        else:
            alive = bool(subprocess.run(["pgrep", "openvpn"],
                         capture_output=True).stdout.strip())
        self._connected = alive and self._connected
        return {
            "connected":  self._connected and alive,
            "server":     (f"{self._cur_server[0]} {self._cur_server[1]}:{self._cur_server[2]}"
                           if self._cur_server else None),
            "origin_ip":  self._origin_ip,
            "server_count": len(self._servers),
        }


# Singleton dùng chung toàn app
vpn = VPNManager()


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "list":
        n = vpn.ensure_list(force=True)
        print(f"{n} server TCP/443")
        for i, s in enumerate(vpn._servers[:10]):
            print(f"  [{i}] {s[1]} {s[2]}:443  {s[0]/1e6:.0f}Mbps")
    elif cmd == "rotate":
        vpn.ensure_list()
        print("IP trước:", vpn.current_ip())
        print(vpn.rotate())
        print("IP sau:", vpn.current_ip())
    elif cmd == "off":
        vpn.disconnect()
        print("Đã tắt. IP:", vpn.current_ip())
    else:
        print("IP:", vpn.current_ip())
        print(vpn.status())
