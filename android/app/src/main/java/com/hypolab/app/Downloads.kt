package com.hypolab.app

import android.app.Activity
import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.webkit.URLUtil
import android.widget.Toast
import java.io.File
import java.io.IOException
import java.io.OutputStream
import kotlin.concurrent.thread

/**
 * Saves files the web UI produces (database backups, catalog/prompt exports, CSV evidence) into the
 * public Downloads/HypoLab folder, where the Files app, Drive, etc. can reach them.
 */
object Downloads {

    /** Streams a local engine URL (e.g. /api/settings/storage/backup) into Downloads. */
    fun fromEngine(activity: Activity, url: String, suggestedName: String?) {
        thread(name = "hypolab-download") {
            try {
                val conn = BackendRuntime.open(url)
                val code = conn.responseCode
                if (code !in 200..299) throw IOException("HTTP $code")
                val mime = conn.contentType?.substringBefore(';')?.trim().orEmpty()
                    .ifBlank { "application/octet-stream" }
                val name = sanitize(
                    suggestedName?.takeIf { it.isNotBlank() }
                        ?: URLUtil.guessFileName(url, conn.getHeaderField("Content-Disposition"), mime)
                )
                conn.inputStream.use { input -> save(activity, name, mime) { out -> input.copyTo(out, 1 shl 20) } }
                toast(activity, activity.getString(R.string.saved_to_downloads, name))
            } catch (e: Exception) {
                toast(activity, activity.getString(R.string.download_failed, e.message ?: e.javaClass.simpleName))
            }
        }
    }

    /** Saves bytes created in the page (Blob downloads). */
    fun fromBytes(activity: Activity, filename: String, mime: String, bytes: ByteArray) {
        thread(name = "hypolab-save") {
            try {
                val name = sanitize(filename.ifBlank { "hypolab-export" })
                save(activity, name, mime.ifBlank { "application/octet-stream" }) { it.write(bytes) }
                toast(activity, activity.getString(R.string.saved_to_downloads, name))
            } catch (e: Exception) {
                toast(activity, activity.getString(R.string.download_failed, e.message ?: e.javaClass.simpleName))
            }
        }
    }

    private fun save(ctx: Context, name: String, mime: String, write: (OutputStream) -> Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val resolver = ctx.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, mime)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/HypoLab")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("Could not create the file in Downloads")
            try {
                resolver.openOutputStream(uri)?.use(write) ?: throw IOException("Could not open the file")
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
        } else {
            // Android 8–9: app-specific Downloads folder (no storage permission needed).
            val dir = File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "HypoLab").apply { mkdirs() }
            File(dir, name).outputStream().use(write)
        }
    }

    private fun sanitize(name: String) = name.replace(Regex("[\\\\/:*?\"<>|]"), "_").take(120)

    private fun toast(activity: Activity, msg: String) =
        activity.runOnUiThread { Toast.makeText(activity, msg, Toast.LENGTH_LONG).show() }
}
