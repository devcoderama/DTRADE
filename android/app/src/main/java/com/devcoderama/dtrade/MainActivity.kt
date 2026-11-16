package com.devcoderama.dtrade

import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
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

    /** Isi assets/standalone.html, dibaca sekali lalu disimpan di memori. */
    private var standaloneHtml: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        }
        webView.addJavascriptInterface(ApiBridge(), "AndroidBridge")

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
                return target.host != allowedHost && target.host != STANDALONE_HOST
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
        } else {
            webView.restoreState(savedInstanceState)
        }
    }

    private fun serverUrl(): String {
        var url = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "http://$url"
        return url.trimEnd('/') + "/"
    }

    private fun loadHome() {
        standaloneMode = false
        fabMenu.visibility = View.VISIBLE
        webView.loadUrl(serverUrl())
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
        webView.loadDataWithBaseURL(
            "https://$STANDALONE_HOST/", html, "text/html", "UTF-8", null
        )
    }

    private fun showError() {
        webView.visibility = View.GONE
        errorView.visibility = View.VISIBLE
        errorView.text =
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
                prefs.edit().putString(KEY_URL, input.text.toString().trim()).apply()
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
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    /**
     * Jembatan JavaScript untuk halaman mode mandiri. Permukaan yang
     * diekspos sengaja sempit dan read-only:
     * - fetch(): GET https HANYA ke host API publik Binance dan host RSS
     *   berita crypto (daftar-izin di bawah); hasil dikembalikan sebagai
     *   teks lewat __apiResolve.
     * - getServerUrl(): alamat server yang dikonfigurasi (untuk label).
     * - openServer(): kembali memuat Web UI server.
     * - openExternal(): membuka tautan https (artikel berita) di browser
     *   ponsel — WebView sendiri tetap terkunci ke host server.
     * Tidak ada kredensial, tidak ada penulisan, tidak ada host lain.
     */
    inner class ApiBridge {
        @JavascriptInterface
        fun fetch(callbackId: Int, url: String) {
            Thread {
                var body = ""
                var error: String? = null
                try {
                    val parsed = URL(url)
                    if (parsed.protocol != "https" ||
                        (parsed.host !in BINANCE_HOSTS && parsed.host !in NEWS_HOSTS)
                    ) {
                        throw SecurityException("host tidak diizinkan")
                    }
                    val conn = parsed.openConnection() as HttpURLConnection
                    conn.connectTimeout = 15000
                    conn.readTimeout = 15000
                    conn.setRequestProperty("User-Agent", "DTrade/1.2 Android")
                    val code = conn.responseCode
                    val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                    body = stream?.bufferedReader()?.readText() ?: ""
                    if (code !in 200..299) error = "HTTP $code"
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

        /** Bawaan untuk emulator Android (10.0.2.2 = localhost komputer host). */
        private const val DEFAULT_URL = "http://10.0.2.2:8000"

        /** Host semu (base URL) halaman mode mandiri. */
        private const val STANDALONE_HOST = "dtrade.local"

        /** Host API publik Binance yang boleh dihubungi jembatan mode mandiri. */
        private val BINANCE_HOSTS = setOf("fapi.binance.com", "testnet.binancefuture.com")

        /**
         * Host RSS berita crypto untuk tab Berita mode mandiri (v1.6.0) —
         * hanya feed publik read-only dari 11 sumber ini.
         */
        private val NEWS_HOSTS = setOf(
            "www.coindesk.com", "cointelegraph.com", "decrypt.co",
            "cryptopotato.com", "www.theblock.co", "u.today",
            "ambcrypto.com", "beincrypto.com", "bitcoinmagazine.com",
            "cryptoslate.com", "www.newsbtc.com"
        )

        private const val MENU_HOME = 1
        private const val MENU_RELOAD = 2
        private const val MENU_URL = 3
        private const val MENU_STANDALONE = 4
    }
}
