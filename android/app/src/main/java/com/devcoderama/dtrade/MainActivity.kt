package com.devcoderama.dtrade

import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView

/**
 * D TRADE — aplikasi Android pendamping binance-ai-future.
 *
 * Aplikasi ini adalah WebView aman di atas Web UI binance-ai-future:
 * seluruh logika trading tetap berjalan di backend Python (komputer/VPS
 * pengguna), aplikasi hanya menampilkannya di ponsel. Alamat server dapat
 * diganti dari menu "Alamat Server" dan tersimpan di perangkat.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var errorView: TextView
    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        title = "D TRADE"

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        webView = findViewById(R.id.webview)
        progressBar = findViewById(R.id.progress)
        errorView = findViewById(R.id.error_view)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            // Pengerasan: WebView hanya boleh memuat URL server backend —
            // tidak ada akses berkas lokal / content provider, dan tidak
            // ada jembatan JavaScript ke kode native (addJavascriptInterface
            // memang tidak pernah dipakai). Tidak ada kredensial apa pun
            // yang disimpan aplikasi ini; autentikasi API (bila aktif)
            // hidup di cookie sesi WebView milik server.
            allowFileAccess = false
            allowContentAccess = false
        }

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

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true) showError()
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
        webView.loadUrl(serverUrl())
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

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, MENU_HOME, 0, "Beranda")
        menu.add(0, MENU_RELOAD, 0, "Muat Ulang")
        menu.add(0, MENU_URL, 0, "Alamat Server")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            MENU_HOME -> loadHome()
            MENU_RELOAD -> webView.reload()
            MENU_URL -> showUrlDialog()
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    companion object {
        private const val PREFS = "dtrade"
        private const val KEY_URL = "server_url"

        /** Bawaan untuk emulator Android (10.0.2.2 = localhost komputer host). */
        private const val DEFAULT_URL = "http://10.0.2.2:8000"
        private const val MENU_HOME = 1
        private const val MENU_RELOAD = 2
        private const val MENU_URL = 3
    }
}
