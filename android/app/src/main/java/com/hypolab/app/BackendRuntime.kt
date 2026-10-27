package com.hypolab.app

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom
import java.util.concurrent.CopyOnWriteArraySet
import kotlin.concurrent.thread

/**
 * Owns the embedded HypoLab engine: CPython (Chaquopy) running the FastAPI backend on 127.0.0.1.
 *
 * The engine lives for the whole app process; the activity only displays it, and
 * [BackendService] keeps the process alive while the app is in the background.
 */
object BackendRuntime {
    private const val TAG = "HypoLab"
    const val PREFERRED_PORT = 8766
    private const val BOOT_TIMEOUT_MS = 240_000L

    enum class Phase { IDLE, STARTING, RUNNING, ERROR }

    data class State(
        val phase: Phase,
        val port: Int = 0,
        val message: String = "",
        val error: String = "",
    )

    /** Per-process secret that the backend requires on every /api request. */
    val token: String = ByteArray(24).also { SecureRandom().nextBytes(it) }
        .joinToString("") { "%02x".format(it) }

    @Volatile
    var state: State = State(Phase.IDLE)
        private set

    private val listeners = CopyOnWriteArraySet<(State) -> Unit>()
    private val main = Handler(Looper.getMainLooper())

    fun addListener(l: (State) -> Unit) { listeners += l }
    fun removeListener(l: (State) -> Unit) { listeners -= l }

    private fun publish(s: State) {
        state = s
        main.post { listeners.forEach { it(s) } }
    }

    val origin: String get() = "http://127.0.0.1:${state.port}"

    @Synchronized
    fun ensureStarted(context: Context) {
        if (state.phase == Phase.STARTING || state.phase == Phase.RUNNING) return
        val app = context.applicationContext
        publish(State(Phase.STARTING, message = app.getString(R.string.engine_starting)))
        thread(name = "hypolab-boot") { boot(app) }
    }

    private fun boot(ctx: Context) {
        val t0 = SystemClock.elapsedRealtime()
        try {
            if (!Python.isStarted()) {
                publish(State(Phase.STARTING, message = "Starting Python…"))
                Python.start(AndroidPlatform(ctx))
            }
            publish(State(Phase.STARTING, message = "Preparing the workspace…"))
            val www = WebAssets.sync(ctx)
            val data = File(ctx.filesDir, "data").apply { mkdirs() }

            publish(State(Phase.STARTING, message = "Loading research modules…"))
            val server = Python.getInstance().getModule("hypolab_android.server")
            val port = server.callAttr(
                "start", data.absolutePath, www.absolutePath, PREFERRED_PORT, token,
                BuildConfig.VERSION_NAME,
            ).toInt()

            while (true) {
                val st = JSONObject(server.callAttr("status").toString())
                when (st.optString("status")) {
                    "running" -> {
                        Log.i(TAG, "Engine ready on port $port in ${SystemClock.elapsedRealtime() - t0} ms; " +
                            "modules=${st.optJSONArray("modules")}")
                        publish(State(Phase.RUNNING, port = port))
                        return
                    }
                    "error", "stopped" -> {
                        publish(State(Phase.ERROR, port = port, error = st.optString("error", "Engine stopped.")))
                        return
                    }
                }
                if (SystemClock.elapsedRealtime() - t0 > BOOT_TIMEOUT_MS) {
                    publish(State(Phase.ERROR, port = port, error = "The engine did not start within ${BOOT_TIMEOUT_MS / 1000}s."))
                    return
                }
                Thread.sleep(150)
            }
        } catch (t: Throwable) {
            Log.e(TAG, "Engine failed to start", t)
            publish(State(Phase.ERROR, error = Log.getStackTraceString(t)))
        }
    }

    /** Opens a connection to the local engine with the access token attached. */
    fun open(pathOrUrl: String): HttpURLConnection {
        val url = if (pathOrUrl.startsWith("http")) URL(pathOrUrl) else URL(origin + pathOrUrl)
        require(isLocal(url.host)) { "Refusing to send the engine token to ${url.host}" }
        return (url.openConnection() as HttpURLConnection).apply {
            setRequestProperty("X-HypoLab-Token", token)
            connectTimeout = 10_000
            readTimeout = 600_000
        }
    }

    fun isLocal(host: String?): Boolean = host == "127.0.0.1" || host == "localhost"
}
