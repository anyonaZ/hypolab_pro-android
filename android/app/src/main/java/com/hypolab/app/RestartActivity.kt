package com.hypolab.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Process

/**
 * Restarts HypoLab with a fresh engine. Python cannot be re-initialised inside a running process,
 * so this short-lived activity (in its own process) ends the main process and relaunches the app.
 */
class RestartActivity : Activity() {

    companion object {
        private const val EXTRA_PID = "pid"

        fun restart(from: Context) {
            from.startActivity(
                Intent(from, RestartActivity::class.java)
                    .putExtra(EXTRA_PID, Process.myPid())
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pid = intent.getIntExtra(EXTRA_PID, -1)
        if (pid > 0) Process.killProcess(pid)
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        )
        finish()
        Runtime.getRuntime().exit(0)
    }
}
