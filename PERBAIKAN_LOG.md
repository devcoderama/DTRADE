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
