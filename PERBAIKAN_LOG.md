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

---

# FASE 6 — Audit November 2026 (branch `fix/audit-nov-2026`)

Program lanjutan atas permintaan pemilik repo: perbaikan bug hasil
audit, dibuktikan dengan test otomatis DAN tes langsung di Binance
Futures TESTNET (aturan keras khusus program ini: hanya testnet,
order testnet kecil diizinkan untuk pengujian, branch + PR — bukan
push langsung ke main). Baseline sebelum perubahan: **108 test hijau**.

## Fase A — Kritis sebelum live — SELESAI (unit), 125 test hijau

- **A1 Race Reconciler vs Executor.** Executor memakai RLock yang
  diekspos (`executor.lock`); seluruh putaran Reconciler berjalan di
  dalamnya dan membaca ulang posisi+algo di dalam lock — eksekusi
  yang sedang memasang SL tidak bisa dinilai dari snapshot lama.
  Masa tenggang `RECONCILER_GRACE_SEC=120`: simbol dengan transisi
  eksekusi lebih muda dari itu dilewati. Write-ahead: executor
  memanggil hook jurnal (`record_intent`) tepat sebelum order entri
  dikirim — registry mengenali posisi + SL-nya sejak awal
  (`open_registry` menerima state SUBMITTING dari entri intent).
  Test: fake dengan latensi `place_algo_order` 0,6 dtk + run_once di
  thread lain → posisi TIDAK ditutup; intent tercatat sebelum entri.
- **A2 Notasi ilmiah.** Semua parameter float dinormalisasi di
  `_request_once` SEBELUM penandatanganan dengan
  `format(Decimal(str(x)), 'f')` — `1.23e-05` menjadi `0.0000123`.
  Test menangkap parameter request untuk order & algo order mikro.
- **A3 Jurnal dry-run.** `open_registry` mengabaikan entri
  `dry_run=True`; langkah (c) reconciler tidak menulis apa pun dalam
  mode dry-run. Test: jurnal dry-run + income akun → nol event close.
- **A4 Secret di repr/log.** Semua field secret Settings kini
  `field(repr=False)` (13 field). `RedactingFormatter` meredaksi
  hasil format akhir termasuk traceback/exc_info. Test keduanya.
- **A5 pending_recovery.** Marker close_position kini membawa
  `client_order_id` korelasi `close-<simbol>-<ts>` sehingga
  CLOSING↔CLOSED berpasangan; `mark_resolved()` menulis event
  RESOLVED (status terminal baru); execution_state.jsonl dirotasi
  pada 5 MB (satu cadangan `.1`). Test: close bersih tidak muncul di
  pending_recovery; RESOLVED membersihkan entri macet.
- **A6 DATA_DIR tunggal.** `load_settings` menghitung DATA_DIR
  absolut (env `DATA_DIR` atau `<root>/data`); risk_state & jurnal
  bawaan kini di bawahnya (CATATAN MIGRASI: jurnal berpindah dari
  `logs/trades.jsonl` ke `data/trades.jsonl` — pindahkan berkas lama
  Anda ke lokasi baru bila ingin riwayat menyatu). ai_settings &
  cache klines mengikuti DATA_DIR. `assert_data_writable`: startup
  LIVE menolak jalan bila DATA_DIR tidak bisa ditulis. Selfcheck
  otomatis berjalan di startup web (hasilnya di log). Docker Compose
  beralih ke named volume (bind mount menimpa izin uid 10001 —
  instruksi chown didokumentasikan di compose).
- **A7 Mode akun & versi endpoint.** Selfcheck memeriksa
  `GET /fapi/v1/positionSide/dual`: Hedge Mode + live = GAGAL (web
  startup juga menolak); ping tak bertanda tangan diganti validasi
  panggilan bertanda tangan + selisih jam server. Migrasi v3 dengan
  fallback: account/balance/positionRisk kini mencoba `/fapi/v3/*`
  dulu (panduan migrasi resmi Binance Futures Connector: v2 account
  & balance digantikan v3 sejak Des 2024, v2 positionRisk deprecated
  Jan 2025), fallback ke v2 bila v3 gagal. Perilaku testnetnya
  diverifikasi pada tahap E2E.

### Fase B — selesai (uji 125 → 150)

- **B1 Id algo unik + buffer BE.** `PositionManager._replace_sl` dan
  pemasangan ulang SL oleh Reconciler kini memakai sufiks cap waktu
  milidetik pada clientAlgoId — pemasangan ke-2 tidak lagi ditolak
  bursa sebagai duplikat. Break-even memakai target entri ±
  `BREAKEVEN_BUFFER_PCT` (baru, bawaan 0,1% ≈ 2× fee taker) agar
  "break-even" tidak lagi keluar rugi sebesar fee.
- **B2 Streak per trade.** `_consecutive_losses` mengelompokkan baris
  REALIZED_PNL per trade (tradeId/tranId; cadangan simbol + ember
  1 detik) dan menjumlahkan fill sebelum menghitung streak — satu
  trade 3-fill = satu rugi.
- **B3 Fail-closed live.** `_consecutive_losses` mengembalikan None
  bila income tak terbaca; `evaluate` MENOLAK sinyal saat live (mode
  dry-run hanya peringatan). `_open_risk` yang None saat live juga
  menolak (sebelumnya pemeriksaan terlewati diam-diam).
- **B4 Baseline oleh reconciler + kunci berkas.** `Reconciler`
  menerima `risk` (web + CLI mengopernya) dan memanggil
  `RiskManager.sync_daily_baselines()` tiap putaran — pergantian hari
  UTC tercatat tepat waktu. Penulisan `risk_state.json` dan append
  jurnal kini di bawah kunci berkas/thread agar proses web & CLI
  tidak saling menimpa.
- **B5 Validasi Settings.** ConfigError baru: RECONCILER_INTERVAL_SEC
  < 5, PARTIAL_TP_FRACTION di luar (0,1), MAX_HOLD_HOURS < 0,
  BREAKEVEN_TRIGGER_R/TRAILING_TRIGGER_R/TRAILING_ATR_MULT <= 0,
  MAX_SYMBOL/TOTAL_EXPOSURE_PCT <= 0, RATE_LIMIT_PER_MINUTE < 0,
  WEB_API_TOKEN terisi tapi < 24 karakter. Nilai non-kritis yang salah
  format kini MEMICU PERINGATAN log (tidak lagi diam-diam bawaan).
- **B6 Jurnal terpisah + cache.** `record_ai_decision` menulis ke
  `ai_decisions.jsonl` di samping jurnal (bukan lagi trades.jsonl);
  append di bawah threading.Lock; `all_entries()` di-cache per
  (mtime, ukuran) dan invalidasi saat record.
- **B7 Strategi.** (a) ATR None → sinyal arah diturunkan NEUTRAL
  (level AI tidak lagi lolos tanpa validasi rentang ATR). (b)
  `snapshot_light` limit 200 → 300 (selaras jalur analisis). (c) MTF
  hanya mengambil klines (1 panggilan/TF) — tren/rezim/ADX dihitung
  build_snapshot dari klines. (d) Gerbang ADX diperbaiki: entri
  pengikut-tren ditolak bila rezim `sideways` ATAU adx14 < ADX_MIN
  pada rezim apa pun (gerbang lama mustahil aktif karena "trending"
  mensyaratkan ADX ≥ 25). (e) `classify_regime` menerima `interval`:
  ambang atr_pct diskalakan √(menit/15) dibatasi 0,5–4×.
- **B8 Refcount stream.** `MarketStream` menghitung pemakai per
  (simbol, interval); unsubscribe hanya mencabut langganan saat
  pemakai terakhir pergi — menutup satu tab UI tidak lagi
  menghentikan tab lain pada simbol yang sama.

**Uji lama yang diubah (aturan 7 — dengan alasan):**
`test_security.py` (token uji diperpanjang ≥24 char mengikuti B5,
termasuk token pada URL WS), `test_phase1.py::test_enforce_web_safety`
(token fixture diperpanjang), `test_phase6_a.py::test_a4_*` (token
fixture diperpanjang), `test_phase3.py::test_gate_adx_and_regime`
(ekspektasi sideways dibalik sesuai B7d), `test_phase3.py::
test_pm_breakeven_*` (target BE 100,0 → 100,1 sesuai buffer B1).

### Fase C — selesai (uji 150 → 160; workflow CI terpisah di a14135c)

- **C1 Hardening web.** Validasi Host di middleware (nama domain
  publik tak dikenal → 400, anti DNS-rebinding; literal IP/localhost/
  host CORS diizinkan agar akses LAN D TRADE & VPS tetap jalan);
  pemeriksaan Origin untuk POST ber-cookie (asing → 403, anti CSRF);
  Content-Type wajib application/json untuk request berbadan (→ 415);
  endpoint baru `POST /api/auth/logout` menghapus cookie sesi;
  `X-Forwarded-For` hanya dipercaya dari `TRUSTED_PROXIES` (baru,
  bawaan kosong) untuk kunci rate-limit; dukungan `?token=` pada
  WebSocket **dihapus** (cookie/header saja).
- **C2 CI & lockfile.** Workflow: `permissions: contents: read`,
  actions ter-pin SHA penuh (checkout v4 `11d5960…`, setup-python v5
  `a26af69…`), instalasi dari `requirements.lock` (76 paket, pip
  freeze), dan gerbang cakupan per berkas via
  `scripts/check_coverage.py`. Ambang ditetapkan dari pengukuran
  nyata (bukan tebakan): risk 78 (terukur 83,4), executor 78 (81,3 —
  naik dari 77,6 berkat 4 uji executor baru), journal 70 (75,9),
  strategy 60 (63,2), reconciler 65 (70,7), position_manager 60
  (66,7), indicators 70 (93,7), market_stream 70 (81,0), settings 80
  (90,2), web/app 50 (59,0).
- **C3 Android.** `shouldOverrideUrlLoading`: WebView hanya boleh
  bernavigasi ke host server yang dikonfigurasi (host lain diblokir);
  `minifyEnabled true` untuk rilis. (Sumber saja — APK belum dibangun
  ulang; perlu rebuild dengan keystore rilis yang sama agar berlaku.)
- **C4 Dokumentasi.** Bagian ini + README (lapisan Fase 6, DATA_DIR,
  logout, token ≥24) + `.env.example` (RECONCILER_GRACE_SEC,
  BREAKEVEN_BUFFER_PCT, DATA_DIR, TRUSTED_PROXIES).

**Uji lama yang diubah (aturan 7):** `test_security.py` — klien uji
memakai base_url literal IP (validasi Host C1) dan uji WS memakai
header X-API-Token menggantikan `?token=` yang dihapus.
`test_phase6_c.py` membaca token/klien secara malas per uji karena
`test_security` me-reload modul web.app saat impor (menangkapnya saat
impor mengikat instance lama).

### E2E Testnet — selesai (skrip: scripts/e2e_testnet.py + scripts/e2e_web_testnet.py)

Semua skenario dijalankan terhadap Binance Futures TESTNET sungguhan
(penjaga base URL di kedua skrip menolak non-testnet). Order kecil
sungguhan, risiko 0,1%/trade. Laporan mentah tersanitasi ada di
`reports/` lokal (tidak di-commit, sesuai aturan program).

**CLI (8/8 LULUS):** (1) fungsi baca — selfcheck hijau, saldo, 528
simbol, snapshot, analyze AI (BangunWeb deepseek-v4-flash), backtest
7 hari, jurnal; (2) trade live BTCUSDT LONG → state PROTECTED, SL/TP
algo terpasang, jurnal tercatat; (3) close_position bersih, tidak di
pending_recovery; (4) simbol harga mikro JCTUSDT @0,0020950 entri &
tutup tanpa error -1100 (A2 terbukti di bursa); (5) penolakan deviasi
harga, kunci harian, dan streak rugi — ketiganya dengan alasan benar;
(6) rekonsiliasi: SL yang dihapus dipasang ulang, algo yatim ETHUSDT
dibatalkan, tutup eksternal tercatat + PnL; (7) smoke CLI watch 1
putaran + konstruksi DashboardApp. Catatan jujur: riwayat income akun
testnet memuat streak rugi lama (18 trade) sehingga RiskManager
menolak SEMUA trade pada awalnya — proteksi bekerja benar; skenario
trade memakai konfigurasi sah MAX_CONSECUTIVE_LOSSES=0 dan logika
streak diverifikasi tersendiri di skenario 5. Kandidat mikro pertama
(PTBUSDT) juga ditolak penjaga deviasi secara sah (harga mark vs pasar
menyimpang 6,77% di buku testnet tak likuid).

**Web (9 LULUS, 1 TIDAK TERVERIFIKASI):** W1 matriks auth lengkap
(401/cookie/viewer 403/logout/batas login 429); W2 dashboard + WS
klines live; W3 dua tab — menutup satu tab tidak memutus lainnya (B8);
W4 XSS (respons selalu JSON, app.js esc()); W5 eksekusi & tutup via
API; **W6 uji ras A1 empiris: 20 siklus buka→tutup dengan reconciler
5 detik — 20/20 bersih, NOL RECOVERY_REQUIRED, nol posisi sehat
ditutup reconciler**; W8 kemurnian jurnal dry-run (A3) di server web;
W9 **workingType='MARK_PRICE' diterima testnet** — TODO(verify) di
`core/binance_client.py` terjawab dan komentarnya diperbarui;
W10 server live menolak start pada Hedge Mode dan akun dikembalikan
ke One-way. **W7 TIDAK TERVERIFIKASI:** dalam tiga jendela pengamatan
8 menit, harga BTC tidak pernah mencapai pemicu 0,3R sehingga geseran
SL oleh PositionManager (BE/trailing) tidak teramati di pasar —
bukan kegagalan kode; logikanya teruji di unit test Fase 3/B1.

Kejadian selama E2E (dicatat apa adanya): jalan-ulang web ke-2
terputus oleh penggantian VM runtime di tengah fase W7; posisi sisa
LONG 0,0169 BTC ditemukan saat pemeriksaan pasca-putus dan ditutup
bersih lewat executor; mode akun tidak sempat berubah. Ekspektasi
skrip diperbaiki dua kali (batas login dapat aktif di percobaan ke-5
karena login berhasil ikut terhitung; penolakan RiskManager pada uji
ras dihitung sebagai skip-ulang, bukan kegagalan) — keduanya perbaikan
skrip uji, bukan kode aplikasi.

**Pembersihan akhir:** akun testnet datar terverifikasi (nol posisi,
nol order biasa, nol algo order), mode One-way, `.env` lokal berisi
kunci testnet DIHAPUS dari mesin agen. Saldo akhir 4.926,87 USDT
(virtual testnet).

---

## Paritas Web UI dengan aplikasi Android (16 Nov 2026, atas permintaan Rams)

Fokus Web UI tetap crypto futures Binance (tanpa saham); berita boleh ditambah.

- **Backend:** `GET /api/tickers` (ticker 24j semua simbol perpetual USDT dari satu panggilan Binance, dinormalisasi `_normalize_tickers`, cache server 20 dtk) dan `GET /api/news` — modul baru `web/news.py`: agregasi RSS/Atom 11 sumber crypto (CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine, CryptoSlate, NewsBTC, AMBCrypto, BeInCrypto, U.Today, CryptoPotato, Blockworks) hanya dengan pustaka standar; `parse_feed` murni (RSS 2.0 + Atom, gambar dari media:content/thumbnail/enclosure/<img> deskripsi), pengambilan per sumber terisolasi, cache memori 5 menit, maks 90 artikel terbaru.
- **Frontend Pasar:** panel + menu "Pasar" — 528 simbol dengan harga/24j/volume langsung (segar otomatis 30 dtk), skor teknikal 1h dihitung di peramban untuk 80 simbol tervolume dengan mesin skor yang sama persis seperti aplikasi Android (EMA/RSI/MACD/Bollinger −100…+100), favorit ★ (localStorage `baf-favs`) + filter, pencarian, urutan Volume/Sinyal/Perubahan, cache-pertama dari localStorage `baf-market-cache`, ketuk baris membuka analisis.
- **Frontend Analisis:** panel Multi-Timeframe (15m/1h/4h/1d + catatan konfluensi, cache 5 menit) di bawah grafik, dan tombol "Salin teks analisis" (ringkasan siap tempel ke WhatsApp/Telegram/dll. dari payload analisis terakhir, termasuk penilaian AI bila ada).
- **Frontend Berita:** panel + menu "Berita" — kartu berita dengan gambar, filter sumber (chip) + pencarian, dimuat malas saat panel terlihat; ketuk membuka situs penerbit.
- **Navigasi bawah mobile** diselaraskan: Home · Pasar · Analysis · Signals · Berita.
- **Verifikasi:** pytest 165/165 lulus (5 tes baru `tests/test_web_news.py`, tanpa jaringan); uji visual end-to-end pada server lokal testnet+dry-run: 528 baris pasar + chip skor terisi, klik BTCUSDT membuka analisis dengan 4 chip MTF selesai ("Selaras: 4 dari 4 timeframe mengarah SHORT" saat pengujian), 90 kartu berita tampil. Tidak ada perubahan pada RiskManager/executor; tidak ada order ditempatkan.

## D TRADE v1.20.0 — Perbaikan bug pasar tercampur (16 Nov 2026, dari tangkapan layar Rams)

- **Gejala:** buka detail saham IDX (BBRI) → kembali ke Pasar → ganti segmen ke Kripto → ketuk BTCUSDT: detail terbuka sebagai saham IDX ("Bursa Indonesia (IDX) · Yahoo Finance"), muncul "Gagal memuat grafik: instrumen tidak ditemukan", grafik/statistik/rencana BBRI tertinggal di layar, dan panel Multi-Timeframe menggantung di "Menghitung skor antar-timeframe…".
- **Akar masalah:** baris daftar crypto (`renderList`) memanggil `openDetail(symbol)` TANPA argumen pasar; fallback `lastMarket` menyimpan pasar detail SEBELUMNYA (dari BBRI = "idx"), sehingga simbol crypto dicari di daftar saham dan tidak ditemukan (`detailEntry = null`). Perilaku fallback ini sisa perbaikan v1.14.0 yang mengasumsikan panggilan tanpa pasar selalu berarti "buka ulang detail yang sama".
- **Perbaikan:** (1) pasar detail kini ditentukan dari simbolnya — fungsi `homeMarket()`/`inMarket()`: pasar eksplisit yang valid untuk simbol itu menang, selain itu pasar asal simbol menurut daftar yang dimuat, terakhir pasar detail saat ini; (2) baris daftar crypto dan pemilih simbol kini mengoper `"crypto"` secara eksplisit; buka-ulang tab Analisis tidak lagi mengoper `lastMarket`; (3) keadaan gagal muat (`paintDetailError`) membersihkan grafik, statistik, alasan, rencana, dan berita terkait milik simbol sebelumnya, serta menyelesaikan panel Multi-Timeframe dengan pesan tidak tersedia.
- **Verifikasi:** harness drive29 meniru langkah persis di atas dua arah (IDX→BBRI→Kripto→BTCUSDT dan sebaliknya) + buka-ulang Analisis tanpa argumen — semua membuka pasar yang benar; APK 397.975 byte, SHA-256 34347ef9…, sertifikat rilis resmi sama.

## D TRADE v1.21.0 — Ganti simbol di Analisis saham (16 Nov 2026, permintaan Rams)

- **Permintaan:** "di analisis saham tambahkan ganti simbol seperti yang ada di crypto".
- **Perubahan:** pemilih simbol (v1.7.0, sebelumnya khusus crypto — `openPicker` menolak pasar saham dan tombol ⇄ disembunyikan di detail saham) kini digeneralisasi: di detail saham, tombol ⇄ tampil dan ketuk judul simbol membuka pemilih berisi daftar pasar yang sedang dibuka (IDX 99 / global 60) lengkap dengan logo emiten, harga, perubahan %, chip sinyal, dan bintang favorit (`pickerStockRowHtml`, aman untuk entri tanpa harga). Pencarian menyaring dari kode, nama perusahaan, dan kata kunci (`kw`); placeholder menyesuaikan pasar ("Cari saham IDX… (cth: GOTO)"). Ketuk baris membuka detail saham itu di pasar yang benar.
- **Verifikasi:** harness drive30 — BBRI → pemilih 99 baris IDX → cari "goto" → tepat GOTO.JK → ketuk → detail GOTO sebagai IDX tanpa error; ketuk judul juga membuka pemilih; regresi pemilih crypto (200 baris, ETHUSDT) dan global (60 baris, AAPL) lulus. APK 398.503 byte, SHA-256 4394a1b6…, sertifikat rilis resmi sama.

## D TRADE v1.22.0 — Gambar bagikan berisi grafik asli (16 Nov 2026, permintaan Rams)

- **Permintaan:** "perbarui gambar yang dibagikan adalah grafik seperti ini dengan entry tp/sl dan skor multitimeframe-nya" (dengan contoh tangkapan layar grafik Analisis).
- **Perubahan:** `drawShareCard` dirancang ulang — renderer baru `drawShareChart` meniru `drawChart` halaman Analisis pada kanvas 1080px: candle + EMA20 (emas)/EMA50 (biru), garis putus-putus ENTRY/TP/SL + nilai di sumbu kanan (label anti-tabrakan, termasuk geser label level di dekat tepi atas menjauhi legenda), harga terakhir, batang volume, kisi + label harga, dan sumbu waktu `fmtWaktuCandle`. Tata letak kartu: header + lencana sisi/skor + harga + satu baris ringkasan RSI/MACD/EMA20/EMA50 (kotak indikator 2×2 dan strip rencana terpisah dihapus — rencana kini terlihat di grafik), lalu grafik 540px, lalu chip skor multi-timeframe; tinggi kanvas 1620 (dengan MTF) / 1480 (tanpa). Teks bagikan tidak berubah.
- **Verifikasi:** harness drive31 merender kartu dari data sungguhan dan diperiksa visual: BTCUSDT SHORT −54 (SL 64820,19 / Entry 64166,10 / TP 63075,95; MTF 15m −48, 1h −54, 4h −34, 1d −60) dan BBRI LONG +36 (Entry 3100, TP 3374,65, SL 2935,21; MTF 1h +31, 1d +36, 1wk NETRAL −15, 1mo SHORT −36). Satu tabrakan label TP vs legenda EMA50 ditemukan dari pemeriksaan visual dan diperbaiki (offset label tepi atas 190→310). APK 399.387 byte, SHA-256 e6be54e6…, sertifikat rilis resmi sama.
