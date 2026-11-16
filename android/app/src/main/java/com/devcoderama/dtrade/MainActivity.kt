package com.devcoderama.dtrade

import android.app.Activity
import android.app.AlertDialog
import android.app.Dialog
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import java.security.MessageDigest
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/**
 * D TRADE — aplikasi Android pendamping binance-ai-future.
 *
 * Aplikasi ini adalah WebView aman di atas Web UI binance-ai-future:
 * seluruh logika trading tetap berjalan di backend Python (komputer/VPS
 * pengguna), aplikasi hanya menampilkannya di ponsel. Alamat server dapat
 * diganti dari menu "Alamat Server" dan tersimpan di perangkat.
 *
 * Mode mandiri (sejak v1.2.0): bila server tidak terjangkau — atau dipilih
 * dari menu — aplikasi menampilkan halaman bawaan (assets/standalone.html)
 * berisi sinyal teknikal + grafik candlestick semua simbol Binance Futures
 * dari API publik Binance. Tanpa AI, tanpa eksekusi, tanpa kredensial.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var errorView: TextView
    private lateinit var fabMenu: Button
    private lateinit var prefs: SharedPreferences

    /** true selama halaman mode mandiri yang sedang ditampilkan. */
    private var standaloneMode = false

    /** Dialog pembaruan yang sedang tampil (untuk dibersihkan di onDestroy). */
    private var updateDialog: Dialog? = null

    /** true bila verifikasi tanda tangan gagal — aplikasi diblokir total. */
    private var tampered = false

    /** Kedalaman navigasi halaman mandiri (0 daftar, 1 detail, 2 pembaca). */
    @Volatile
    private var navDepth = 0

    /** Isi assets/standalone.html, dibaca sekali lalu disimpan di memori. */
    private var standaloneHtml: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // v1.14.0: verifikasi keaslian APK SEBELUM apa pun dimuat.
        // Aplikasi yang dimodifikasi (mod/repack) pasti ditandatangani
        // ulang dengan kunci orang lain — tanda tangan tidak akan cocok
        // dengan sertifikat rilis resmi, dan aplikasi berhenti di sini
        // dengan dialog unduh versi resmi. Build debug (yang bisa
        // diinspeksi) juga ditolak.
        if (!signatureOk() ||
            (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        ) {
            tampered = true
            val tv = TextView(this)
            tv.text = "Aplikasi tidak resmi — unduh versi resmi D TRADE dari GitHub."
            tv.setPadding(48, 96, 48, 48)
            setContentView(tv)
            showTamperDialog()
            return
        }
        // Cookie bersama untuk koneksi jembatan (HttpURLConnection) —
        // dibutuhkan sesi quote Yahoo Finance (cookie → crumb → quote)
        // untuk data Bid/Offer saham. Hanya cookie pihak-pertama dari
        // host yang diizinkan jembatan yang pernah diminta halaman.
        java.net.CookieHandler.setDefault(
            java.net.CookieManager(null, java.net.CookiePolicy.ACCEPT_ALL))
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        webView = findViewById(R.id.webview)
        progressBar = findViewById(R.id.progress)
        errorView = findViewById(R.id.error_view)
        // Sejak v1.3.0 tidak ada action bar (tema NoActionBar) — menu
        // aplikasi hidup di tombol melayang ini (PopupMenu). Sejak v1.6.0
        // tombol disembunyikan selama mode mandiri tampil, karena halaman
        // mandiri kini punya navigasi bawah sendiri (tab Lainnya memuat
        // menu yang sama).
        fabMenu = findViewById(R.id.fab_menu)
        fabMenu.setOnClickListener { showAppMenu(it) }

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            // Pengerasan: WebView hanya boleh memuat URL server backend —
            // tidak ada akses berkas lokal / content provider. Jembatan
            // JavaScript (ApiBridge) hanya mengekspos GET read-only ke
            // API publik Binance untuk mode mandiri; tidak ada kredensial
            // apa pun yang disimpan aplikasi ini — autentikasi API (bila
            // aktif) hidup di cookie sesi WebView milik server.
            allowFileAccess = false
            allowContentAccess = false
            // Pengerasan tambahan (v1.13.0): tanpa konten campuran
            // HTTP-di-dalam-HTTPS, tanpa akses berkas lintas-asal, tanpa
            // jendela pop-up otomatis, tanpa geolokasi, tanpa simpan
            // formulir, Safe Browsing aktif, dan inspeksi WebView mati.
            @Suppress("DEPRECATION")
            allowFileAccessFromFileURLs = false
            @Suppress("DEPRECATION")
            allowUniversalAccessFromFileURLs = false
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            setGeolocationEnabled(false)
            @Suppress("DEPRECATION")
            saveFormData = false
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                safeBrowsingEnabled = true
            }
        }
        WebView.setWebContentsDebuggingEnabled(false)
        // v1.13.0: jembatan JavaScript TIDAK lagi terdaftar permanen —
        // ia hanya dipasang selama halaman mode mandiri tampil (lihat
        // loadStandalone/loadHome). Halaman Web UI server (konten jarak
        // jauh) tidak boleh menyentuh jembatan sama sekali.

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progressBar.progress = newProgress
                progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                errorView.visibility = View.GONE
                webView.visibility = View.VISIBLE
            }

            // Fase 6-C3: daftar-izin host — WebView HANYA boleh bernavigasi
            // ke host server yang dikonfigurasi pengguna, atau host semu
            // halaman mode mandiri (dtrade.local). Tautan/redirect ke host
            // lain diblokir agar halaman server yang terkompromi tidak
            // dapat membawa WebView (beserta cookie sesi) ke situs asing.
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean {
                val target = request?.url ?: return false
                val allowedHost = android.net.Uri.parse(serverUrl()).host
                if (target.host != allowedHost && target.host != STANDALONE_HOST) {
                    return true
                }
                // v1.13.0: HTTP teks-terang hanya untuk host lokal/LAN
                // privat. Server publik wajib HTTPS — cookie sesi backend
                // tidak boleh melintasi internet tanpa enkripsi.
                if (target.scheme == "http" && !isPrivateOrLocalHost(target.host)) {
                    return true
                }
                return false
            }

            // v1.8.0: halaman mode mandiri dimuat dengan base URL semu
            // https://dtrade.local/ sehingga subresource relatif (CSS +
            // font Font Awesome di assets/fa/) tidak bisa diambil dari
            // jaringan — sajikan langsung dari aset aplikasi. Jalur /fa/
            // dan /img/ (gambar lokal, mis. QRIS donasi v1.23.0) di host
            // semu yang dilayani, dengan penolakan "..".
            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest?
            ): WebResourceResponse? {
                val url = request?.url ?: return null
                val path = url.path ?: return null
                if (url.host != STANDALONE_HOST ||
                    !(path.startsWith("/fa/") || path.startsWith("/img/")) ||
                    path.contains("..")
                ) {
                    return null
                }
                return try {
                    val assetPath = path.removePrefix("/")
                    val mime = when {
                        assetPath.endsWith(".css") -> "text/css"
                        assetPath.endsWith(".woff2") -> "font/woff2"
                        assetPath.endsWith(".jpg") || assetPath.endsWith(".jpeg") -> "image/jpeg"
                        assetPath.endsWith(".png") -> "image/png"
                        else -> "application/octet-stream"
                    }
                    WebResourceResponse(mime, "UTF-8", assets.open(assetPath))
                } catch (e: Exception) {
                    null
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true) {
                    // Server tidak terjangkau → jatuh ke mode mandiri.
                    if (!standaloneMode) loadStandalone() else showError()
                }
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                if (request?.isForMainFrame == true &&
                    (errorResponse?.statusCode ?: 0) >= 500 && !standaloneMode
                ) {
                    loadStandalone()
                }
            }
        }
        errorView.setOnClickListener { showUrlDialog() }

        if (savedInstanceState == null) {
            loadHome()
        } else if (savedInstanceState.getBoolean(KEY_STANDALONE, false)) {
            // Halaman loadDataWithBaseURL tidak bisa dipulihkan oleh
            // restoreState — muat ulang mode mandiri secara eksplisit
            // (v1.13.0; sebelumnya layar bisa kosong setelah proses mati).
            loadStandalone()
        } else {
            webView.restoreState(savedInstanceState)
        }

        // v1.9.0: cek pembaruan WAJIB setiap aplikasi dibuka.
        checkForUpdate()
    }

    /**
     * Cek rilis terbaru D TRADE di GitHub (API publik, tanpa kunci).
     * Bila versi terbaru lebih tinggi dari yang terpasang, tampilkan
     * dialog yang TIDAK bisa ditutup selain dengan mengunduh versi
     * terbaru. Semua kegagalan (offline, API berubah, dsb.) diabaikan
     * diam-diam — aplikasi tidak pernah terkunci hanya karena cek gagal.
     */
    private fun checkForUpdate() {
        Thread {
            try {
                val conn = URL(
                    "https://api.github.com/repos/devcoderama/binance-ai-future/releases/latest"
                ).openConnection() as HttpURLConnection
                conn.setRequestProperty("User-Agent", "D-Trade-Android")
                conn.setRequestProperty("Accept", "application/vnd.github+json")
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                // v1.13.0: jangan ikuti redirect — jawaban selain 200
                // langsung dari api.github.com dianggap gagal (diam).
                conn.instanceFollowRedirects = false
                if (conn.responseCode != 200) return@Thread
                val json = JSONObject(conn.inputStream.bufferedReader().readText())
                val latest = json.optString("tag_name").removePrefix("v")
                val page = json.optString("html_url")
                @Suppress("DEPRECATION")
                val current = packageManager.getPackageInfo(packageName, 0)
                    .versionName ?: "0"
                if (page.startsWith("https://") && isNewerVersion(latest, current) &&
                    updatePromptedVersion != latest
                ) {
                    updatePromptedVersion = latest
                    runOnUiThread { showUpdateDialog(latest, current, page) }
                }
            } catch (e: Exception) {
                /* abaikan — jangan pernah mengunci pengguna karena cek gagal */
            }
        }.start()
    }

    /** Bandingkan versi semver sederhana "a.b.c" per komponen angka. */
    private fun isNewerVersion(latest: String, current: String): Boolean {
        val l = latest.split(".").map { it.toIntOrNull() ?: 0 }
        val c = current.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(l.size, c.size)) {
            val lv = l.getOrElse(i) { 0 }
            val cv = c.getOrElse(i) { 0 }
            if (lv != cv) return lv > cv
        }
        return false
    }

    /**
     * Popup pembaruan bertema aplikasi (v1.24.0): dialog kustom
     * gelap + aksen emas mengikuti tema D TRADE — bukan lagi
     * AlertDialog bawaan Android. Sifatnya tetap sama: wajib
     * (tidak bisa ditutup) dan tampil lagi sepulang dari browser
     * sampai aplikasi benar-benar diperbarui.
     */
    private fun showUpdateDialog(latest: String, current: String, page: String) {
        if (isFinishing) return
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        fun lp(top: Int) = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { topMargin = dp(top) }
        fun label(text: String, sizeSp: Float, color: Int, bold: Boolean) =
            TextView(this).apply {
                this.text = text
                textSize = sizeSp
                setTextColor(color)
                if (bold) setTypeface(
                    typeface, android.graphics.Typeface.BOLD
                )
            }

        val gold = Color.parseColor("#F0B90B")
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(22), dp(24), dp(24))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#151A21"))
                cornerRadius = dp(18).toFloat()
                setStroke(dp(1), Color.parseColor("#2A3038"))
            }
        }
        card.addView(label("D TRADE", 13f, gold, true))
        card.addView(label("Pembaruan Tersedia", 21f, Color.WHITE, true), lp(4))
        card.addView(
            label(
                "Versi terbaru D TRADE sudah tersedia. Perbarui sekarang " +
                    "untuk melanjutkan menggunakan aplikasi.",
                14.5f, Color.parseColor("#9AA7B4"), false
            ),
            lp(10)
        )
        /* Kotak versi: terpasang → terbaru (terbaru disorot emas). */
        val verText = "v$current   →   v$latest"
        val verSpan = android.text.SpannableString(verText).apply {
            setSpan(
                android.text.style.ForegroundColorSpan(gold),
                verText.indexOf("v$latest"), verText.length, 0
            )
            setSpan(
                android.text.style.StyleSpan(android.graphics.Typeface.BOLD),
                verText.indexOf("v$latest"), verText.length, 0
            )
        }
        card.addView(TextView(this).apply {
            text = verSpan
            textSize = 15f
            setTextColor(Color.parseColor("#C3CAD4"))
            gravity = android.view.Gravity.CENTER
            setPadding(dp(12), dp(12), dp(12), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#1C2129"))
                cornerRadius = dp(10).toFloat()
            }
        }, lp(16))
        val btn = Button(this).apply {
            text = "Unduh Versi Terbaru"
            textSize = 15f
            isAllCaps = false
            setTextColor(Color.parseColor("#0B0E11"))
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            background = GradientDrawable().apply {
                setColor(gold)
                cornerRadius = dp(12).toFloat()
            }
            setOnClickListener {
                updateDialog?.dismiss()
                try {
                    startActivity(
                        Intent(Intent.ACTION_VIEW, android.net.Uri.parse(page))
                    )
                } catch (e: Exception) {
                    Toast.makeText(
                        this@MainActivity, "Tidak ada browser untuk membuka unduhan",
                        Toast.LENGTH_LONG
                    ).show()
                }
                // Tampilkan lagi sepulang dari browser: pembaruan bersifat wajib.
                showUpdateDialog(latest, current, page)
            }
        }
        card.addView(btn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(52)
        ).apply { topMargin = dp(20) })

        val dlg = Dialog(this)
        dlg.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE)
        dlg.setContentView(card)
        dlg.setCancelable(false)
        dlg.window?.setBackgroundDrawable(
            android.graphics.drawable.ColorDrawable(Color.TRANSPARENT)
        )
        updateDialog = dlg
        dlg.show()
        dlg.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.88).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT
        )
    }

    /**
     * SHA-256 sertifikat penanda tangan APK ini harus sama persis
     * dengan sertifikat rilis resmi (v1.14.0). Gagal membaca tanda
     * tangan = dianggap tidak resmi (fail-closed).
     */
    private fun signatureOk(): Boolean {
        return try {
            val infos = if (android.os.Build.VERSION.SDK_INT >= 28) {
                packageManager.getPackageInfo(
                    packageName, PackageManager.GET_SIGNING_CERTIFICATES
                ).signingInfo?.apkContentsSigners?.map { it.toByteArray() }
            } else {
                @Suppress("DEPRECATION")
                packageManager.getPackageInfo(
                    packageName, PackageManager.GET_SIGNATURES
                ).signatures?.map { it.toByteArray() }
            } ?: return false
            infos.any { sig ->
                val digest = MessageDigest.getInstance("SHA-256").digest(sig)
                digest.joinToString("") { "%02x".format(it) } ==
                    OFFICIAL_CERT_SHA256
            }
        } catch (e: Exception) {
            false
        }
    }

    /** Dialog pemblokir untuk APK modifikasi — hanya jalan keluar resmi. */
    private fun showTamperDialog() {
        if (isFinishing) return
        AlertDialog.Builder(this)
            .setTitle("Versi Tidak Resmi Terdeteksi")
            .setMessage(
                "Tanda tangan aplikasi ini tidak cocok dengan rilis resmi " +
                    "D TRADE. Aplikasi kemungkinan telah dimodifikasi " +
                    "(mod/repack) — versi seperti ini berisiko disusupi " +
                    "iklan, malware, atau pencuri data.\n\n" +
                    "Unduh versi resmi dari GitHub untuk melanjutkan."
            )
            .setCancelable(false)
            .setPositiveButton("Unduh Versi Resmi") { _, _ ->
                try {
                    startActivity(
                        Intent(Intent.ACTION_VIEW, android.net.Uri.parse(OFFICIAL_RELEASES_URL))
                    )
                } catch (e: Exception) {
                    Toast.makeText(
                        this, "Tidak ada browser untuk membuka unduhan",
                        Toast.LENGTH_LONG
                    ).show()
                }
                showTamperDialog()
            }
            .show()
    }

    private fun serverUrl(): String {
        var url = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "http://$url"
        return url.trimEnd('/') + "/"
    }

    private fun loadHome() {
        standaloneMode = false
        fabMenu.visibility = View.VISIBLE
        // v1.13.0: halaman server adalah konten jarak jauh — jembatan
        // JavaScript dicabut sebelum memuatnya.
        webView.removeJavascriptInterface("AndroidBridge")
        val url = serverUrl()
        val uri = android.net.Uri.parse(url)
        if (uri.scheme == "http" && !isPrivateOrLocalHost(uri.host)) {
            showError(
                "Alamat server memakai HTTP ke host publik:\n$url\n\n" +
                    "Demi keamanan, HTTP hanya diizinkan untuk alamat lokal/LAN " +
                    "(localhost, 10.x, 172.16–31.x, 192.168.x). Untuk server di " +
                    "internet gunakan HTTPS.\n\n" +
                    "Ketuk di sini untuk mengatur alamat server."
            )
            return
        }
        webView.loadUrl(url)
    }

    /**
     * Mode mandiri: muat halaman bawaan (sinyal + grafik dari API publik
     * Binance) tanpa perlu server. HTML dibaca dari aset lalu dimuat dengan
     * base URL https semu agar tidak perlu akses berkas (allowFileAccess
     * tetap false).
     */
    private fun loadStandalone() {
        standaloneMode = true
        fabMenu.visibility = View.GONE
        val html = standaloneHtml ?: try {
            assets.open("standalone.html").bufferedReader().readText()
        } catch (e: Exception) {
            showError()
            return
        }
        standaloneHtml = html
        Toast.makeText(
            this,
            "Server tidak terjangkau — mode mandiri (data Binance langsung)",
            Toast.LENGTH_LONG
        ).show()
        // v1.13.0: jembatan hanya hidup untuk halaman bawaan ini.
        webView.addJavascriptInterface(ApiBridge(), "AndroidBridge")
        webView.loadDataWithBaseURL(
            "https://$STANDALONE_HOST/", html, "text/html", "UTF-8", null
        )
    }

    private fun showError(message: String? = null) {
        webView.visibility = View.GONE
        errorView.visibility = View.VISIBLE
        errorView.text = message ?:
            "Tidak dapat terhubung ke server binance-ai-future di:\n${serverUrl()}\n\n" +
                "Pastikan backend berjalan di komputer/VPS Anda:\n" +
                "python run.py web\n\n" +
                "Ketuk di sini untuk mengatur alamat server."
    }

    private fun showUrlDialog() {
        val input = EditText(this)
        input.setText(prefs.getString(KEY_URL, DEFAULT_URL))
        input.hint = "cth: http://192.168.1.10:8000"
        AlertDialog.Builder(this)
            .setTitle("Alamat Server")
            .setMessage("Alamat Web UI binance-ai-future di komputer/VPS Anda (satu jaringan Wi-Fi untuk IP lokal).")
            .setView(input)
            .setPositiveButton("Simpan") { _, _ ->
                val raw = input.text.toString().trim()
                val probe = android.net.Uri.parse(
                    if (raw.startsWith("http")) raw else "http://$raw")
                // v1.13.0: tolak HTTP ke host publik sejak di dialog.
                if (probe.scheme == "http" && !isPrivateOrLocalHost(probe.host)) {
                    Toast.makeText(
                        this,
                        "HTTP hanya untuk alamat lokal/LAN. Gunakan HTTPS untuk server publik.",
                        Toast.LENGTH_LONG
                    ).show()
                    return@setPositiveButton
                }
                prefs.edit().putString(KEY_URL, raw).apply()
                loadHome()
            }
            .setNegativeButton("Batal", null)
            .show()
    }

    /** Menu aplikasi (pengganti menu action bar) pada tombol melayang. */
    private fun showAppMenu(anchor: View) {
        val popup = PopupMenu(this, anchor)
        popup.menu.add(0, MENU_HOME, 0, "Beranda Server")
        popup.menu.add(0, MENU_STANDALONE, 0, "Mode Mandiri")
        popup.menu.add(0, MENU_RELOAD, 0, "Muat Ulang")
        popup.menu.add(0, MENU_URL, 0, "Alamat Server")
        popup.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                MENU_HOME -> loadHome()
                MENU_STANDALONE -> loadStandalone()
                MENU_RELOAD -> webView.reload()
                MENU_URL -> showUrlDialog()
                else -> return@setOnMenuItemClickListener false
            }
            true
        }
        popup.show()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (tampered) {
            super.onBackPressed()
            return
        }
        // v1.14.0: tombol Back Android mengikuti navigasi internal
        // halaman mandiri (pembaca → detail → daftar), bukan langsung
        // keluar aplikasi.
        if (standaloneMode && navDepth >= 2) {
            webView.evaluateJavascript(
                "if (window.__readerBack) window.__readerBack();", null)
            return
        }
        if (standaloneMode && navDepth == 1) {
            webView.evaluateJavascript("closeDetail()", null)
            return
        }
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (tampered) return
        outState.putBoolean(KEY_STANDALONE, standaloneMode)
        webView.saveState(outState)
    }

    override fun onDestroy() {
        updateDialog?.dismiss()
        updateDialog = null
        super.onDestroy()
    }

    /**
     * Host lokal/LAN privat yang masih diizinkan memakai HTTP: localhost,
     * nama *.local (mDNS), dan IP literal loopback / privat / link-local.
     * Nama host publik lewat HTTP selalu ditolak (v1.13.0).
     */
    private fun isPrivateOrLocalHost(host: String?): Boolean {
        if (host.isNullOrBlank()) return false
        val h = host.lowercase()
        if (h == "localhost" || h.endsWith(".local") || h == "::1") return true
        val parts = h.split(".")
        if (parts.size != 4) return false
        val nums = parts.map { it.toIntOrNull() ?: return false }
        val a = nums[0]
        val b = nums[1]
        return a == 10 || a == 127 ||
            (a == 172 && b in 16..31) ||
            (a == 192 && b == 168) ||
            (a == 169 && b == 254)
    }

    /**
     * Jembatan JavaScript untuk halaman mode mandiri. Permukaan yang
     * diekspos sengaja sempit dan read-only:
     * - fetch(): GET https HANYA ke host API publik Binance, host RSS
     *   berita, endpoint terjemahan Google, dan Yahoo Finance untuk data
     *   saham (daftar-izin di bawah); hasil dikembalikan sebagai teks
     *   lewat __apiResolve.
     * - getServerUrl(): alamat server yang dikonfigurasi (untuk label).
     * - openServer(): kembali memuat Web UI server.
     * - openExternal(): membuka tautan https (artikel berita) di browser
     *   ponsel — WebView sendiri tetap terkunci ke host server.
     * - downloadQris(): menyimpan gambar QRIS terbundel ke galeri
     *   pengguna (satu-satunya penulisan, hanya atas ketukan pengguna).
     * Tidak ada kredensial dan tidak ada host lain.
     */
    inner class ApiBridge {
        @JavascriptInterface
        fun fetch(callbackId: Int, url: String) {
            Thread {
                var body = ""
                var error: String? = null
                try {
                    // v1.13.0: redirect diikuti MANUAL (maks 5) dan SETIAP
                    // lompatan divalidasi ulang ke daftar-izin — sebelumnya
                    // HttpURLConnection mengikuti redirect otomatis ke host
                    // mana pun, melewati pemeriksaan host awal.
                    var current = URL(url)
                    var redirects = 0
                    var done = false
                    while (!done) {
                        if (current.protocol != "https" ||
                            (current.host !in BINANCE_HOSTS && current.host !in NEWS_HOSTS &&
                                current.host !in TRANSLATE_HOSTS && current.host !in STOCK_HOSTS)
                        ) {
                            throw SecurityException("host tidak diizinkan")
                        }
                        val conn = current.openConnection() as HttpURLConnection
                        conn.connectTimeout = 15000
                        conn.readTimeout = 15000
                        conn.instanceFollowRedirects = false
                        conn.setRequestProperty("User-Agent", "DTrade/1.2 Android")
                        val code = conn.responseCode
                        if (code in 300..399) {
                            val loc = conn.getHeaderField("Location")
                            if (loc.isNullOrBlank() || ++redirects > 5) {
                                throw SecurityException("redirect tidak valid")
                            }
                            current = URL(current, loc)
                        } else {
                            val stream =
                                if (code in 200..299) conn.inputStream else conn.errorStream
                            body = stream?.bufferedReader()?.readText() ?: ""
                            if (code !in 200..299) error = "HTTP $code"
                            done = true
                        }
                    }
                } catch (e: Exception) {
                    error = e.message ?: "network error"
                }
                val payload = JSONObject()
                    .put("id", callbackId)
                    .put("body", body)
                    .put("error", error ?: JSONObject.NULL)
                webView.post {
                    webView.evaluateJavascript("window.__apiResolve($payload)", null)
                }
            }.start()
        }

        @JavascriptInterface
        fun getServerUrl(): String = serverUrl()

        /**
         * Laporan kedalaman navigasi halaman mandiri untuk tombol Back
         * Android (v1.14.0): 0 daftar, 1 detail grafik, 2 pembaca berita.
         */
        @JavascriptInterface
        fun setNavDepth(depth: Int) {
            navDepth = depth
        }

        /**
         * Bagikan analisis (v1.19.0): teks ringkasan + gambar kartu
         * (PNG base64 dari canvas halaman) lewat panel berbagi Android
         * — pengguna bebas memilih WhatsApp, Telegram, atau aplikasi
         * lain. Gambar disajikan ShareProvider dari cache aplikasi.
         * Bila gambar gagal dibuat, teks saja yang dibagikan.
         */
        @JavascriptInterface
        fun shareAnalysis(text: String, imageDataUrl: String) {
            var uri: android.net.Uri? = null
            if (imageDataUrl.startsWith("data:image/png;base64,")) {
                try {
                    val bytes = android.util.Base64.decode(
                        imageDataUrl.substringAfter("base64,"),
                        android.util.Base64.DEFAULT
                    )
                    val dir = java.io.File(cacheDir, "share")
                    dir.mkdirs()
                    java.io.File(dir, "analisis.png").writeBytes(bytes)
                    uri = android.net.Uri.parse(
                        "content://com.devcoderama.dtrade.share/analisis.png")
                } catch (e: Exception) {
                    uri = null
                }
            }
            val intent = Intent(Intent.ACTION_SEND).apply {
                if (uri != null) {
                    type = "image/png"
                    putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                } else {
                    type = "text/plain"
                }
                putExtra(Intent.EXTRA_TEXT, text)
            }
            runOnUiThread {
                try {
                    startActivity(Intent.createChooser(intent, "Bagikan analisis"))
                } catch (e: Exception) {
                    Toast.makeText(
                        this@MainActivity, "Tidak ada aplikasi untuk berbagi",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }

        /**
         * Unduh gambar QRIS donasi (v1.24.0): menyalin berkas aset
         * terbundel img/qris-donasi.jpg ke galeri pengguna agar bisa
         * diunggah ke fitur scan aplikasi bank/e-wallet. Android 10+
         * lewat MediaStore (tanpa izin); versi lama ke folder
         * Pictures khusus aplikasi + pemindaian media. Hasilnya
         * dilaporkan lewat Toast. Satu-satunya penulisan berkas di
         * jembatan ini, dan hanya atas ketukan pengguna.
         */
        @JavascriptInterface
        fun downloadQris() {
            Thread {
                val ok = try {
                    val name = "qris-donasi.jpg"
                    if (android.os.Build.VERSION.SDK_INT >= 29) {
                        val values = android.content.ContentValues().apply {
                            put(
                                android.provider.MediaStore.Images.Media.DISPLAY_NAME,
                                name
                            )
                            put(
                                android.provider.MediaStore.Images.Media.MIME_TYPE,
                                "image/jpeg"
                            )
                            put(
                                android.provider.MediaStore.Images.Media.RELATIVE_PATH,
                                "Pictures/D TRADE"
                            )
                        }
                        val uri = contentResolver.insert(
                            android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                            values
                        )
                        if (uri == null) {
                            false
                        } else {
                            contentResolver.openOutputStream(uri)?.use { out ->
                                assets.open("img/qris-donasi.jpg").use { input ->
                                    input.copyTo(out)
                                }
                            }
                            true
                        }
                    } else {
                        val dir = getExternalFilesDir(
                            android.os.Environment.DIRECTORY_PICTURES
                        ) ?: filesDir
                        val f = java.io.File(dir, name)
                        assets.open("img/qris-donasi.jpg").use { input ->
                            f.outputStream().use { input.copyTo(it) }
                        }
                        android.media.MediaScannerConnection.scanFile(
                            this@MainActivity, arrayOf(f.absolutePath),
                            arrayOf("image/jpeg"), null
                        )
                        true
                    }
                } catch (e: Exception) {
                    false
                }
                runOnUiThread {
                    Toast.makeText(
                        this@MainActivity,
                        if (ok) "Gambar QRIS tersimpan di galeri (Pictures/D TRADE)"
                        else "Gagal menyimpan gambar QRIS",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }.start()
        }

        @JavascriptInterface
        fun openServer() {
            webView.post { loadHome() }
        }

        /** Buka dialog alamat server (tombol "Ubah" di halaman mandiri). */
        @JavascriptInterface
        fun editServerUrl() {
            webView.post { showUrlDialog() }
        }

        /**
         * Buka tautan artikel (dari tab Berita) di browser ponsel.
         * Hanya skema https yang diterima; kegagalan membuka browser
         * diabaikan diam-diam agar halaman tidak terganggu.
         */
        @JavascriptInterface
        fun openExternal(url: String) {
            if (!url.startsWith("https://")) return
            webView.post {
                try {
                    startActivity(
                        android.content.Intent(
                            android.content.Intent.ACTION_VIEW,
                            android.net.Uri.parse(url)
                        ).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                } catch (e: Exception) {
                    Toast.makeText(
                        this@MainActivity,
                        "Tidak ada browser untuk membuka tautan",
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }
        }
    }

    companion object {
        private const val PREFS = "dtrade"
        private const val KEY_URL = "server_url"
        private const val KEY_STANDALONE = "standalone_mode"

        /**
         * SHA-256 sertifikat penanda tangan rilis resmi D TRADE —
         * APK modifikasi (ditandatangani ulang pihak lain) tidak akan
         * pernah cocok dengan nilai ini (v1.14.0).
         */
        private const val OFFICIAL_CERT_SHA256 =
            "6f199a57ad908109ffe7b8be3cdacd0af51c877b7c1b5600f87c4ba90c9f349c"

        /** Halaman unduhan resmi untuk dialog aplikasi tidak resmi. */
        private const val OFFICIAL_RELEASES_URL =
            "https://github.com/devcoderama/binance-ai-future/releases/latest"

        /**
         * Versi yang sudah dimunculkan dialog pembaruannya pada proses
         * ini — mencegah dialog menumpuk saat onCreate berjalan lagi
         * (v1.13.0).
         */
        @Volatile
        private var updatePromptedVersion: String? = null

        /** Bawaan untuk emulator Android (10.0.2.2 = localhost komputer host). */
        private const val DEFAULT_URL = "http://10.0.2.2:8000"

        /** Host semu (base URL) halaman mode mandiri. */
        private const val STANDALONE_HOST = "dtrade.local"

        /** Host API publik Binance yang boleh dihubungi jembatan mode mandiri. */
        private val BINANCE_HOSTS = setOf(
            "fapi.binance.com", "testnet.binancefuture.com",
            "api.binance.com", "testnet.binance.vision"
        )

        /**
         * Host RSS berita crypto untuk tab Berita mode mandiri (v1.6.0) —
         * hanya feed publik read-only dari 11 sumber ini.
         */
        private val NEWS_HOSTS = setOf(
            "www.coindesk.com", "cointelegraph.com", "decrypt.co",
            "cryptopotato.com", "www.theblock.co", "u.today",
            "ambcrypto.com", "beincrypto.com", "bitcoinmagazine.com",
            "cryptoslate.com", "www.newsbtc.com",
            "nftplazas.com", "nftnow.com",
            "www.cnbcindonesia.com", "finance.detik.com", "www.idxchannel.com",
            "news.google.com",
            // Penerbit arus utama lain yang sering menjadi tujuan
            // redirect Google News untuk berita pendukung sinyal
            // (v1.14.0) — tanpa ini artikelnya gagal dibuka pembaca.
            "www.kontan.co.id", "www.bisnis.com", "www.kompas.com",
            "www.liputan6.com", "www.antaranews.com", "investor.id",
            "www.katadata.co.id", "www.tempo.co",
            "www.reuters.com", "www.investing.com"
        )

        /** Endpoint terjemahan Google (EN→ID) untuk tab Berita (v1.7.0). */
        private val TRANSLATE_HOSTS = setOf("translate.googleapis.com")

        /** Yahoo Finance — data harian saham IDX & global (v1.9.0). */
        private val STOCK_HOSTS = setOf(
            "query1.finance.yahoo.com", "query2.finance.yahoo.com",
            "fc.yahoo.com"
        )

        private const val MENU_HOME = 1
        private const val MENU_RELOAD = 2
        private const val MENU_URL = 3
        private const val MENU_STANDALONE = 4
    }
}
