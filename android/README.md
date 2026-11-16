# D TRADE — Aplikasi Android DTRADE

Aplikasi Android pendamping **DTRADE**. Aplikasi ini adalah
WebView aman di atas Web UI: seluruh logika trading tetap berjalan di
backend Python (komputer/VPS Anda), aplikasi hanya menampilkannya di
ponsel dengan nama **D TRADE**.

- **Paket:** `com.devcoderama.dtrade`
- **Min. Android:** 7.0 (API 24) · **Target:** Android 14 (API 34)
- **Stack:** Kotlin, satu Activity, tanpa AndroidX — APK kecil & cepat.

## Cara Pakai

1. Jalankan backend di komputer/VPS Anda: `python run.py web`
   (secara bawaan di `http://<IP-komputer>:8000`).
2. Pasang APK **D TRADE** (dari halaman Releases repo ini).
3. Buka aplikasi → menu ⋮ → **Alamat Server** → isi alamat backend Anda,
   contoh `http://192.168.1.10:8000` (ponsel & komputer satu Wi-Fi), atau
   domain VPS Anda. Alamat tersimpan di perangkat.
4. Semua fitur Web UI (dasbor, scan, analisis + grafik candlestick,
   backtest, eksekusi) bekerja persis seperti di browser.

> Bawaan alamat server adalah `http://10.0.2.2:8000` (localhost komputer
> host dari emulator Android) — ganti sesuai alamat backend Anda.

## Mode Mandiri (sejak v1.2.0; grafik live sejak v1.3.0)

Bila server tidak terjangkau, aplikasi otomatis beralih ke **mode
mandiri** (bisa juga dipilih manual lewat tombol menu melayang →
**Mode Mandiri**): halaman bawaan `assets/standalone.html` menampilkan
**sinyal teknikal** dan **grafik candlestick semua simbol USDT
perpetual Binance Futures** langsung dari API publik Binance — tanpa
AI, tanpa eksekusi, tanpa kunci API. Skor −100…+100 dihitung di
perangkat dari EMA20/50, MACD, RSI, dan Bollinger Bands; ketuk simbol
untuk grafik + rincian alasannya. Tersedia sakelar Mainnet/Testnet di
kanan atas.

Grafik detailnya **live via WebSocket** Binance Futures
(`wss://fstream.binance.com` / `wss://stream.binancefuture.com`,
stream `kline_<interval>`): candle berjalan diperbarui tiap tick,
skor dihitung ulang setiap candle ditutup, ada indikator titik
**LIVE**, reconnect otomatis dengan backoff, dan REST menjadi cadangan
bila WS putus. Pilihan timeframe lengkap ala Binance: 1m, 3m, 5m, 15m,
30m, 1h, 2h, 4h, 6h, 12h, 1d, 3d, 1w (tab bisa digeser).

Sejak v1.4.0, detail simbol juga menampilkan **rencana trade
indikatif** bila sinyal LONG/SHORT: **Entry** (harga penutupan
terakhir), **Take Profit** 2,5×ATR(14), dan **Stop Loss** 1,5×ATR(14)
— rumus yang sama dengan fallback ATR backend (R:R 1:1,67) — sebagai
panel angka + garis putus-putus ENTRY/TP/SL langsung di grafik.
Level dihitung ulang setiap candle ditutup; sinyal NETRAL tidak
memunculkan rencana.

Sejak v1.5.0, daftar simbol dan header detail menampilkan **logo
koin** seperti aplikasi Binance: gambar dimuat dari CDN resmi
Binance (`bin.bnbstatic.com/static/assets/logos/<BASE>.png`, memakai
`baseAsset` dari exchangeInfo; awalan `1000` dicoba dilepas), cadangan
CDN cryptocurrency-icons (jsDelivr), dan bila keduanya tidak punya
ikonnya, tampil avatar huruf berwarna deterministik dari nama koin —
tidak pernah ada gambar rusak. Logo dimuat sebagai subresource gambar
biasa (bukan navigasi), jadi daftar-izin host WebView tidak berubah.

Catatan teknis: halaman dimuat lewat `loadDataWithBaseURL` (base host
semu `dtrade.local`, jadi `allowFileAccess` tetap `false`), dan panggilan
REST-nya lewat jembatan `AndroidBridge` yang **hanya** mengizinkan GET
https ke `fapi.binance.com` / `testnet.binancefuture.com` (WebSocket
dibuka langsung oleh halaman ke host Binance yang sama). Tombol
"Hubungkan"/"Ubah" di halaman mandiri (atau menu **Beranda Server**)
kembali ke Web UI server / membuka dialog alamat server.

Sejak v1.3.0 aplikasi memakai tema **NoActionBar** — tidak ada lagi
bilah judul bawaan Android; menu aplikasi (Beranda Server / Mode
Mandiri / Muat Ulang / Alamat Server) hidup di **tombol melayang ⋮**
di kanan bawah layar.

## Catatan Keamanan Jaringan (penting sejak pengerasan 2026-11-16)

Aplikasi kini **menolak HTTP (cleartext) secara bawaan**; hanya
`localhost` dan `10.0.2.2` (emulator) yang diizinkan memakai HTTP.
Akibatnya, alamat backend berupa IP LAN biasa (mis. `http://192.168.1.10:8000`)
**tidak akan dimuat** sampai Anda memilih salah satu:

1. **(Dianjurkan) Tailscale/HTTPS** — jalankan backend di belakang
   Tailscale atau reverse proxy HTTPS, lalu isi alamat `https://…` di
   aplikasi. Tidak perlu mengubah aplikasi sama sekali.
2. **Tambahkan IP LAN Anda secara eksplisit** — Android tidak mendukung
   rentang CIDR di `network_security_config.xml`; tambahkan satu entri
   untuk IP persis backend Anda lalu build ulang APK:

   ```xml
   <domain-config cleartextTrafficPermitted="true">
       <domain includeSubdomains="false">192.168.1.10</domain>
   </domain-config>
   ```

Backend juga kini **menolak start** bila bind non-loopback tanpa
`WEB_API_TOKEN` — isi token di `.env` backend; Web UI akan memintanya
sekali di dalam aplikasi.

## Build dari Sumber

Syarat: JDK 17 + Android SDK (platform 34, build-tools 34).

```bash
cd android
gradle assembleDebug      # APK debug
gradle assembleRelease    # APK rilis (butuh keystore, lihat bawah)
```

### Penandatanganan Rilis

APK rilis di halaman Releases ditandatangani dengan keystore pribadi
pemilik repo yang **tidak** disertakan di sini. Untuk menandatangani
build Anda sendiri:

1. Buat keystore:
   `keytool -genkeypair -keystore dtrade-release.jks -alias dtrade -keyalg RSA -keysize 2048 -validity 10000`
2. Buat berkas `keystore.properties` (di luar repo / jangan di-commit):

   ```properties
   storeFile=/path/ke/dtrade-release.jks
   storePassword=…
   keyAlias=dtrade
   keyPassword=…
   ```
3. Build dengan `DTRADE_KEYSTORE_PROPS=/path/ke/keystore.properties gradle assembleRelease`,
   atau salin berkasnya ke folder `android/` (sudah di-gitignore).
