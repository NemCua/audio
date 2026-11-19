# Khoi phuc du an sau khi clone

Nhanh `snapshot-local-files` chua cac file chua tung duoc commit (threads/, srt/, ...).

1. `git clone https://github.com/NemCua/audio.git && cd audio`
2. `git checkout snapshot-local-files` (hoac merge vao nhanh dang lam)
3. `git clone https://github.com/davidteather/TikTok-Api.git TikTok-Api`  (thu vien ngoai, khong nam trong repo)
4. Tao `.env` tu `.env.example` va dien lai khoa API (file .env that KHONG nam tren GitHub)
5. Cac nhanh `stash-*-wip` la thay doi dang lam do luu tu git stash; `local-backup-*` la ban local cua nhanh bi lech remote.
