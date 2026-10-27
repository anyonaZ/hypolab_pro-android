package com.hypolab.app

import android.Manifest
import android.annotation.SuppressLint
import androidx.appcompat.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.MimeTypeMap
import android.webkit.RenderProcessGoneDetail
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabColorSchemeParams
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class MainActivity : AppCompatActivity() {

    private lateinit var root: ViewGroup
    private lateinit var web: WebView
    private lateinit var splash: View
    private lateinit var statusText: TextView
    private lateinit var progress: ProgressBar

    private var loadedPort = 0
    private var uiShown = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    /** True while the WebView shows HypoLab's own UI (checked by [WebBridge]). */
    @Volatile var isShowingLocalPage = false
        private set

    private val stateListener: (BackendRuntime.State) -> Unit = { render(it) }

    private val fileChooser = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data))
        fileCallback = null
    }
    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        root = findViewById(R.id.root)
        web = findViewById(R.id.web)
        splash = findViewById(R.id.splash)
        statusText = findViewById(R.id.status)
        progress = findViewById(R.id.progress)
        findViewById<Button>(R.id.restartButton).setOnClickListener { RestartActivity.restart(this) }
        findViewById<Button>(R.id.copyButton).setOnClickListener {
            val text = findViewById<TextView>(R.id.errorText).text
            (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(ClipData.newPlainText("HypoLab error", text))
            Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show()
        }

        enterImmersive()
        // Keep text fields above the on-screen keyboard (edge-to-edge windows don't resize by default).
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            v.setPadding(0, 0, 0, ime.bottom)
            insets
        }
        setupWebView()

        onBackPressedDispatcher.addCallback(this) {
            if (uiShown && web.canGoBack()) web.goBack() else moveTaskToBack(true)
        }

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        BackendService.start(this)
        BackendRuntime.ensureStarted(this)
        BackendRuntime.addListener(stateListener)
        render(BackendRuntime.state)
    }

    // ── engine state → UI ─────────────────────────────────────────────────────────────

    private fun render(s: BackendRuntime.State) {
        when (s.phase) {
            BackendRuntime.Phase.IDLE, BackendRuntime.Phase.STARTING -> {
                statusText.text = s.message.ifBlank { getString(R.string.engine_starting) }
            }
            BackendRuntime.Phase.RUNNING -> if (loadedPort != s.port) loadUi(s.port)
            BackendRuntime.Phase.ERROR -> showError(s.error)
        }
    }

    private fun loadUi(port: Int) {
        loadedPort = port
        statusText.text = "Opening the workspace…"
        val origin = "http://127.0.0.1:$port"
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setCookie(origin, "hypolab_token=${BackendRuntime.token}; Path=/; HttpOnly; SameSite=Strict")
            flush()
        }
        web.loadUrl("$origin/")
    }

    private fun revealUi() {
        if (uiShown) return
        uiShown = true
        web.visibility = View.VISIBLE
        splash.animate().alpha(0f).setDuration(250).withEndAction { splash.visibility = View.GONE }.start()
        maybeAskBatteryExemption()
    }

    private fun showError(error: String) {
        uiShown = false
        splash.alpha = 1f
        splash.visibility = View.VISIBLE
        web.visibility = View.INVISIBLE
        progress.visibility = View.GONE
        statusText.setText(R.string.engine_failed)
        findViewById<TextView>(R.id.errorText).text = error.ifBlank { "Unknown error." }
        findViewById<View>(R.id.errorBox).visibility = View.VISIBLE
        findViewById<View>(R.id.errorActions).visibility = View.VISIBLE
    }

    // ── WebView ───────────────────────────────────────────────────────────────────────

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    private fun setupWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.hl_bg))
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            textZoom = 100
            useWideViewPort = true
            loadWithOverviewMode = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            mediaPlaybackRequiresUserGesture = true
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            userAgentString = "$userAgentString HypoLabAndroid/${BuildConfig.VERSION_NAME}"
        }
        web.addJavascriptInterface(WebBridge(this), "HypoLabAndroid")

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (BackendRuntime.isLocal(url.host)) {
                    if (url.path.orEmpty().startsWith("/api/") && request.isForMainFrame) {
                        // e.g. "Backup database" / "Export" links: save instead of navigating away.
                        Downloads.fromEngine(this@MainActivity, url.toString(), null)
                        return true
                    }
                    return false
                }
                openExternal(url.toString())
                return true
            }

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                isShowingLocalPage = BackendRuntime.isLocal(Uri.parse(url).host)
            }

            override fun onPageFinished(view: WebView, url: String) {
                isShowingLocalPage = BackendRuntime.isLocal(Uri.parse(url).host)
                if (isShowingLocalPage) revealUi()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame && BackendRuntime.isLocal(request.url.host)) {
                    Log.w("HypoLab", "UI load failed (${error.description}); retrying")
                    view.postDelayed({ view.reload() }, 1500)
                }
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                // The page renderer crashed or was reclaimed; the engine is unaffected. Rebuild the UI.
                Log.w("HypoLab", "WebView renderer gone (crash=${detail.didCrash()}); recreating")
                root.removeView(view)
                view.destroy()
                recreate()
                return true
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean {
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(R.string.ok) { _, _ -> result.confirm() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }

            override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton(R.string.ok) { _, _ -> result.confirm() }
                    .setNegativeButton(R.string.cancel) { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }

            override fun onJsPrompt(
                view: WebView, url: String, message: String, defaultValue: String?, result: JsPromptResult,
            ): Boolean {
                val input = EditText(this@MainActivity).apply { setText(defaultValue.orEmpty()) }
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setView(input)
                    .setPositiveButton(R.string.ok) { _, _ -> result.confirm(input.text.toString()) }
                    .setNegativeButton(R.string.cancel) { _, _ -> result.cancel() }
                    .setOnCancelListener { result.cancel() }
                    .show()
                return true
            }

            override fun onShowFileChooser(
                view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    fileChooser.launch(chooserIntent(params))
                    true
                } catch (e: ActivityNotFoundException) {
                    fileCallback = null
                    false
                }
            }

            override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                Log.d("HypoLabWeb", "${msg.messageLevel()} ${msg.message()} @${msg.sourceId()}:${msg.lineNumber()}")
                return true
            }
        }

        web.setDownloadListener { url, _, contentDisposition, mimetype, _ ->
            if (BackendRuntime.isLocal(Uri.parse(url).host)) {
                Downloads.fromEngine(this, url, URLUtil.guessFileName(url, contentDisposition, mimetype))
            } else {
                openExternal(url)
            }
        }
    }

    /** Document picker for `<input type="file">` (PDF papers, JSON imports, database restores). */
    private fun chooserIntent(params: WebChromeClient.FileChooserParams): Intent {
        val mimes = params.acceptTypes.orEmpty()
            .flatMap { it.split(",") }
            .map { it.trim().lowercase() }
            .filter { it.isNotEmpty() }
            .map { if (it.startsWith(".")) MimeTypeMap.getSingleton().getMimeTypeFromExtension(it.drop(1)) ?: "*/*" else it }
            .distinct()
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        // Only filter for PDFs; JSON and SQLite files are often reported as application/octet-stream.
        if (mimes.isNotEmpty() && mimes.all { it == "application/pdf" }) {
            intent.putExtra(Intent.EXTRA_MIME_TYPES, mimes.toTypedArray())
        }
        if (params.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        return intent
    }

    /** BRAIN pages, biometrics and other sites open in a Chrome Custom Tab (or the default browser). */
    fun openExternal(url: String) {
        val uri = Uri.parse(url)
        if (uri.scheme != "http" && uri.scheme != "https") return
        try {
            val colors = CustomTabColorSchemeParams.Builder()
                .setToolbarColor(ContextCompat.getColor(this, R.color.hl_bg)).build()
            CustomTabsIntent.Builder().setDefaultColorSchemeParams(colors).setShowTitle(true).build()
                .launchUrl(this, uri)
        } catch (e: Exception) {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (e2: ActivityNotFoundException) {
                Toast.makeText(this, "No browser available to open $url", Toast.LENGTH_LONG).show()
            }
        }
    }

    // ── battery optimisation ──────────────────────────────────────────────────────────

    private fun maybeAskBatteryExemption() {
        val prefs = getSharedPreferences("hypolab", MODE_PRIVATE)
        if (prefs.getBoolean("battery_asked", false)) return
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        prefs.edit().putBoolean("battery_asked", true).apply()
        AlertDialog.Builder(this)
            .setTitle(R.string.battery_title)
            .setMessage(R.string.battery_message)
            .setPositiveButton(R.string.battery_allow) { _, _ -> requestBatteryExemption() }
            .setNegativeButton(R.string.battery_later, null)
            .show()
    }

    @SuppressLint("BatteryLife")
    fun requestBatteryExemption() {
        try {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        } catch (e: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    // ── window ────────────────────────────────────────────────────────────────────────

    private fun enterImmersive() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enterImmersive()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    override fun onPause() {
        web.onPause()
        CookieManager.getInstance().flush()
        super.onPause()
    }

    override fun onDestroy() {
        BackendRuntime.removeListener(stateListener)
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        if (isFinishing) {
            (web.parent as? ViewGroup)?.removeView(web)
            web.destroy()
        }
        super.onDestroy()
    }
}
