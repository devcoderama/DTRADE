package com.devcoderama.dtrade

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileNotFoundException

/**
 * Provider konten minimal (v1.19.0) agar gambar kartu analisis yang
 * dibuat halaman mandiri di cache aplikasi bisa dibagikan ke aplikasi
 * lain (WhatsApp, Telegram, dll.) lewat ACTION_SEND — tanpa perlu
 * pustaka AndroidX FileProvider. Hanya menyajikan satu berkas:
 * content://com.devcoderama.dtrade.share/analisis.png
 * Provider tidak diekspor; akses hanya lewat pemberian izin URI
 * sesaat dari intent berbagi.
 */
class ShareProvider : ContentProvider() {

    override fun onCreate(): Boolean = true

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        val ctx = context ?: throw FileNotFoundException("tanpa konteks")
        val f = File(File(ctx.cacheDir, "share"), "analisis.png")
        if (!f.exists()) throw FileNotFoundException("kartu belum dibuat")
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun getType(uri: Uri): String = "image/png"

    override fun query(
        uri: Uri, projection: Array<out String>?, selection: String?,
        selectionArgs: Array<out String>?, sortOrder: String?
    ): Cursor? = null

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0

    override fun update(
        uri: Uri, values: ContentValues?, selection: String?,
        selectionArgs: Array<out String>?
    ): Int = 0
}
