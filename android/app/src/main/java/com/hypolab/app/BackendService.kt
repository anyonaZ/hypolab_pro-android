package com.hypolab.app

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.Process
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * Foreground service that keeps the HypoLab process (and so the embedded engine) alive while the
 * app is in the background — e.g. while you complete BRAIN biometrics in the browser, or while a
 * long simulation batch runs with the screen off. While jobs are running it also holds a partial
 * wake lock so the CPU and network keep working.
 */
class BackendService : Service() {

    companion object {
        const val CHANNEL_ID = "engine"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_QUIT = "com.hypolab.app.QUIT"
        private const val POLL_MS = 15_000L
        private const val WAKE_TIMEOUT_MS = 30 * 60_000L

        fun start(context: Context) {
            val intent = Intent(context, BackendService::class.java)
            runCatching { androidx.core.content.ContextCompat.startForegroundService(context, intent) }
                .onFailure { Log.w("HypoLab", "Could not start the engine service", it) }
        }
    }

    @Volatile private var alive = true
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastText = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        goForeground(getString(R.string.engine_starting))
        BackendRuntime.ensureStarted(this)
        thread(name = "hypolab-monitor", isDaemon = true) { monitor() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_QUIT) {
            quit()
            return START_NOT_STICKY
        }
        return START_NOT_STICKY
    }

    private fun goForeground(text: String) {
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(text), type)
        lastText = text
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val quit = PendingIntent.getService(
            this, 1, Intent(this, BackendService::class.java).setAction(ACTION_QUIT),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(0, getString(R.string.notif_quit), quit)
            .build()
    }

    private fun updateText(text: String) {
        if (text == lastText) return
        lastText = text
        runCatching {
            NotificationManagerCompat.from(this).notify(NOTIFICATION_ID, buildNotification(text))
        }
    }

    private fun monitor() {
        while (alive) {
            val st = BackendRuntime.state
            when (st.phase) {
                BackendRuntime.Phase.RUNNING -> {
                    val running = runningJobs()
                    if (running > 0) {
                        holdWakeLock()
                        updateText(getString(R.string.notif_jobs, running))
                    } else {
                        releaseWakeLock()
                        updateText(getString(R.string.notif_ready))
                    }
                }
                BackendRuntime.Phase.ERROR -> updateText(getString(R.string.engine_failed))
                else -> updateText(st.message.ifBlank { getString(R.string.engine_starting) })
            }
            Thread.sleep(if (st.phase == BackendRuntime.Phase.RUNNING) POLL_MS else 1_000L)
        }
    }

    private fun runningJobs(): Int = try {
        val conn = BackendRuntime.open("/api/meta/jobs?limit=60").apply { readTimeout = 10_000 }
        conn.inputStream.bufferedReader().use { r ->
            val jobs = JSONObject(r.readText()).optJSONArray("jobs")
            (0 until (jobs?.length() ?: 0)).count { jobs!!.getJSONObject(it).optString("status") == "running" }
        }
    } catch (e: Exception) {
        0
    }

    @Suppress("WakelockTimeout")
    private fun holdWakeLock() {
        val lock = wakeLock ?: (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "HypoLab:jobs").also {
                it.setReferenceCounted(false)
                wakeLock = it
            }
        lock.acquire(WAKE_TIMEOUT_MS)   // renewed on every poll while jobs run
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
    }

    private fun quit() {
        alive = false
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
        // The engine runs inside this process; ending the process is the clean way to stop it.
        Process.killProcess(Process.myPid())
    }

    override fun onDestroy() {
        alive = false
        releaseWakeLock()
        super.onDestroy()
    }
}
