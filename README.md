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

## Izin hilang setelah update

Setelah memasang versi baru, Bisik bisa meminta Microphone, Accessibility, dan Input Monitoring lagi.
Toggle "Bisik" di System Settings mungkin masih terlihat menyala, tetapi macOS menganggapnya app lain, sehingga menu Bisik menampilkan "BELUM diizinkan".
Penyebabnya, Bisik belum ditandatangani dengan Apple Developer ID, jadi macOS mengenalinya lewat hash biner yang berubah di setiap versi.

Cara mengatasinya jika Anda memakai `.dmg`:

1. Klik ikon mikrofon di menu bar, lalu pilih "Izin tidak sesuai? Atur ulang...". Ini menghapus entri izin lama dan meminta izin dari awal.
2. Nyalakan toggle "Bisik" di Accessibility dan Input Monitoring saat diminta, dan klik Allow pada popup Microphone.
3. Jika menu itu belum ada di versi Anda, klik baris izin yang bertanda "BELUM diizinkan" untuk membuka panelnya, pilih entri "Bisik" lalu klik `-`, dan buka Bisik lagi.

Bisik mencoba lagi sendiri sampai izin diberikan, jadi tidak perlu restart setelah itu.

Jika entri lama sulit dihapus, reset lewat terminal lalu buka Bisik lagi:

```
tccutil reset Accessibility com.bisik.app
tccutil reset ListenEvent com.bisik.app
tccutil reset Microphone com.bisik.app
```

Cara agar izin bertahan lintas update jika Anda membangun dari source:

1. Jalankan `./install.sh` sekali. `build_app.sh` membuat sertifikat self-signed di keychain khusus `~/Library/Keychains/bisik-signing.keychain-db`.
2. Tandai sertifikat itu tepercaya untuk penandatanganan kode. macOS meminta password login Anda satu kali.
   ```
   security find-certificate -c "Bisik Local Signing" -p ~/Library/Keychains/bisik-signing.keychain-db > /tmp/bisik-cert.pem
   security add-trusted-cert -r trustRoot -p codeSign /tmp/bisik-cert.pem
   rm /tmp/bisik-cert.pem
   ```
3. Jalankan `./install.sh` lagi, lalu berikan izin sekali.
   Setelah itu `install.sh` mencetak "Signing identity unchanged" dan izin tidak diminta lagi pada update berikutnya.

Cara ini hanya berlaku di Mac yang membangun app sendiri.
Untuk `.dmg` yang dibagikan ke banyak orang, satu-satunya solusi penuh adalah menandatangani dan menotarisasi app dengan Apple Developer ID (Apple Developer Program, USD 99 per tahun).

Untuk menghapus sertifikat dan keychain khusus ini, jalankan `security delete-keychain ~/Library/Keychains/bisik-signing.keychain-db`.

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
