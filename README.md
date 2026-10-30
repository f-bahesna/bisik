# Bisik

Bisik adalah aplikasi dikte suara untuk macOS yang berjalan sepenuhnya di Mac Anda, tanpa internet dan tanpa mengirim suara ke server mana pun.
Tekan fn 2x untuk mulai merekam, bicara, lalu tekan fn sekali lagi, dan teksnya langsung ditempel ke aplikasi yang sedang aktif.
Mendukung bahasa Indonesia dan Inggris dengan deteksi bahasa otomatis.
Ringan karena hanya satu script Python di atas Whisper (MLX), tanpa Xcode atau build yang rumit.

## Setup

Butuh Mac Apple Silicon dan Python 3.12.

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bisik.py
```

Saat pertama kali dijalankan, model Whisper (sekitar 1,6 GB) diunduh otomatis.

## Jalan otomatis saat login

```
./install.sh
```

Script ini membuat venv, memasang dependensi, lalu mendaftarkan LaunchAgent `com.bisik.agent`.
Bisik akan aktif setiap login dan log-nya ada di `~/Library/Logs/bisik.log`.
Untuk menghentikan dan menghapus LaunchAgent, jalankan `./uninstall.sh`.

Saat jalan lewat LaunchAgent, izin macOS harus diberikan ke binary Python di venv, bukan ke Terminal.
`install.sh` mencetak path binary yang perlu ditambahkan.

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
