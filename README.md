# Bisik

Dikte suara ke teks di macOS, lokal dan offline.
Tekan fn 2x, bicara, tekan fn sekali lagi, dan teks langsung ditempel ke aplikasi yang sedang aktif.
Mendukung bahasa Indonesia dan Inggris dengan deteksi bahasa otomatis.

## Setup

Butuh Mac Apple Silicon dan Python 3.12.

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bisik.py
```

Saat pertama kali dijalankan, model Whisper (sekitar 1,6 GB) diunduh otomatis.

## Izin macOS

Beri izin berikut untuk aplikasi Terminal di System Settings > Privacy & Security.

- Microphone
- Accessibility
- Input Monitoring

Di System Settings > Keyboard, atur "Press fn key to" menjadi "Do Nothing".
Matikan juga shortcut Dictation supaya tidak bentrok dengan fn 2x.

## Konfigurasi

- `BISIK_MODEL`: repo model MLX Whisper, default `mlx-community/whisper-large-v3-turbo`.
- `BISIK_LANGUAGE`: kode bahasa seperti `id` atau `en`, default deteksi otomatis.
