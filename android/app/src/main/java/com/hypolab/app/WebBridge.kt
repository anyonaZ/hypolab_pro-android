package com.hypolab.app

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.PowerManager
import android.util.Base64
import android.webkit.JavascriptInterface
import android.widget.Toast
import org.json.JSONObject

/**
 * `window.HypoLabAndroid` in the web UI. Only HypoLab's own pages (served from 127.0.0.1) are ever
 * shown in the WebView — every other URL opens in the browser — and each call re-checks that.
 */
class WebBridge(private val activity: MainActivity) {

    private fun allowed() = activity.isShowingLocalPage

    @JavascriptInterface
    fun appInfo(): String = JSONObject().apply {
        put("version", BuildConfig.VERSION_NAME)
        put("versionCode", BuildConfig.VERSION_CODE)
        put("android", Build.VERSION.RELEASE)
        put("sdk", Build.VERSION.SDK_INT)
        put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
        put("abi", Build.SUPPORTED_ABIS.firstOrNull() ?: "")
        put("batteryUnrestricted", batteryUnrestricted())
    }.toString()

    /** The WebView's drawing area in dp; the page scales its layout canvas to this. */
    @JavascriptInterface
    fun viewportDp(): String {
        val (w, h) = activity.webViewSizeDp()
        return JSONObject().put("w", w).put("h", h).toString()
    }

    @JavascriptInterface
    fun openExternal(url: String) {
        if (!allowed()) return
        activity.runOnUiThread { activity.openExternal(url) }
    }

    @JavascriptInterface
    fun download(url: String, filename: String?) {
        if (!allowed()) return
        val abs = if (url.startsWith("/")) BackendRuntime.origin + url else url
        Downloads.fromEngine(activity, abs, filename)
    }

    @JavascriptInterface
    fun saveBase64(filename: String, mime: String, base64: String) {
        if (!allowed()) return
        Downloads.fromBytes(activity, filename, mime, Base64.decode(base64, Base64.DEFAULT))
    }

    @JavascriptInterface
    fun copyText(text: String) {
        if (!allowed()) return
        activity.runOnUiThread {
            val cm = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("HypoLab", text))
            if (Build.VERSION.SDK_INT < 33) {   // Android 13+ shows its own clipboard confirmation
                Toast.makeText(activity, R.string.copied, Toast.LENGTH_SHORT).show()
            }
        }
    }

    @JavascriptInterface
    fun requestUnrestrictedBattery() {
        if (!allowed()) return
        activity.runOnUiThread { activity.requestBatteryExemption() }
    }

    @JavascriptInterface
    fun restartApp() {
        if (!allowed()) return
        activity.runOnUiThread { RestartActivity.restart(activity) }
    }

    private fun batteryUnrestricted(): Boolean =
        (activity.getSystemService(Context.POWER_SERVICE) as PowerManager)
            .isIgnoringBatteryOptimizations(activity.packageName)
}
