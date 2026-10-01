# Persistent Agent Workspace

Local-first backend foundation for persistent AI teammates inspired by the product behavior in `PRD.md`.

## Cara kerja tim Bot

Setiap Bot memiliki percakapan pribadi, peran, job, dan daftar skill terpisah. Skill menentukan kemampuan yang tersedia saat Bot menjalankan tugas:

- `workspace_admin`: membuat, mengubah, dan mengarsipkan Bot.
- `job_manager`: membuat dan memperbarui job.
- `coordination`: mendelegasikan tugas ke Bot lain dan memberi pembaruan di grup.

Pilih skill saat membuat Bot atau dari chat pribadi Bot. Gunakan **Grup kerja** untuk mengirim arahan yang diterima semua anggota grup. Bot yang menerima arahan membuat Run sendiri dan membalas ke grup. Penghapusan Bot oleh AI diwujudkan sebagai arsip agar riwayat kerja tidak hilang; penghapusan permanen belum disediakan.

## Jalankan backend

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

API tersedia di `http://127.0.0.1:8000`, dengan dokumentasi di `/docs`. Tambahkan `OPENROUTER_API_KEY` ke `.env`. Backend memuat `.env` dari root proyek otomatis (dan perintah di atas juga menyertakan `--env-file .env` untuk Uvicorn).

The application never passes the OpenRouter credential into a sandbox. Each Bot can use a persistent workspace rooted at `WORKSPACE_ROOT` to list, read, and write text files, and can save or recall durable Bot memory. External or destructive actions still require explicit approval.

## Jalankan frontend

Di terminal kedua:

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Buka `http://127.0.0.1:3000`. Frontend memakai `NEXT_PUBLIC_API_BASE_URL` dari `.env.local`; arahkan nilainya ke endpoint backend yang berjalan.
Untuk memakai Bot, pilih **Buka percakapan**, tulis tugas, lalu pilih **Kirim tugas**. Jawaban model tersimpan dan ditampilkan di riwayat percakapan.

Untuk menjalankan build produksi:

```bash
npm run build
npm run start -- --hostname 127.0.0.1 --port 3000
```

Next.js dipatok ke versi `15.5.26` karena versi `16.3.7` yang terpasang sebelumnya gagal memuat compiler SWC di lingkungan ini.

## Jalankan dengan Docker Compose

```bash
cp .env.example .env
DOCKER_HOST=unix:///var/run/docker.sock docker compose up --build
```

## Verifikasi

```bash
.venv/bin/python -m pytest -q
curl http://127.0.0.1:8000/health
```

Install the browser binary only when browser automation is enabled:

```bash
.venv/bin/playwright install chromium
```
