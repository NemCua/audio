"""Kiem tra moi getElementById('x') trong trang deu co phan tu id="x".

Loi thieu id lam JavaScript dung ngay khi tai trang (vd: khong nap duoc video).
Chay: python tools/check_html_ids.py static_translate/index.html
"""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "static_translate/index.html")
html = path.read_text(encoding="utf-8-sig")

declared = set(re.findall(r'\bid\s*=\s*["\']([^"\'$]+)["\']', html))
# id sinh dong trong template string, vd id="fix-row-${i}"
dynamic_prefixes = set(re.findall(r'\bid\s*=\s*["\']([^"\'$]*)\$\{', html))

used = re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", html)
used_prefix = re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\+", html)

missing = sorted({i for i in used if i not in declared})
missing += sorted({
    p + "*" for p in used_prefix
    if p not in dynamic_prefixes and not any(d.startswith(p) for d in declared)
})

if missing:
    print("THIEU phan tu HTML cho cac id:", ", ".join(missing))
    sys.exit(1)
print(f"OK: {len(set(used))} id deu ton tai trong {path}")
