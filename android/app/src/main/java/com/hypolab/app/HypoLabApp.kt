package com.hypolab.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class HypoLabApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                BackendService.CHANNEL_ID,
                getString(R.string.channel_engine),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.channel_engine_desc)
                setShowBadge(false)
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }
}
