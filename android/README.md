# D TRADE — Aplikasi Android binance-ai-future

Aplikasi Android pendamping **binance-ai-future**. Aplikasi ini adalah
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
