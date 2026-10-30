# Bisik

Bisik adalah aplikasi dikte suara untuk macOS yang berjalan sepenuhnya di Mac Anda, tanpa internet dan tanpa mengirim suara ke server mana pun.
Tekan fn 2x untuk mulai merekam, bicara, lalu tekan fn sekali lagi, dan teksnya langsung ditempel ke aplikasi yang sedang aktif.
Mendukung bahasa Indonesia dan Inggris dengan deteksi bahasa otomatis.
Ringan karena hanya satu script Python di atas Whisper (MLX), tanpa Xcode atau build yang rumit.

Ingin ikut berkontribusi, termasuk membawa Bisik ke Windows?
Lihat [rencana pengembangan](#rencana-pengembangan).

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

## Rencana pengembangan

Daftar ini terbuka untuk kontributor.
Centang item yang selesai di pull request Anda.
Item bertanda **(mudah)** cocok untuk kontributor baru.

### Dukungan Windows (prioritas)

Saat ini Bisik hanya berjalan di macOS karena sebagian besar bagiannya memakai API Apple.
Tabel berikut memetakan apa yang perlu diganti.

| Bagian | Sekarang (macOS) | Dibutuhkan di Windows |
| --- | --- | --- |
| Transkripsi | `mlx-whisper`, khusus Apple Silicon | `faster-whisper` atau `whisper.cpp` (CPU atau CUDA) |
| Tombol pintas | Event tap Quartz untuk fn 2x | Windows tidak meneruskan tombol Fn ke aplikasi, jadi pilih tombol lain (misalnya Ctrl atau Right Alt 2x) lewat hook global |
| Tempel teks | `pbcopy` lalu Cmd+V lewat Quartz | Clipboard lalu Ctrl+V lewat `SendInput` atau `pynput` |
| Rekam audio | `sounddevice` | Dipakai apa adanya, sudah lintas platform |
| Suara umpan balik | `afplay` | `winsound` |
| Overlay dan menu | App Swift/AppKit (`app/Bisik.swift`) | Ikon tray dan overlay, misalnya `pystray` dengan Tkinter atau Qt, atau app .NET |
| Izin | Microphone, Accessibility, Input Monitoring | Umumnya hanya izin mikrofon |
| Jalan saat login | LaunchAgent dan `SMAppService` | Folder Startup atau Task Scheduler |
| Pemasangan | `.dmg` | Installer (PyInstaller dengan Inno Setup atau MSI) |
| Tes dan CI | pytest di macOS, tes integrasi memakai `say` | GitHub Actions `windows-latest`, suara uji lewat SAPI |

Urutan pengerjaan yang disarankan:

- [ ] Pisahkan `bisik.py` menjadi backend per platform (hotkey, tempel, suara, overlay) di balik antarmuka yang sama, tanpa mengubah perilaku di macOS.
- [ ] Pilih dan implementasikan tombol pintas pengganti fn 2x untuk Windows.
- [ ] Tambah backend transkripsi non-Apple (`faster-whisper`) yang dipilih otomatis sesuai platform.
- [ ] Ikon tray dan overlay status (merekam, memproses) di Windows.
- [ ] Clipboard dan tempel teks di Windows.
- [ ] Jalan saat login dan installer Windows.
- [ ] CI Windows yang menjalankan tes unit.

Rancangan backend yang sama juga membuka jalan untuk Linux.

### Pengalaman pakai

- [ ] Pilih bahasa dan model dari menu **(mudah)**. Sekarang hanya lewat variabel lingkungan `BISIK_MODEL` dan `BISIK_LANGUAGE`.
- [ ] Kembalikan isi clipboard sebelumnya setelah menempel **(mudah)**. Sekarang transkrip menimpa clipboard.
- [ ] Batas durasi rekaman **(mudah)**. Audio disimpan di memori tanpa batas.
- [ ] Status "Memuat model..." di overlay dan progres unduhan model. Sekarang hanya terlihat di log.
- [ ] Mode tahan tombol (push-to-talk) sebagai alternatif double-tap.
- [ ] Batalkan rekaman dengan Esc.
- [ ] Pilih mikrofon.
- [ ] Riwayat transkrip di menu.
- [ ] Teks antarmuka dalam beberapa bahasa. Sekarang tulisan Indonesia tertanam di kode.

### Akurasi

- [ ] Deteksi suara (VAD) sungguhan sebagai pengganti heuristik volume dan daftar frasa karangan. Batasan yang diketahui: bisikan "thank you" yang sangat pelan ikut dibuang.
- [ ] Kosakata kustom dan koreksi hasil, misalnya nama, singkatan, dan tanda baca otomatis.
- [ ] Penanganan campuran Indonesia dan Inggris dalam satu kalimat. Sekarang bahasa dideteksi per rekaman.
- [ ] Transkripsi bertahap saat masih bicara.
- [ ] Mode terjemahan.

### Kekokohan

- [ ] Putuskan perilaku ketukan fn yang menghentikan rekaman. Sekarang ketukan itu dihitung sebagai ketukan pertama double-tap, sehingga satu ketukan lagi dalam 0,4 detik memulai rekaman baru. Perilaku ini dikunci oleh tes `test_the_stop_tap_counts_as_the_first_tap_of_a_new_double_tap`.
- [ ] Pulihkan status fn yang tersangkut jika event lepas tombol terlewat saat event tap dinonaktifkan sistem.

### Rilis dan kualitas

- [ ] CI di GitHub Actions: `ruff`, `pytest` dengan coverage minimal 90%, `shellcheck`, dan kompilasi Swift **(mudah)**.
- [ ] Tandatangani dan notarisasi dengan Apple Developer ID, agar izin bertahan lintas update dan peringatan "unidentified developer" hilang.
- [ ] Tes otomatis untuk peluncur Swift. Sekarang hanya kompilasi tanpa peringatan dan uji manual.
- [ ] Distribusi lewat Homebrew cask.
- [ ] Pembaruan otomatis atau notifikasi versi baru.

### Cara berkontribusi

1. Fork repo ini lalu buat branch dari `main`.
2. Kerjakan satu item, tambahkan tes, dan pastikan semua pemeriksaan di bagian [Pengujian](#pengujian) lolos.
3. Buka pull request yang menyebut item to-do yang Anda kerjakan.

## Pengujian

Pasang dependensi pengembangan sekali.

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Tes unit memakai mock untuk macOS dan audio, jadi cepat dan tidak butuh mikrofon atau model.
Coverage harus minimal 90% (saat ini 100% untuk `bisik.py`).

```
.venv/bin/python -m pytest --cov=bisik
```

Tes integrasi memakai model Whisper asli dan suara dari `say` (Inggris dan Indonesia), termasuk keheningan dan derau ruangan.
Model harus sudah diunduh, dan tes dilewati otomatis jika model atau suara tidak tersedia.

```
.venv/bin/python -m pytest -m integration
```

Pemeriksaan gaya dan kualitas lainnya:

```
.venv/bin/ruff check . && .venv/bin/ruff format --check .
shellcheck *.sh
swiftc -swift-version 5 -warnings-as-errors app/Bisik.swift -o /tmp/bisik-check
```

Peluncur Swift (`app/Bisik.swift`) belum punya tes otomatis karena sebagian besar berupa UI AppKit dan izin macOS.
Bagian itu baru diperiksa lewat kompilasi tanpa peringatan dan pengujian manual.

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
