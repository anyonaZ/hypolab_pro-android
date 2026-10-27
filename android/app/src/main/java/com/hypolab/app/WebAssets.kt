package com.hypolab.app

import android.content.Context
import android.content.res.AssetManager
import java.io.File

/**
 * The backend serves the React UI from `<root>/frontend/dist`, like the desktop build. The built
 * UI ships inside the APK under assets/www and is copied to private storage after each install or
 * update (FastAPI's static file serving needs real files).
 */
object WebAssets {
    fun sync(ctx: Context): File {
        val root = File(ctx.filesDir, "www")
        val dist = File(root, "frontend/dist")
        val stamp = File(root, ".installed")
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        val want = "${BuildConfig.VERSION_CODE}:${info.lastUpdateTime}"
        if (stamp.isFile && stamp.readText() == want && File(dist, "index.html").isFile) return root

        dist.deleteRecursively()
        copyDir(ctx.assets, "www", dist)
        check(File(dist, "index.html").isFile) { "The web UI is missing from this APK (assets/www)." }
        stamp.writeText(want)
        return root
    }

    private fun copyDir(am: AssetManager, path: String, dest: File) {
        val children = am.list(path).orEmpty()
        if (children.isEmpty()) {            // a file (or an empty directory)
            dest.parentFile?.mkdirs()
            runCatching { am.open(path) }.getOrNull()?.use { input ->
                dest.outputStream().use { input.copyTo(it) }
            }
            return
        }
        dest.mkdirs()
        for (child in children) copyDir(am, "$path/$child", File(dest, child))
    }
}
