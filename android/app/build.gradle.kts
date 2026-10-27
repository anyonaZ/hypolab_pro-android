import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// ── Versioning ─────────────────────────────────────────────────────────────────────────
// CI passes -PhypolabBuild=<run number>; local builds default to 0.
val buildNumber = (findProperty("hypolabBuild") as String?)?.toIntOrNull() ?: 0
val hypolabVersionName = "1.0.$buildNumber"
val hypolabVersionCode = 1000 + buildNumber

// ── ABIs ───────────────────────────────────────────────────────────────────────────────
// The Galaxy Tab A9 is arm64-v8a. CI also builds an x86_64 APK to boot-test on an emulator.
val abis = ((findProperty("hypolabAbis") as String?) ?: "arm64-v8a")
    .split(",").map { it.trim() }.filter { it.isNotEmpty() }

// ── Signing ────────────────────────────────────────────────────────────────────────────
// A stable key is required so new APKs install over old ones and keep your data.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore/keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.hypolab.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.hypolab.app"
        minSdk = 26
        targetSdk = 36
        versionCode = hypolabVersionCode
        versionName = hypolabVersionName
        ndk { abiFilters += abis }
        buildConfigField("String", "UPDATE_REPO", "\"anyonaZ/hypolab_pro-android\"")
    }

    signingConfigs {
        create("release") {
            val storePath = keystoreProps.getProperty("storeFile")
            if (storePath != null) {
                storeFile = rootProject.file("keystore/$storePath")
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystoreProps.getProperty("storeFile") != null)
                signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures { buildConfig = true }

    sourceSets {
        getByName("main") {
            // The built React UI (frontend/dist) is staged here by syncWebAssets.
            assets.srcDir(layout.buildDirectory.dir("generated/hypolab-web"))
        }
    }

    packaging {
        resources.excludes += setOf("META-INF/LICENSE*", "META-INF/NOTICE*")
    }

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // Chaquopy needs a Python 3.13 on the build machine; CI passes its path explicitly.
        System.getenv("HYPOLAB_BUILD_PYTHON")?.takeIf { it.isNotBlank() }?.let { buildPython(it) }
        pip {
            // pydantic-core and jiter are compiled for Android by CI (see .github/workflows).
            options("--find-links", rootProject.file("wheels").absolutePath)
            install("-r", "requirements-android.txt")
        }
        pyc {
            src = true
            pip = true
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            // The shared HypoLab backend (FastAPI app) and its vendored BRAIN engine, which the
            // backend imports as top-level modules (ace_lib, session_manager, …).
            srcDir("../../backend")
            srcDir("../../backend/vendor")
            exclude("tests/**", "vendor/**", "**/__pycache__/**", "requirements.txt")
        }
    }
}

// ── Web UI: copy the Vite build into the APK assets ────────────────────────────────────
val frontendDist = rootProject.file("../frontend/dist")
val syncWebAssets by tasks.registering(Sync::class) {
    description = "Copies frontend/dist into the APK assets (assets/www)."
    from(frontendDist)
    into(layout.buildDirectory.dir("generated/hypolab-web/www"))
    doFirst {
        if (!frontendDist.resolve("index.html").exists()) {
            throw GradleException(
                "frontend/dist is missing. Build the web UI first:  cd frontend && npm ci && npm run build"
            )
        }
    }
}
tasks.named("preBuild") { dependsOn(syncWebAssets) }

dependencies {
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.webkit:webkit:1.13.0")
    implementation("androidx.browser:browser:1.8.0")
}
