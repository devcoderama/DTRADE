# STRUKTUR.MD — Rancangan Struktur `binance-ai-future`

Dokumen ini adalah gambar rancangan proyek: susunan folder, tanggung jawab tiap
modul, dan aliran data antar modul. Tujuannya agar pengembangan berikutnya
(oleh manusia maupun AI) bisa langsung tahu di mana sebuah fungsi tinggal dan
ke mana harus menambah fitur baru.

---

## 1. Peta Folder

```text
binance-ai-future/
├── STRUKTUR.MD              ← dokumen ini
├── README.MD                ← panduan pengguna
├── LICENSE                  ← lisensi MIT
├── CONTRIBUTORS.MD          ← daftar kontributor & cara berkontribusi
├── requirements.txt         ← dependensi Python (cross-platform)
├── .env.example             ← contoh konfigurasi (salin menjadi .env)
├── .gitignore
├── run.py                   ← pintu masuk utama: `web` | `cli`
│
├── config/                  ← KONFIGURASI
│   ├── __init__.py
│   └── settings.py          ← Settings dataclass, dibaca dari .env / environment
│
├── core/                    ← LOGIKA INTI TRADING
│   ├── __init__.py
│   ├── binance_client.py    ← REST client Binance USDⓈ-M Futures (signed + public)
│   ├── ai_client.py         ← client AI Router BangunWeb (OpenAI-compatible)
│   ├── ai_providers.py      ← lapisan multi-provider AI + factory (OpenAI, Anthropic, DeepSeek, Grok, Nous, custom)
│   ├── notifier.py          ← notifikasi Telegram (Bot API) untuk hasil eksekusi
│   ├── symbols.py           ← penemuan & filter semua simbol futures
│   ├── indicators.py        ← indikator teknikal murni (EMA, RSI, MACD, ATR, BB)
│   ├── strategy.py          ← mesin sinyal: skor teknikal + konfirmasi AI
│   ├── risk.py              ← manajemen risiko & position sizing
│   └── executor.py          ← eksekusi order (dry-run / live), SL & TP
│
├── cli/                     ← ANTARMUKA TERMINAL
│   ├── __init__.py
│   └── terminal.py          ← argparse CLI: balance, scan, analyze, trade, ...
│
├── web/                     ← ANTARMUKA WEB
│   ├── __init__.py
│   ├── app.py               ← server FastAPI + REST API + static files
│   └── static/
│       ├── index.html       ← dashboard satu halaman (responsif, mobile-ready)
│       ├── app.js           ← logika frontend (fetch API, render tabel/kartu)
│       └── style.css        ← tema gelap profesional
│
└── utils/                   ← UTILITAS UMUM
    ├── __init__.py
    ├── logger.py            ← logging terstruktur (console + file)
    └── formatting.py        ← format angka, persen, warna terminal
```

---

## 2. Tanggung Jawab Modul

| Modul | Tanggung jawab | Tidak boleh |
|---|---|---|
| `config/settings.py` | Membaca `.env`, menyediakan satu objek `Settings` immutable untuk semua modul | Mengandung secret yang di-hardcode |
| `core/binance_client.py` | Semua panggilan REST ke `fapi.binance.com` / testnet: data publik, akun, posisi, order. Menandatangani request (HMAC-SHA256) | Mengandung strategi / keputusan trading |
| `core/ai_client.py` | Semua panggilan ke `router.bangunweb.com/v1`: chat completions, cek saldo token, daftar model. Juga rumah bagi prompt sistem & skema `AIAnalysis` bersama | Mengandung logika Binance |
| `core/ai_providers.py` | Abstraksi provider AI: satu antarmuka `analyze_market` untuk BangunWeb, OpenAI, Anthropic, DeepSeek, Grok (xAI), Nous Portal, dan gateway OpenAI-compatible custom; factory `create_ai_provider` membaca `AI_PROVIDER` | Menempatkan order; tahu soal Binance |
| `core/notifier.py` | Mengirim ringkasan eksekusi/sinyal ke Telegram (user id atau channel id) lewat Bot API. Kegagalan kirim tidak pernah menggagalkan trading | Mengambil keputusan trading |
| `core/symbols.py` | Memuat `exchangeInfo`, menyaring perpetual USDT yang `TRADING`, menyediakan filter presisi (stepSize, tickSize, minNotional) + pembulatan qty/harga | Memanggil order |
| `core/indicators.py` | Fungsi matematika murni dari deret harga → nilai indikator. Tanpa I/O, tanpa jaringan — mudah diuji | Mengambil data sendiri |
| `core/strategy.py` | Menggabungkan snapshot indikator menjadi skor teknikal, meminta penilaian AI, menghasilkan `TradeSignal` (side, confidence, SL, TP, alasan) | Menempatkan order |
| `core/risk.py` | Menghitung ukuran posisi dari % risiko, memeriksa batas (maks posisi, leverage, rugi harian), memberi/menolak izin eksekusi | Menempatkan order |
| `core/executor.py` | Satu-satunya modul yang menempatkan/membatalkan order. Menghormati mode `dry_run` vs `live` | Mengambil keputusan sinyal |
| `cli/terminal.py` | Antarmuka terminal: menerjemahkan perintah user → panggilan core | Logika bisnis sendiri |
| `web/app.py` | Antarmuka web: endpoint REST tipis di atas core yang sama persis dengan CLI | Logika bisnis sendiri |
| `utils/*` | Logging & formatting yang dipakai semua modul | Ketergantungan ke core |

Prinsip kunci: **CLI dan Web UI adalah dua kulit di atas core yang sama.**
Fitur baru cukup ditulis sekali di `core/`, lalu diekspos di kedua antarmuka.

---

## 3. Aliran Data

```text
                 ┌────────────────────┐
                 │  Binance Futures   │  klines, ticker, exchangeInfo
                 │  REST API          │  account, positions, orders
                 └─────────▲──────────┘
                           │ signed/public REST
                 ┌─────────┴──────────┐
                 │ binance_client.py  │
                 └─────────▲──────────┘
             klines  │              │ account/positions
        ┌────────────┴───┐   ┌──────┴───────┐
        │  indicators.py │   │  risk.py     │
        │  (snapshot)    │   │  (sizing &   │
        └────────▲───────┘   │   limits)    │
                 │           └──────▲───────┘
        ┌────────┴───────┐          │ validated size
        │  strategy.py   │──────────┘
        │  skor teknikal │   TradeSignal
        └────────▲───────┘
                 │ penilaian AI (JSON)
        ┌────────┴───────┐   ┌──────────────┐
        │  ai_client.py  │   │ executor.py  │──► order + SL/TP (atau simulasi
        │  (BangunWeb)   │   │              │    bila dry-run)
        └────────────────┘   └──────▲───────┘
                                    │ perintah user
                      ┌─────────────┴─────────────┐
                      │  cli/terminal.py          │
                      │  web/app.py + static UI   │
                      └───────────────────────────┘
```

Urutan satu siklus analisis → eksekusi:

1. `symbols.py` memastikan daftar simbol & filter presisi mutakhir.
2. `binance_client.py` mengambil klines simbol dari Binance.
3. `indicators.py` mengubah klines menjadi snapshot indikator.
4. `strategy.py` memberi skor teknikal, lalu `ai_client.py` menilai snapshot
   yang sama dan mengembalikan keputusan JSON (LONG/SHORT/NEUTRAL + alasan).
5. `risk.py` memeriksa sinyal terhadap saldo, batas posisi, dan rugi harian,
   lalu menghitung kuantitas yang aman.
6. `executor.py` mengeksekusi — disimulasikan penuh bila `TRADING_MODE=dry_run`,
   atau dikirim ke Binance bila `live` (dengan SL/TP otomatis).

---

## 4. Keputusan Desain

- **Python murni + `requests`** untuk core: berjalan identik di macOS, Windows,
  Linux, dan Android (Termux). Tidak ada dependensi biner.
- **Web UI tanpa build step** (HTML/CSS/JS vanilla): server yang sama melayani
  desktop dan browser ponsel (iOS/Android) di jaringan yang sama.
- **Dry-run adalah bawaan.** Mode `live` harus diaktifkan eksplisit di `.env`
  dan tetap melewati RiskManager. Tidak ada jalur kode yang melewati risk.py.
- **AI tidak pernah memegang kunci Binance.** AI hanya menerima snapshot angka
  (harga + indikator) dan mengembalikan penilaian; eksekusi selalu lokal.
- **Semua simbol, bukan satu simbol.** Daftar simbol selalu dibaca dari
  `exchangeInfo` saat runtime, jadi simbol baru Binance otomatis terdeteksi.
- **Satu sumber konfigurasi.** Semua modul membaca `config.settings.Settings`;
  tidak ada `os.environ` yang tersebar di luar modul itu.

---

## 5. Menambah Fitur Baru

| Keinginan | Sentuh modul ini |
|---|---|
| Indikator baru | `core/indicators.py` → ekspos di snapshot → pakai di `strategy.py` |
| Aturan risiko baru | `core/risk.py` |
| Endpoint web baru | `web/app.py` (+ `web/static/app.js` bila perlu tampilan) |
| Perintah terminal baru | `cli/terminal.py` |
| Model AI / prompt baru | `core/ai_client.py` (prompt) dan `.env` (nama model) |
| Exchange lain di masa depan | client baru sejajar `binance_client.py` dengan antarmuka yang sama |
