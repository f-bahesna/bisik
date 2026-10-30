# Bisik

Bisik adalah aplikasi dikte suara untuk macOS yang berjalan sepenuhnya di Mac Anda, tanpa internet dan tanpa mengirim suara ke server mana pun.
Tekan fn 2x untuk mulai merekam, bicara, lalu tekan fn sekali lagi, dan teksnya langsung ditempel ke aplikasi yang sedang aktif.
Mendukung bahasa Indonesia dan Inggris dengan deteksi bahasa otomatis.
Ringan karena hanya satu script Python di atas Whisper (MLX), tanpa Xcode atau build yang rumit.

## Instalasi

Butuh Mac Apple Silicon dan Python 3.10 sampai 3.13 (`brew install python@3.12`).

### Dari file .dmg

1. Unduh `Bisik.dmg` dari halaman [Releases](https://github.com/f-bahesna/bisik/releases).
2. Buka file tersebut, lalu seret `Bisik.app` ke folder `Applications`.
3. Buka Bisik. Karena app belum melalui notarisasi Apple, klik kanan app lalu pilih Open, atau pilih Open Anyway di System Settings > Privacy & Security.
4. Saat pertama dibuka, Bisik menyiapkan environment Python sendiri. Ini butuh internet dan beberapa menit.
5. Pilih "Buka saat login" di menu ikon mikrofon agar Bisik aktif otomatis.

### Dari source

Butuh Command Line Tools (`xcode-select --install`).

```
git clone https://github.com/f-bahesna/bisik.git
cd bisik
./install.sh
```

Script ini membuat `~/Applications/Bisik.app`, memasang dependensi Python, lalu menjalankan Bisik setiap kali Anda login.
Untuk membuat file `.dmg` sendiri, jalankan `./build_dmg.sh`. Hasilnya ada di `build/Bisik.dmg`.

Ikon mikrofon akan muncul di menu bar.
Saat pertama kali jalan, model Whisper (sekitar 1,6 GB) diunduh otomatis.

## Izin macOS

Klik Allow pada popup Microphone yang muncul.
Untuk Accessibility dan Input Monitoring, nyalakan toggle "Bisik" di System Settings > Privacy & Security saat diminta.
Bisik mencoba lagi sendiri sampai izin diberikan, jadi tidak perlu restart.

`install.sh` juga mengatur "Press fn key to" menjadi "Do Nothing".
Jika fn 2x masih membuka Dictation, matikan shortcut Dictation di System Settings > Keyboard.

## Pemakaian

Klik kolom teks di aplikasi mana saja, tekan fn 2x, bicara, lalu tekan fn sekali.
Ikon di menu bar punya menu untuk membuka log dan keluar.
Log ada di `~/Library/Logs/bisik.log`.

## Hapus

```
./uninstall.sh
```

Model yang sudah diunduh tetap ada di `~/.cache/huggingface`.

## Konfigurasi

- `BISIK_MODEL`: repo model MLX Whisper, default `mlx-community/whisper-large-v3-turbo`.
- `BISIK_LANGUAGE`: kode bahasa seperti `id` atau `en`, default deteksi otomatis.

## Menjalankan manual tanpa app

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bisik.py
```

Dalam mode ini izin diberikan ke Terminal.
