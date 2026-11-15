# PERBAIKAN_LOG — Program Perbaikan & Peningkatan binance-ai-future

Catatan per fase: apa yang diubah, kenapa, dan test apa yang membuktikannya.
Aturan keras program ini: tanpa order live, tanpa akses jaringan ke Binance
saat pengujian (semua mock/fake), `TRADING_MODE` bawaan tetap `dry_run`,
perubahan `risk.py` hanya boleh membuat sistem sama/lebih aman.

## Baseline (Langkah 0) — 2026-11-16
- `python -m pytest tests/ -q` → **47 passed** sebelum perubahan apa pun.
- Orientasi: README.MD, STRUKTUR.MD, dan modul inti (strategy, risk,
  executor, binance_client, indicators, backtest, web/app, database,
  notifier, settings) dibaca ulang.

## Fase 1 — Keamanan & Eksekusi (kritis) — SELESAI, 61 test hijau (baseline 47 → +14)

- **1.1 Web aman secara default.** `WEB_HOST` bawaan `127.0.0.1`. Server
  MENOLAK start bila (live tanpa token) atau (bind non-loopback tanpa
  token); pengecualian sadar-risiko `ALLOW_INSECURE_WEB=true` dengan
  peringatan keras (`web/app.py::enforce_web_safety`, dipanggil di
  startup & `run()`). Semua perbandingan token memakai
  `hmac.compare_digest`. Login dibatasi terpisah 5/menit/IP; bucket rate
  limit disapu agar memori terbatas. `/api/health` kini hanya
  `{ok, service}` — ringkasan konfigurasi pindah ke `/api/config`
  (bertoken). Cookie sesi `Secure` otomatis pada HTTPS
  (`x-forwarded-proto`). WebSocket: cookie → header → query
  (`?token=` ditandai DEPRECATED). docker-compose bind
  `127.0.0.1:8000:8000`; Dockerfile berjalan sebagai user non-root.
  `.env.example`/README: panduan reverse proxy HTTPS/Tailscale.
- **1.2 Frontend.** Helper `esc()` diterapkan ke semua string dinamis
  innerHTML (reasoning AI, reasons, model, pesan error, simbol).
  Token TIDAK lagi di localStorage — autentikasi hanya cookie HttpOnly
  dari `/api/auth/login` (alur 401: prompt → login → ulangi). Semua
  inline `onclick` berisi string simbol diganti atribut `data-*` +
  delegasi `addEventListener` (posisi, kartu eksekusi, tabel scan).
- **1.3 Android.** `network_security_config.xml`: cleartext global MATI;
  hanya `localhost` + `10.0.2.2`. Android tidak mendukung CIDR privat di
  domain-config — didokumentasikan di `android/README.md` (tambah IP LAN
  eksplisit atau pakai Tailscale/HTTPS). Berlaku setelah APK dibangun ulang.
- **1.4 `close_position`.** Urutan dibalik: MARKET reduce-only DULU →
  verifikasi posisi nol → BARU batalkan order biasa + algo. Perintah
  tutup gagal → SL/TP utuh, hasil terstruktur `{closed: false, error}`.
  Test: gagal → proteksi tetap ada; sukses → semua order bersih.
- **1.5 Jendela tanpa SL.** `_place_protection` & `_emergency_close`
  menangkap `Exception` lebar; `_execute_locked` punya jaring pengaman:
  exception apa pun setelah fill → emergency close → CLOSED /
  RECOVERY_REQUIRED. Penulisan database menjadi ASINKRON penuh
  (`queue.Queue` + thread pekerja daemon di `core/database.py`) —
  urutan jalur kritis: pasang SL dulu, catat belakangan. Circuit
  breaker DB: 3 kegagalan beruntun → cooldown 60 dtk; koneksi
  dilindungi lock. Test: DB menggantung tidak menahan pemanggil.
- **1.6 Eksekusi data segar.** `SignalEngine.analyze` selalu
  mengembalikan deepcopy (cache tidak bisa diracuni — test mutasi) dan
  menerima `fresh=True`; `/api/trade` & CLI `trade` WAJIB segar.
  `TradingService` menolak eksekusi bila harga bergeser >
  `MAX_ENTRY_DEVIATION_PCT` (bawaan 0,3%) dari harga sinyal; di dalam
  toleransi, sinyal dinilai ulang pada harga terkini. Override `side`
  manual membuat sinyal TURUNAN (`dataclasses.replace`) + alasan
  eksplisit. Executor memvalidasi SL/TP terhadap `avgPrice` isi aktual;
  tidak valid → emergency close.
- **1.7 Atomisitas.** Modul baru `core/service.py::TradingService` —
  satu lock level-aplikasi membungkus validasi harga + evaluate +
  execute; dipakai Web & CLI. Test: dua submit paralel dengan maks
  1 posisi → hanya satu yang dieksekusi.
- **1.8 Redaksi log.** `utils/logger.py::redact()` + `RedactFilter` di
  semua handler: pola `/bot<id>:<token>` Telegram dan `signature=…`
  Binance disamarkan. Test memastikan token tidak muncul di output.
- **1.9 Sinkron waktu.** Startup web memanggil `binance.sync_time()`
  (best-effort) + thread penyegar tiap 30 menit. Error `-1021` di
  client: sinkronkan jam lalu ulangi request SEKALI (aman — request
  ditolak sebelum diproses bursa). Test dengan session palsu.

Test baru: `tests/test_phase1.py` (10 test) + 3 test executor
(close ordering, exception pasca-fill) + test DB non-blocking.
Perilaku yang sengaja berubah & test-nya disesuaikan: `test_database.py`
(insert kini asinkron — memakai `flush()`), sesuai butir 1.5.

## Fase 2 — Keandalan & Koreksi Logika — SELESAI, 79 test hijau (61 → +18)

- **2.1 Streak rugi berjendela.** `_consecutive_losses` kini hanya
  menghitung REALIZED_PNL dalam `CONSECUTIVE_LOSS_WINDOW_HOURS` (bawaan
  24) — rugi lama tidak mengunci selamanya (cooldown alami saat jendela
  berlalu). Pengambilan memakai `startTime` + `incomeType` + paginasi
  (5×1000), tidak lagi bergantung pada 100 baris tercampur.
  `income_history` client diperluas (start/end time, income type).
  Test lama disesuaikan (timestamp relatif) + test jendela baru.
- **2.2 `cli watch` DIPERBAIKI** — sebelumnya crash: `SymbolManager`
  tidak punya `top_by_volume`. Kini diimplementasikan (urut
  quoteVolume dari `tickers_24h_all`, hanya simbol aktif) + test.
  `cmd_journal`/`cmd_backtest` aman tanpa rich (fallback teks polos);
  `cmd_analyze` menangani `rsi14 is None`.
- **2.3 Presisi Decimal.** `round_quantity`/`round_price` memakai
  `Decimal` ROUND_DOWN (kasus 0.3/0.1, 0.57/0.01, 1.15/0.01 + property
  test 1000 nilai acak: selalu kelipatan step, tak pernah turun >1 step).
  Arah pembulatan SL didokumentasikan di `risk.py` (risiko dolar tetap
  karena qty dihitung dari jarak SL final).
- **2.4 Indikator.** `change_1h_pct` dihitung dari interval aktual
  (3600/detik-interval; interval >1h → None, tidak ditebak). EMA kini
  di-seed SMA(period) bila data cukup. `snapshot_for` mengambil 300
  candle agar EMA200 valid. Snapshot menandai `includes_open_candle`
  (persiapan kebijakan candle-tertutup Fase 3.1).
- **2.5 Konfigurasi ketat.** `ConfigError`: parameter risiko/keuangan
  yang salah format kini MENOLAK start (bukan diam-diam default) dan
  `Settings.__post_init__` memvalidasi rentang (risk ≤5%, leverage
  1–20, batas rugi >0, R/R ≥1, interval sah, dll). `has_ai_key` &
  `ai_model` di ringkasan kini mengikuti provider aktif, bukan selalu
  BangunWeb.
- **2.6 Reconciler BARU** (`core/reconciler.py`): thread latar web
  (juga perintah CLI `reconcile`). Tiap putaran: (a) posisi tanpa SL →
  pasang ulang SL dari jurnal / tutup darurat bila asal tak diketahui;
  (b) algo yatim → dibatalkan; (c) posisi tertutup → event penutupan +
  realized PnL dicatat ke jurnal. Registri diturunkan dari jurnal
  (restart-safe). Dry-run: hanya log. 4 test skenario + dry-run.
- **2.7 Jurnal & metrik.** `record_close` (harga keluar, alasan,
  PnL terealisasi, fee, durasi, R-multiple, terhubung client_order_id),
  `record_ai_decision` (provider/model/sinyal/keyakinan per analisis,
  dicatat dari Web & CLI), `TradeJournal.performance()` →
  `GET /api/performance` + CLI `performance` (win rate, PF, expectancy,
  R rata-rata, drawdown kurva PnL).
- **2.8 Kebersihan.** `@app.on_event` → `lifespan` (validasi + broadcast
  + reconciler; peringatan deprecation hilang). `realized_pnl_today`
  dihapus (tak terpakai). Akses privat `ai_client._client` diganti
  method publik `list_models()` di provider. Cache analisis disapu
  TTL-nya & dibatasi 512 entri. Baseline harian/mingguan RiskManager
  kini UTC (satu sumber waktu). Algo order menerima `workingType`
  (`PROTECTION_WORKING_TYPE`, bawaan MARK_PRICE) — ditandai
  `TODO(verify)` di kode sesuai aturan kehati-hatian API.

## Fase 3 — Strategi Sinyal — SELESAI, 93 test hijau (79 → +14)

- **3.1 Candle tertutup saja** (`USE_CLOSED_CANDLES_ONLY=true` bawaan):
  candle berjalan dibuang sebelum indikator dihitung di kedua jalur
  snapshot; harga acuan entri/SL/TP tetap harga kini (mark price).
  Backtest tidak terpengaruh (data historis memang tertutup).
- **3.2 Gerbang keras** (sebelumnya MTF hanya peringatan): tren 1h/4h
  berlawanan → sinyal dibatalkan (`MTF_GATE_ENABLED`); rezim
  high/low_volatility → semua entri arah ditolak; rezim trending dengan
  adx14 < `ADX_MIN` (20) → ditolak (`REGIME_GATE_ENABLED`). Berlaku di
  analisis penuh maupun jalur pindai; alasan selalu tertulis di sinyal.
- **3.3 Skor v2 per kategori** (`SCORING_VERSION=2`, bawaan tetap 1):
  trend (maks 40, dilemahkan di luar rezim trending) / mean-reversion
  (maks 25, hanya aktif saat sideways) / positioning (maks 20: funding
  berjenjang + volume). Syarat minimal 2 kategori sepakat — satu
  kategori sendirian dibatasi skornya ke ≤24 (di bawah ambang entri).
  v1 dipertahankan persis & menjadi bawaan agar perilaku lama bisa
  dibandingkan. Catatan jujur: riwayat perubahan OI tidak tersedia dari
  snapshot saat ini, jadi positioning memakai funding + volume.
- **3.4 AI hanya konfirmasi/veto** (`AI_CAN_INITIATE=false` bawaan):
  jalur lama "teknikal netral + AI ≥75 → trade" kini mati secara
  bawaan. Konteks AI diperkaya: MTF, tren/regim BTC 1h (cache 60 dtk),
  rezim, dan 25 candle terakhir (`recent_candles` di snapshot). Level
  SL/TP dari AI kini juga divalidasi jaraknya terhadap ATR (SL
  0,5–3×ATR, TP ≤5×ATR) — di luar rentang diganti level ATR. Keluaran
  AI divalidasi skema pydantic (`_AIOutput`); gagal skema → NEUTRAL.
- **3.5 PositionManager BARU** (`core/position_manager.py`), semua
  fitur bawaan MATI: break-even setelah 1R, trailing stop ATR setelah
  1,5R, TP parsial 50% pada 1,5R, batas waktu posisi
  (`MAX_HOLD_HOURS`). Penggantian SL aman: pasang baru → verifikasi →
  baru batalkan lama. Berjalan di dalam putaran reconciler; state
  persisten di `data/position_manager_state.json`; dry-run hanya log.
  **Belum dikerjakan di fase ini:** model keluar yang sama di backtest —
  ditunda ke Fase 4 yang memang menulis ulang loop backtest (window
  indikator), agar tidak dikerjakan dua kali.
- **3.6 Risiko portofolio** di `RiskManager._portfolio_checks`:
  batas posisi searah (`MAX_SAME_DIRECTION_POSITIONS=2`), total risiko
  terbuka dari SL algo tiap posisi + risiko rencana ≤
  `MAX_OPEN_RISK_PCT` (bawaan 2× risiko per trade), filter likuiditas
  (`MIN_QUOTE_VOLUME_USD`, bawaan mati), penjaga waktu funding
  (`FUNDING_GUARD_MINUTES`, bawaan mati; funding ekstrem di sekitar
  00/08/16 UTC ditolak), dan `BTC_FILTER` (bawaan mati; arah kuat BTC
  dari EMA 1h & 4h, cache 60 dtk).

## Fase 4 — Backtest & Riset — SELESAI, 102 test hijau (93 → +9)

- **4.1 Riwayat panjang + cache.** `BinanceFuturesClient.klines`
  menerima start/end time dan `klines_paginated` mengambil ≥10.000
  candle per halaman 1.500 tanpa duplikat. Modul baru
  `core/history.py`: cache disk per simbol+interval di
  `data/kline_cache/` — pemuatan ulang hanya mengambil ekor yang belum
  tercache. `Backtester.run(..., days=N)` memakai jalur ini.
- **4.2 Jendela indikator tetap.** Backtest kini menilai sinyal dari
  300 candle terakhir saja (persis snapshot live) dan meneruskan
  `interval` yang benar ke snapshot (sebelumnya jendela mengembang &
  `change_1h_pct` salah untuk interval selain 15m). `SCORING_VERSION`
  dan gerbang rezim/ADX tersedia sebagai opsi config (`use_gates`,
  bawaan mati agar hasil lama tetap dapat dibandingkan). **Paritas
  model keluar dari Fase 3.5 kini dikerjakan**: break-even, trailing
  ATR, dan TP parsial tersedia di `BacktestConfig` (bawaan mati,
  pemicu R sama seperti PositionManager live; penyesuaian SL berlaku
  mulai candle berikutnya).
- **4.3 Model biaya realistis.** Slippage per pengisian (bawaan 5 bps,
  selalu merugikan), funding per periode 8 jam selama posisi ditahan
  (suku konstan yang dapat diatur; LONG membayar saat positif), dan
  gap-through-stop: candle yang membuka melewati SL/TP diisi pada
  harga pembukaannya, bukan harga level.
- **4.4 Sharpe/Sortino berbasis waktu.** Kurva ekuitas kini dicatat
  SETIAP candle (mark-to-market), dan Sharpe/Sortino di-annualisasi
  dengan akar jumlah bar per tahun dari interval — bukan lagi
  akar jumlah trade.
- **4.5 Riset** (`core/research.py` + CLI `research`): walk-forward
  expanding-window (parameter min_score × versi skor dipilih dari
  train saja, dinilai di test), baseline entri acak 20 seed dengan
  aturan keluar & biaya yang sama, dan laporan Markdown di `reports/`
  yang WAJIB memuat peringatan ketidakpastian — tanpa klaim profit.
  `reports/` masuk .gitignore (artefak hasil, bukan kode).
- Catatan: suite test kini ~80 detik karena uji walk-forward/riset
  menjalankan banyak backtest penuh; itu harga kejujuran statistiknya.

## Fase 5 — Kualitas & Operasi — SELESAI, 108 test hijau (102 → +6)

- **5.1 Alat kualitas.** Dependensi dev dipisah ke
  `requirements-dev.txt` (pytest, pytest-cov, httpx, ruff, mypy, bandit,
  pip-audit); `requirements.txt` kini murni runtime. `pyproject.toml`
  memuat konfigurasi ruff (E/F), mypy, bandit. Hasil lokal: **ruff
  bersih** (13 temuan awal diperbaiki: impor mati, variabel tak
  terpakai, satu import tengah-file), **mypy bersih** untuk
  `core/risk.py` & `core/executor.py` (9 error diperbaiki, termasuk
  penyempitan tipe pada validasi level AI), **bandit 0 temuan**
  (SHA1 id-order ditandai `usedforsecurity=False`; 4 try/except-pass
  best-effort diberi `# nosec` beralasan), **pip-audit: tidak ada
  kerentanan dikenal** pada requirements runtime.
- **5.2 Cakupan modul kritis.** Cakupan `core/risk.py` naik 72% → 78%
  dan `core/executor.py` 75% → 77% lewat test celah kritis: penjaga
  funding, filter BTC (dua arah + pengecualian BTC sendiri), dan
  emergency close saat avgPrice membatalkan SL/TP. CI mengunci gerbang
  `--cov-fail-under=70` untuk kedua modul.
- **5.3 Selfcheck BARU** (`core/selfcheck.py` + CLI `selfcheck`):
  tujuh pemeriksaan — konfigurasi, mode trading (live tanpa token web
  = GAGAL), kunci Binance, direktori data, database, provider AI, dan
  konektivitas (hanya bila kunci ada & diminta). Kode keluar 1 bila ada
  kegagalan wajib.
- **5.4 CI diperluas**: install runtime+dev, ruff, mypy (risk/executor),
  bandit, pip-audit, pytest dengan gerbang cakupan. (File workflow
  hanya bisa diubah lewat antarmuka web GitHub — aplikasi GitHub Muse
  tidak memiliki izin workflows.)
- **5.5 Dokumentasi**: README memuat perintah baru
  (research/performance/reconcile/selfcheck) dan bagian pengembangan;
  STRUKTUR.MD diperbarui.

---

# LAPORAN AKHIR PROGRAM PERBAIKAN (Fase 1–5)

## Ringkasan per fase
1. **Keamanan & eksekusi** (61 test): web aman secara bawaan (token
   wajib untuk bind non-loopback/live, compare_digest, login 5/mnt,
   cookie Secure, health minimal), frontend anti-XSS & token cookie,
   Android cleartext terbatas, urutan close_position aman, tanpa jendela
   tanpa SL (database asinkron + jaring exception pasca-fill), data
   segar wajib untuk eksekusi (TradingService atomik), redaksi log,
   sinkronisasi waktu + retry -1021.
2. **Keandalan & logika** (79 test): streak rugi berjendela 24 jam,
   presisi Decimal, `cli watch` diperbaiki (top_by_volume tidak ada
   sebelumnya), indikator per-interval & EMA seed SMA, ConfigError +
   validasi rentang, Reconciler posisi, jurnal penutupan + metrik
   performa, lifespan, workingType algo.
3. **Strategi sinyal** (93 test): candle tertutup saja, gerbang keras
   MTF/rezim/ADX, skor v2 per kategori dengan syarat 2 kategori
   sepakat, AI hanya konfirmasi/veto + konteks diperkaya + validasi
   level vs ATR + skema pydantic, PositionManager (BE/trailing/TP
   parsial/batas waktu — bawaan mati), risiko portofolio.
4. **Backtest & riset** (102 test): paginasi klines + cache disk,
   jendela indikator tetap 300, slippage/funding/gap-through-stop,
   Sharpe/Sortino berbasis waktu, paritas model keluar di backtest,
   walk-forward + baseline acak + laporan Markdown jujur.
5. **Kualitas & operasi** (108 test): requirements-dev terpisah,
   ruff/mypy/bandit/pip-audit bersih, gerbang cakupan risk/executor,
   selfcheck, CI diperluas, dokumentasi.

## Belum selesai / TODO(verify)
- `TODO(verify)` tunggal di kode: parameter `workingType` pada Algo
  Order API (`core/binance_client.py`) — nama parameter mengikuti
  dokumentasi Binance yang dikenal saat penulisan; verifikasi ulang ke
  dokumentasi resmi sebelum mode live. Nilai tidak valid akan DITOLAK
  bursa dengan error parameter (gagal terlihat, bukan diam-diam).
- Riset pasar nyata (`cli research` atas data Binance sungguhan)
  SENGAJA tidak dijalankan oleh agen: aturan keras program melarang
  akses jaringan ke Binance selama pengembangan. Perintahnya siap;
  jalankan di mesin pengguna.
- Kalibrasi confidence AI & fallback provider otomatis: di luar cakupan
  prompt program ini (AI kini berperan veto/konfirmasi saja).

## Hasil riset
Tidak ada hasil riset pasar yang diklaim — lihat butir di atas.
Kerangka validasinya (walk-forward + baseline acak) teruji pada data
sintetis dan selalu menerbitkan peringatan ketidakpastian.

## Setting baru penting (bawaan)
USE_CLOSED_CANDLES_ONLY=true, SCORING_VERSION=1, AI_CAN_INITIATE=false,
ADX_MIN=20, MTF_GATE_ENABLED=true, REGIME_GATE_ENABLED=true,
CONSECUTIVE_LOSS_WINDOW_HOURS=24, PROTECTION_WORKING_TYPE=MARK_PRICE,
RECONCILER_ENABLED=true, MAX_SAME_DIRECTION_POSITIONS=2,
MAX_OPEN_RISK_PCT=0 (=2× risiko/trade); manajemen keluar
(MOVE_SL_TO_BE/TRAILING_STOP_ENABLED/PARTIAL_TP_ENABLED/MAX_HOLD_HOURS)
dan filter tambahan (MIN_QUOTE_VOLUME_USD/FUNDING_GUARD_MINUTES/
BTC_FILTER) semua BAWAAAN MATI.

## Langkah lanjutan yang disarankan
1. `git pull`, `pip install -r requirements.txt`, restart; jalankan
   `python run.py cli selfcheck`.
2. Forward-test di testnet/dry-run 4–8 minggu; pantau
   `cli performance` dan jurnal keputusan AI.
3. Jalankan `cli research BTCUSDT --days 90` (dan beberapa simbol)
   untuk bukti out-of-sample sebelum mempertimbangkan dana nyata.
4. Aktifkan fitur Fase 3 satu per satu (skor v2 dulu, lalu gerbang
   sudah aktif; manajemen keluar terakhir) sambil membandingkan metrik.
5. Jangan mengaktifkan TRADING_MODE=live sebelum langkah 2–3 memberi
   bukti, dan rotasi kredensial apa pun yang pernah terekspos di chat.
