# binance-ai-future

Sistem trading **Binance USDⓈ-M Futures** berbantuan AI dengan standar
pengembangan profesional: memindai **semua simbol perpetual** yang tersedia di
Binance (tidak terpaku pada satu simbol), menganalisisnya dengan indikator
teknikal, meminta penilaian kedua dari **AI Router BangunWeb**, lalu
mengeksekusi order hanya setelah lolos **manajemen risiko** yang ketat.

Tersedia dalam dua antarmuka di atas core yang sama:

- **Web UI** — dashboard responsif, nyaman dibuka dari desktop maupun browser
  ponsel (iOS/Android).
- **Terminal CLI** — perintah cepat untuk scanning, analisis, dan trading.

> ⚠️ **Peringatan risiko.** Trading futures berlevrage dapat menghilangkan
> seluruh modal Anda. Software ini adalah alat bantu analisis & eksekusi,
> bukan nasihat keuangan dan bukan jaminan profit. Mode bawaan adalah
> **dry-run** (simulasi). Gunakan mode live hanya dengan dana yang siap Anda
> rugikan, dan selalu uji dulu di testnet.

---

## Fitur Utama

- **Multi-simbol** — daftar simbol dibaca langsung dari `exchangeInfo`
  Binance saat runtime; simbol baru otomatis ikut terpindai.
- **Analisis teknikal lengkap** — EMA, RSI, MACD, Bollinger Bands, ATR, dan
  skor tren teragregasi per simbol.
- **Penilaian AI** — snapshot pasar dikirim ke
  [AI Router BangunWeb](https://bangunweb.com/ai/docs) (OpenAI-compatible,
  model `bw-focus` secara bawaan) dan dikembalikan sebagai keputusan JSON
  terstruktur: sinyal, keyakinan, stop loss, take profit, dan alasan.
- **Manajemen risiko wajib** — position sizing berbasis % risiko, batas
  leverage, batas jumlah posisi, dan batas rugi harian. Tidak ada jalur
  eksekusi yang melewati modul ini.
- **Eksekusi aman** — entri market/limit disertai Stop Loss & Take Profit
  otomatis; mode dry-run mensimulasikan semuanya tanpa menyentuh akun.
- **Cross-platform** — Python murni; berjalan di macOS, Windows, Linux, dan
  Android (Termux). Untuk iOS: jalankan server di komputer/VPS, buka Web UI
  dari Safari/Chrome ponsel.

## Arsitektur Singkat

Lihat **[STRUKTUR.MD](STRUKTUR.MD)** untuk peta folder, tanggung jawab tiap
modul, diagram aliran data, dan panduan menambah fitur.

```text
Binance REST ─► binance_client ─► indicators ─► strategy ─► risk ─► executor
                                      ▲             │
                                 ai_client (BangunWeb AI)
CLI (terminal.py) dan Web UI (web/app.py) adalah dua kulit di atas core ini.
```

## Persyaratan

- Python **3.10+**
- Akun Binance dengan **Futures USDⓈ-M aktif** + API key berizin Futures
- Kunci **AI Router BangunWeb** (bentuk `bwr_live_...`) —
  [dokumentasi resmi](https://bangunweb.com/ai/docs)

## Instalasi

### macOS / Linux

```bash
git clone https://github.com/devcoderama/binance-ai-future.git
cd binance-ai-future
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # lalu isi .env dengan kunci Anda
```

### Windows (PowerShell)

```powershell
git clone https://github.com/devcoderama/binance-ai-future.git
cd binance-ai-future
py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # lalu isi .env dengan kunci Anda
```

### Android (Termux)

```bash
pkg install python git
git clone https://github.com/devcoderama/binance-ai-future.git
cd binance-ai-future
pip install -r requirements.txt
cp .env.example .env
```

### iOS

iOS tidak menjalankan Python secara native. Jalankan proyek ini di
komputer/VPS, mulai Web UI dengan `WEB_HOST=0.0.0.0`, lalu buka
`http://<ip-server>:8000` dari browser iPhone Anda.

## Konfigurasi `.env`

| Variabel | Keterangan |
|---|---|
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | API key Binance (lihat keamanan di bawah) |
| `BINANCE_TESTNET` | `true` = testnet dana virtual, `false` = mainnet |
| `BANGUNWEB_API_KEY` | Kunci AI Router BangunWeb (`bwr_live_...`) |
| `BANGUNWEB_MODEL` | `bw-swift` / `bw-focus` (bawaan) / `bw-pro` / `bw-apex` |
| `TRADING_MODE` | `dry_run` (bawaan) atau `live` |
| `RISK_PER_TRADE_PCT` | % saldo yang dirisikokan per posisi (bawaan 1.0) |
| `MAX_LEVERAGE` | Batas leverage (bawaan 5) |
| `MAX_OPEN_POSITIONS` | Batas posisi terbuka (bawaan 3) |
| `DAILY_LOSS_LIMIT_PCT` | Berhenti trading bila rugi harian mencapai % ini |
| `DEFAULT_INTERVAL` | Interval candle bawaan (bawaan `15m`) |
| `WEB_HOST` / `WEB_PORT` | Alamat & port Web UI (bawaan `0.0.0.0:8000`) |

### Keamanan API key Binance (wajib)

1. Buat key **setelah** akun Futures Anda aktif (key yang dibuat sebelumnya
   tidak bisa diberi izin Futures).
2. Aktifkan **Restrict access to trusted IPs only** dan daftarkan IP mesin
   yang menjalankan program ini — tanpa ini izin Futures tidak bisa dicentang.
3. Centang **Enable Futures** saja. **Jangan pernah** mengaktifkan
   **Enable Withdrawals** untuk key yang dipakai program apa pun.
4. Secret key hanya tampil sekali — simpan langsung ke `.env` Anda, jangan
   dibagikan ke siapa pun, jangan di-commit ke Git (`.env` sudah di-ignore).

## Penggunaan

### Web UI

```bash
python run.py web
# buka http://localhost:8000  (atau http://<ip-lan>:8000 dari ponsel)
```

Dashboard menampilkan saldo & posisi, pemindai sinyal semua simbol, panel
analisis AI per simbol, dan tombol eksekusi (selalu melewati RiskManager;
dalam mode dry-run hasilnya berupa simulasi yang ditandai jelas).

### Terminal CLI

```bash
python run.py cli balance                 # saldo & ringkasan akun futures
python run.py cli symbols                 # semua simbol perpetual USDT aktif
python run.py cli scan --top 10           # pindai & peringkatkan sinyal
python run.py cli analyze BTCUSDT         # analisis teknikal + AI satu simbol
python run.py cli positions               # posisi terbuka saat ini
python run.py cli trade BTCUSDT --side LONG   # eksekusi (dry-run kecuali live)
python run.py cli close BTCUSDT           # tutup posisi satu simbol
python run.py cli ai-account              # sisa token AI BangunWeb Anda
```

Perintah `trade` dalam mode `live` meminta konfirmasi pengetikan ulang simbol
sebelum order dikirim.

## Alur Satu Siklus Trading

1. **Scan** — klines semua simbol diambil, indikator dihitung, skor teknikal
   dibuat, kandidat terbaik dipilih.
2. **AI review** — snapshot kandidat dikirim ke BangunWeb AI; AI mengembalikan
   keputusan JSON (LONG/SHORT/NEUTRAL, confidence, SL, TP, alasan, risiko).
3. **Risk check** — RiskManager memvalidasi batas-batas dan menghitung ukuran
   posisi dari jarak stop loss.
4. **Execute** — Executor menempatkan order + SL/TP (atau mensimulasikannya
   dalam dry-run) dan mencatat semuanya ke log.

## Pengembangan

- Peta & aturan modul: [STRUKTUR.MD](STRUKTUR.MD)
- Berkontribusi: [CONTRIBUTORS.MD](CONTRIBUTORS.MD)
- Lisensi: [MIT](LICENSE)

## Penafian

Proyek ini adalah perangkat lunak sumber terbuka untuk tujuan edukasi dan
otomasi pribadi. Penulis dan kontributor tidak bertanggung jawab atas
kerugian finansial apa pun yang timbul dari penggunaannya. Lakukan riset Anda
sendiri (DYOR) dan uji di testnet sebelum memakai dana sungguhan.
