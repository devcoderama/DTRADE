# STRUKTUR.MD — Rancangan Struktur `binance-ai-future`

Dokumen ini adalah gambar rancangan proyek: susunan folder, tanggung jawab tiap
modul, dan aliran data antar modul. Tujuannya agar pengembangan berikutnya
(oleh manusia maupun AI) bisa langsung tahu di mana sebuah fungsi tinggal dan
ke mana harus menambah fitur baru.

Terakhir diperbarui: 16 November 2026 — disesuaikan dengan isi folder saat ini
(dashboard CLI Textual, grafik Lightweight Charts, workspace web resizable,
siaran Telegram otomatis, pilihan model AI runtime, dan aplikasi Android).

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
├── Dockerfile               ← image produksi (python:3.12-slim, healthcheck /api/health)
├── docker-compose.yml       ← satu layanan; .env sebagai env_file, data/ & logs/ sebagai volume
├── .dockerignore
├── .github/workflows/ci.yml ← CI: pasang dependensi + jalankan pytest di setiap push
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
│   ├── ai_choice.py         ← pilihan provider+model AI runtime (tersimpan di data/ai_settings.json, tanpa edit .env)
│   ├── symbols.py           ← penemuan & filter semua simbol futures
│   ├── indicators.py        ← indikator teknikal murni (EMA 20/50/200, RSI, MACD, ATR, Bollinger, ADX, VWAP) + klasifikasi rezim pasar
│   ├── strategy.py          ← mesin sinyal: skor teknikal + konfirmasi AI (+ cache analisis 180 dtk, konteks multi-timeframe & rezim)
│   ├── risk.py              ← manajemen risiko: validasi SL/TP & R/R, sizing, eksposur, kunci rugi harian/mingguan persisten
│   ├── executor.py          ← eksekusi order berstatus (state machine), idempotency, verifikasi fill & proteksi, emergency close
│   ├── backtest.py          ← mesin backtest deterministik atas klines historis
│   ├── journal.py           ← jurnal perdagangan JSONL (jejak audit eksekusi)
│   ├── service.py           ← TradingService: pintu eksekusi tunggal Web & CLI (lock atomik + validasi harga segar)
│   ├── reconciler.py        ← penjaga posisi ↔ SL algo ↔ jurnal (SL hilang dipasang ulang, algo yatim dibatalkan, penutupan dicatat)
│   ├── position_manager.py  ← manajemen keluar: break-even 1R, trailing ATR, TP parsial, batas waktu posisi (semua bawaan mati)
│   ├── market_stream.py     ← WebSocket data pasar real-time (kline + mark price, reconnect & deteksi basi)
│   ├── database.py          ← cermin opsional jurnal & peristiwa eksekusi ke Supabase/PostgreSQL (DATABASE_URL)
│   ├── notifier.py          ← notifikasi Telegram (teks + foto) untuk eksekusi & sinyal
│   ├── chart_image.py       ← render grafik candlestick PNG (matplotlib) untuk siaran Telegram
│   └── broadcaster.py       ← putaran siaran: pindai → saring → AI → kirim sinyal + grafik ke Telegram
│
├── cli/                     ← ANTARMUKA TERMINAL
│   ├── __init__.py
│   ├── terminal.py          ← argparse CLI: balance, scan, analyze, trade, dashboard, chart, ...
│   ├── dashboard.py         ← dashboard interaktif Textual (overview, grafik, tabel sinyal, metrik)
│   └── term_chart.py        ← grafik candlestick terminal (plotext 5.x: candle + EMA20/50 + volume)
│
├── web/                     ← ANTARMUKA WEB
│   ├── __init__.py
│   ├── app.py               ← server FastAPI + REST API + middleware keamanan (token/CORS/rate limit) + thread siaran otomatis + static files
│   └── static/
│       ├── index.html       ← dashboard satu halaman (responsif, mobile-ready)
│       ├── app.js           ← logika frontend (fetch API, render, grafik, tata letak resizable)
│       ├── style.css        ← tema gelap ala Binance + Font Awesome + workspace resizable
│       ├── hero-gold.jpg    ← gambar hero halaman (berlian emas)
│       └── vendor/          ← library pihak ketiga yang di-host lokal (tanpa CDN)
│           ├── fontawesome/ ← Font Awesome Free 6.5.2 (css/all.min.css + webfonts woff2)
│           └── lightweight-charts/
│               └── lightweight-charts.standalone.production.js
│                            ← TradingView Lightweight Charts v4.2.0 (grafik candlestick interaktif)
│
├── utils/                   ← UTILITAS UMUM
│   ├── __init__.py
│   ├── logger.py            ← logging terstruktur (console + file)
│   └── formatting.py        ← format angka, persen, harga, kuantitas
│
├── android/                 ← APLIKASI ANDROID "D TRADE" (cangkang WebView di atas Web UI)
│   ├── README.md            ← catatan build & rilis APK
│   ├── build.gradle         ← konfigurasi Gradle tingkat proyek
│   ├── settings.gradle
│   ├── gradle.properties
│   ├── gradlew / gradlew.bat
│   ├── gradle/wrapper/      ← gradle-wrapper.jar + properti wrapper (Gradle 8.7)
│   └── app/
│       ├── build.gradle     ← konfigurasi modul aplikasi (SDK, signing dari keystore.properties lokal)
│       ├── proguard-rules.pro
│       └── src/main/
│           ├── AndroidManifest.xml
│           ├── java/com/devcoderama/dtrade/MainActivity.kt
│           │                ← satu Activity: WebView + pengaturan URL server + overlay error
│           └── res/         ← layout, strings, ikon launcher (mipmap), network_security_config.xml
│
├── screenshots/             ← tangkapan layar untuk README (data testnet, dry-run)
│   ├── web-dashboard.png    ← Web UI desktop
│   ├── web-analysis.png     ← Web UI: workspace analisis + grafik Lightweight Charts
│   ├── web-mobile.png       ← Web UI mobile
│   ├── cli-analyze.png      ← CLI: banner + hasil analyze
│   ├── cli-backtest.png     ← CLI: hasil backtest
│   ├── cli-chart.png        ← CLI: grafik candlestick terminal (plotext)
│   └── cli-dashboard.png    ← CLI: dashboard interaktif Textual
│
├── tests/                   ← UJI OTOMATIS pytest (semua memakai mock — tanpa jaringan/order live)
│   ├── conftest.py
│   ├── test_indicators.py   ← EMA/RSI/ADX/VWAP + klasifikasi rezim
│   ├── test_risk.py         ← validasi SL/TP, R/R, kunci harian persisten, rugi beruntun
│   ├── test_executor.py     ← state machine: jalur sukses, SL gagal → emergency close, rekonsiliasi entri
│   ├── test_backtest.py     ← expectancy, Sharpe, Sortino, rugi beruntun maksimum
│   ├── test_market_stream.py ← parsing pesan WS, cache candle, backoff reconnect, deteksi basi
│   ├── test_ai_retry.py     ← klasifikasi error AI & retry post_with_retry
│   ├── test_database.py     ← skema & insert Supabase terparameter + kegagalan DB tidak fatal
│   ├── test_phase1.py       ← Fase 1: cache sinyal anti-racun, service atomik, redaksi log, start aman, -1021
│   ├── test_phase2.py       ← Fase 2: presisi Decimal, validasi Settings, indikator, jurnal performa, reconciler
│   ├── test_phase3.py       ← Fase 3: candle tertutup, gerbang keras, skor v2, peran AI, risiko portofolio, PositionManager
│   └── test_security.py     ← token API, peran viewer, login cookie, CORS, rate limit, endpoint WS
│
├── data/                    ← DATA RUNTIME (dibuat otomatis, di-gitignore)
│   ├── ai_settings.json     ← pilihan provider+model AI yang dipilih lewat Web UI / CLI
│   ├── risk_state.json      ← baseline ekuitas awal hari/minggu + status kunci harian RiskManager
│   └── execution_state.jsonl ← jejak transisi status eksekusi (satu baris per perubahan status)
│
└── logs/                    ← LOG & JURNAL RUNTIME (dibuat otomatis, di-gitignore)
    └── trades.jsonl         ← jurnal eksekusi (satu baris JSON per eksekusi)
```

Catatan: folder `data/` dan `logs/` tidak ikut ter-commit (lihat `.gitignore`),
begitu juga `.env`, `android/keystore.properties`, berkas `*.jks`, dan hasil
build Android (`android/app/build/`, `android/.gradle/`).

---

## 2. Tanggung Jawab Modul

| Modul | Tanggung jawab | Tidak boleh |
|---|---|---|
| `config/settings.py` | Membaca `.env`, menyediakan satu objek `Settings` immutable untuk semua modul (kunci API, mode testnet/dry-run, risiko, siaran, cache) | Mengandung secret yang di-hardcode |
| `core/binance_client.py` | Semua panggilan REST ke `fapi.binance.com` / testnet: data publik (klines, ticker massal, funding, open interest), akun, posisi, order. Menandatangani request (HMAC-SHA256) | Mengandung strategi / keputusan trading |
| `core/ai_client.py` | Semua panggilan ke `router.bangunweb.com/v1`: chat completions, cek saldo token, daftar model. Juga rumah bagi prompt sistem & skema `AIAnalysis` bersama | Mengandung logika Binance |
| `core/ai_providers.py` | Abstraksi provider AI: satu antarmuka `analyze_market` untuk BangunWeb, OpenAI, Anthropic, DeepSeek, Grok (xAI), Nous Portal, dan gateway OpenAI-compatible custom; factory `create_ai_provider` membaca `AI_PROVIDER` | Menempatkan order; tahu soal Binance |
| `core/ai_choice.py` | Menyimpan & membaca pilihan provider+model AI aktif di `data/ai_settings.json` (di atas bawaan `.env`); kunci API tetap hanya di `.env`. Dipakai Web UI (panel AI), CLI (`ai-model`), dan server web (muat ulang otomatis saat berkas berubah) | Menyimpan kunci API |
| `core/symbols.py` | Memuat `exchangeInfo`, menyaring perpetual USDT yang `TRADING`, menyediakan filter presisi (stepSize, tickSize, minNotional) + pembulatan qty/harga, dan peringkat volume | Memanggil order |
| `core/indicators.py` | Fungsi matematika murni dari deret harga → snapshot indikator (tren EMA, RSI, MACD, Bollinger %B, ATR, funding, perubahan harga). Tanpa I/O, tanpa jaringan — mudah diuji | Mengambil data sendiri |
| `core/strategy.py` | Menggabungkan snapshot indikator menjadi skor teknikal, meminta penilaian AI, menghasilkan `TradeSignal` (side, confidence, SL, TP, alasan). Memiliki cache analisis (bawaan 180 detik, `ANALYSIS_CACHE_TTL_SEC`) agar Web UI, CLI, eksekusi, dan siaran memakai hasil yang sama dalam satu jendela waktu | Menempatkan order |
| `core/risk.py` | Otoritas risiko tertinggi: memvalidasi ulang arah SL/TP & rasio R/R & jarak stop minimum, menghitung ukuran posisi dari % risiko, memeriksa batas posisi/leverage/eksposur, dan menegakkan kunci rugi harian (berbasis perubahan ekuitas, persisten di `data/risk_state.json`), batas mingguan, serta perlindungan rugi beruntun | Menempatkan order |
| `core/executor.py` | Satu-satunya modul yang menempatkan/membatalkan order, sebagai mesin status persisten (CREATED→…→PROTECTED/CLOSED/FAILED/RECOVERY_REQUIRED di `data/execution_state.jsonl`). Entri memakai idempotency key + verifikasi fill; SL/TP dipasang dengan retry & verifikasi; SL gagal total → emergency close. Semua eksekusi dilindungi satu lock proses | Mengambil keputusan sinyal |
| `core/backtest.py` | Simulasi aturan strategi atas klines historis: entri candle berikutnya, SL/TP ATR, fee taker, ukuran berbasis risiko; menghasilkan statistik + kurva ekuitas | Memanggil AI (tidak deterministik & berbayar) |
| `core/journal.py` | Menambah satu baris JSON per eksekusi ke jurnal dan membaca entri terakhir | Mengubah/menghapus catatan lama |
| `core/notifier.py` | Mengirim pesan & foto ke Telegram (user id atau channel id) lewat Bot API: hasil eksekusi, sinyal kuat, pesan uji. Kegagalan kirim tidak pernah menggagalkan trading | Mengambil keputusan trading |
| `core/chart_image.py` | Merender klines menjadi gambar PNG bergaya Binance (candle + EMA20/50 + volume + RSI + garis SL/TP) dengan matplotlib untuk lampiran siaran | Mengambil keputusan trading |
| `core/broadcaster.py` | Satu putaran siaran sinyal: pindai semua simbol (prefilter likuiditas + snapshot ringan paralel), saring \|skor\| ≥ ambang, analisis AI, kirim maks. N sinyal terkonfirmasi AI sebagai foto + caption HTML; mendukung jeda siar ulang per simbol+sisi | Mengeksekusi order |
| `cli/terminal.py` | Antarmuka terminal: menerjemahkan perintah user → panggilan core. Perintah: `balance`, `symbols`, `scan`, `analyze`, `positions`, `trade`, `close`, `backtest`, `watch`, `journal`, `broadcast`, `notify-test`, `ai-account`, `ai-model`, `dashboard`, `chart`. Juga banner branding Rich | Logika bisnis sendiri |
| `cli/dashboard.py` | Dashboard terminal interaktif (Textual): header + jam live, Market Overview, grafik, tabel sinyal (backtest / hasil pindai), metrik kunci; penyegaran ringan berkala tanpa memanggil AI. Hanya memantau | Mengeksekusi order |
| `cli/term_chart.py` | Merender klines mentah menjadi grafik candlestick ANSI (plotext 5.x): candle + EMA20/EMA50 + panel volume; ukuran dibatasi ukuran terminal. Dipakai perintah `chart` dan dashboard Textual | Mengambil data sendiri |
| `core/market_stream.py` | Satu koneksi WebSocket Binance (combined stream) untuk kline + mark price simbol yang dipantau: reconnect exponential backoff, heartbeat, deteksi data basi, cache candle terakhir, dan daftar pendengar. Riwayat tetap dari REST — modul ini hanya mendorong pembaruan | Menempatkan order; mengambil keputusan |
| `core/database.py` | Cermin opsional ke Supabase/PostgreSQL (`DATABASE_URL`): membuat tabel `trade_journal` & `execution_events`, menulis dengan query terparameter. JSONL lokal tetap sumber utama; kegagalan DB hanya dicatat sebagai warning dan tidak pernah mengganggu trading. Order kondisional (SL/TP) dikirim `core/binance_client.py` lewat Algo Order API (`place_algo_order`) karena endpoint order klasik menolaknya (error -4120, terverifikasi di testnet) | Menjadi sumber kebenaran; melempar error ke jalur trading |
| `web/app.py` | Antarmuka web: endpoint REST tipis di atas core yang sama persis dengan CLI (`/api/analyze`, `/api/scan`, `/api/klines`, `/api/backtest`, `/api/trade`, `/api/broadcast`, `/api/ai/model`, `/api/config`, `/api/auth/login`, ...), endpoint WebSocket `/api/ws/klines/{symbol}` (autentikasi token manual) yang meneruskan candle real-time dari `market_stream.py` ke grafik, middleware keamanan (autentikasi token admin/viewer, CORS non-wildcard dari `.env`, rate limit per IP, batas ukuran request), validasi startup + pemeriksaan pemulihan eksekusi, melayani berkas statis, dan menjalankan thread siaran otomatis berkala bila Telegram terkonfigurasi | Logika bisnis sendiri |
| `web/static/index.html` | Struktur halaman: sidebar, hero, kartu statistik, workspace analisis (panel analisis + grafik + sentimen), baris sinyal, panel AI, pemindai, posisi | — |
| `web/static/app.js` | Logika frontend: fetch API, render analisis/sentimen/gauge, grafik Lightweight Charts (EMA20/50, volume, crosshair OHLCV), backtest + kurva ekuitas, dan pengelola tata letak resizable (seret panel, sembunyikan, maximize, Reset Layout, tersimpan di localStorage) | Menyimpan kunci API |
| `web/static/style.css` | Tema gelap ala Binance (emas #F0B90B), ikon Font Awesome, tata letak responsif desktop/tablet/mobile + navigasi bawah, dan gaya workspace resizable (gagang seret, rel panel) | — |
| `android/.../MainActivity.kt` | Aplikasi D TRADE: satu Activity berisi WebView yang memuat Web UI dari server yang berjalan (URL bisa diganti di dalam aplikasi); backend tetap terpisah | Menjalankan logika trading di ponsel |
| `utils/*` | Logging & formatting yang dipakai semua modul | Ketergantungan ke core |

Prinsip kunci: **CLI dan Web UI adalah dua kulit di atas core yang sama.**
Fitur baru cukup ditulis sekali di `core/`, lalu diekspos di kedua antarmuka.
Aplikasi Android tidak menduplikasi apa pun — ia hanya membungkus Web UI.

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
        │  + cache 180s  │
        └────────▲───────┘
                 │ penilaian AI (JSON)
        ┌────────┴───────┐   ┌──────────────┐
        │ ai_providers.py│   │ executor.py  │──► order + SL/TP (atau simulasi
        │ (multi-model)  │   │              │    bila dry-run)
        └────────────────┘   └──────▲───────┘
                                    │ perintah user
                      ┌─────────────┴─────────────┐
                      │  cli/terminal.py          │
                      │  cli/dashboard.py         │
                      │  web/app.py + static UI   │──► D TRADE (WebView)
                      └───────────────────────────┘

        Jalur siaran (paralel, tanpa eksekusi):
        broadcaster.py ──► strategy.py (pindai + AI)
                       ──► chart_image.py (PNG) ──► notifier.py ──► Telegram
```

Urutan satu siklus analisis → eksekusi:

1. `symbols.py` memastikan daftar simbol & filter presisi mutakhir.
2. `binance_client.py` mengambil klines simbol dari Binance.
3. `indicators.py` mengubah klines menjadi snapshot indikator.
4. `strategy.py` memberi skor teknikal, lalu provider AI menilai snapshot
   yang sama dan mengembalikan keputusan JSON (LONG/SHORT/NEUTRAL + alasan).
   Hasil analisis disimpan di cache 180 detik, jadi permukaan mana pun
   (Web UI, CLI, siaran, eksekusi) melihat angka yang sama.
5. `risk.py` memeriksa sinyal terhadap saldo, batas posisi, dan rugi harian,
   lalu menghitung kuantitas yang aman.
6. `executor.py` mengeksekusi — disimulasikan penuh bila `TRADING_MODE=dry_run`,
   atau dikirim ke Binance bila `live` (dengan SL/TP otomatis) — dan hasilnya
   dicatat `journal.py` serta diberitahukan lewat `notifier.py`.

Jalur siaran berjalan terpisah: manual lewat tombol Web UI / perintah
`broadcast`, atau otomatis sebagai thread latar di server web
(`BROADCAST_*` di `.env`). Ia tidak pernah mengeksekusi order.

---

## 4. Keputusan Desain

- **Python murni + `requests`** untuk core: berjalan identik di macOS, Windows,
  Linux, dan Android (Termux). Tidak ada dependensi biner.
- **Web UI tanpa build step** (HTML/CSS/JS vanilla): server yang sama melayani
  desktop dan browser ponsel (iOS/Android) di jaringan yang sama.
- **Library frontend di-host lokal** (`web/static/vendor/`): Font Awesome dan
  Lightweight Charts disalin ke repo, jadi ikon & grafik tetap bekerja tanpa
  internet/CDN. plotext disemat di seri 5.x karena seri 6 mengubah API-nya.
- **Workspace web resizable**: lebar panel analisis/sentimen, tinggi workspace,
  dan lebar panel sinyal bisa diseret (desktop), tersimpan di localStorage
  peramban — murni urusan tampilan, tidak menyentuh logika trading.
- **Dry-run adalah bawaan.** Mode `live` harus diaktifkan eksplisit di `.env`
  dan tetap melewati RiskManager. Tidak ada jalur kode yang melewati risk.py.
- **AI tidak pernah memegang kunci Binance.** AI hanya menerima snapshot angka
  (harga + indikator) dan mengembalikan penilaian; eksekusi selalu lokal.
- **Pilihan model AI di luar `.env`.** Provider+model aktif disimpan di
  `data/ai_settings.json` agar bisa diganti dari Web UI/CLI tanpa menyunting
  berkas konfigurasi; kunci API tetap hanya hidup di `.env`.
- **Semua simbol, bukan satu simbol.** Daftar simbol selalu dibaca dari
  `exchangeInfo` saat runtime, jadi simbol baru Binance otomatis terdeteksi.
  Pemindaian memakai prefilter likuiditas + snapshot ringan paralel agar cepat.
- **Satu sumber konfigurasi.** Semua modul membaca `config.settings.Settings`;
  tidak ada `os.environ` yang tersebar di luar modul itu.
- **Android = cangkang tipis.** D TRADE (Kotlin, satu Activity) hanya memuat
  Web UI lewat WebView; pembaruan tampilan Web UI langsung terasa di aplikasi
  tanpa merilis APK baru. Rilis ditandatangani keystore lokal di luar repo.
  WebView dikeraskan (tanpa akses berkas/content provider, tanpa jembatan
  JavaScript, allowBackup mati) dan tidak menyimpan kredensial apa pun.
- **Keamanan berlapis di server web.** Bila `WEB_API_TOKEN` diisi, semua
  endpoint (selain `/api/health` dan login) wajib bertoken; CORS tidak
  pernah wildcard; ada rate limit dan batas ukuran request. Secret tidak
  pernah keluar dari backend — `/api/config` hanya menampilkan ringkasan
  aman, dan error 500 tidak membocorkan detail internal.
- **Eksekusi tidak pernah "percaya begitu saja".** Keberhasilan order
  dibuktikan dari status Binance (fill + open orders), bukan dari
  terkirimnya request; hasil yang tidak diketahui direkonsiliasi lewat
  idempotency key, bukan dikirim ulang.

---

## 5. Menambah Fitur Baru

| Keinginan | Sentuh modul ini |
|---|---|
| Indikator baru | `core/indicators.py` → ekspos di snapshot → pakai di `strategy.py` |
| Aturan risiko baru | `core/risk.py` |
| Endpoint web baru | `web/app.py` (+ `web/static/app.js` bila perlu tampilan) |
| Perintah terminal baru | `cli/terminal.py` |
| Tampilan dashboard terminal | `cli/dashboard.py` (+ `cli/term_chart.py` untuk grafiknya) |
| Model AI / prompt baru | `core/ai_client.py` (prompt), `core/ai_providers.py` (provider), `.env` (bawaan) — pilihan aktif lewat `core/ai_choice.py` |
| Perilaku siaran Telegram | `core/broadcaster.py` (alur), `core/chart_image.py` (gambar), `core/notifier.py` (pengiriman) |
| Tampilan aplikasi Android | `web/static/*` (otomatis terbawa WebView); cangkang di `android/` hanya bila perlu kemampuan native |
| Exchange lain di masa depan | client baru sejajar `binance_client.py` dengan antarmuka yang sama |
