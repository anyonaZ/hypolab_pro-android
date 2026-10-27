# HypoLab for Android

HypoLab is the Android tablet edition of the ACE Studio research workspace for WorldQuant BRAIN
consultants: data discovery, hypotheses, FastExpr generation, templates, BRAIN simulation,
evolution, results review and portfolio correlation. It is built for the **Samsung Galaxy Tab A9
on Android 16** and runs on any 64-bit ARM Android 8+ device.

Everything runs **on the tablet**. The complete Python research engine (the same FastAPI backend as
the desktop app) is embedded in the APK and serves the same React workspace inside the app. There
is no server to host and no computer to keep on.

## Install on the tablet

1. On the tablet, open this repository's **Releases** page (signed in to GitHub) and download the
   latest `HypoLab-<version>-arm64.apk`.
2. Open the downloaded file. If Android asks, allow your browser (or My Files) to *install unknown
   apps*, then tap **Install**.
3. Open **HypoLab**. The first launch takes a little longer while Python and the workspace are
   prepared, and later launches are faster.
4. When asked, allow notifications and **Allow background running**. The engine shows a quiet
   notification while it runs, so BRAIN simulations and syncs continue with the screen off or
   while you are in another app.
5. Sign in to BRAIN. **Begin login** opens BRAIN's biometrics page in the browser. Complete it,
   come back to HypoLab, and tap **Finish login**.

**Updating:** install a newer APK over the old one. All research data, your BRAIN session, saved
credentials and API keys are kept, because every release is signed with the same key.

## Bring your desktop research over

Your laptop's research history (simulation results, catalog, prompts, templates, hypotheses, and so
on) lives in `data/ace_studio_v2.db` in the ACE Studio folder.

1. Copy that file to the tablet (USB, Google Drive, Quick Share…).
2. In HypoLab open **Settings → Local data management → Restore database** and pick the file.

The app checks the file is a real ACE Studio/HypoLab database, keeps the tablet's previous database
as `backups/before-restore.db`, and upgrades the schema if needed. **Backup database** writes a copy
to `Downloads/HypoLab` at any time.

BRAIN sessions and AI keys are not inside the database. Log in and add your keys once on the
tablet.

## What is different from the desktop app

| Area | Desktop (ACE Studio) | Android (HypoLab) |
|---|---|---|
| Engine | Python + uvicorn on your PC | Same backend, embedded (CPython 3.13 via Chaquopy) |
| Storage | `data/` and `~/secrets/` | Private app storage (not readable by other apps, not cloud-backed-up) |
| BRAIN biometrics | Opens a browser tab | Opens a Chrome Custom Tab; return to the app and tap Finish login |
| Exports / backups | Browser download | Saved to `Downloads/HypoLab` |
| Updates | Signed in-place `.exe` swap | Newer APK from GitHub Releases, installed over the old one |
| Layout | Fits 1240×720 | Same layout, auto-fitted to the tablet, landscape, full-screen. **Settings → Appearance → Interface scale** to enlarge |
| Background work | Runs while the terminal is open | Foreground service; a wake lock is held only while jobs run; **Quit HypoLab** from the notification |
| Library versions | pandas 3 / numpy 2 | pandas 2.1.3 / numpy 1.26.2 (latest Android builds). The backend test suite passes on both |

The local API on `127.0.0.1` is protected by a per-launch token, so other apps on the tablet cannot
use your BRAIN session or keys.

## How it is built

```
backend/     HypoLab backend (FastAPI app + vendored BRAIN engine) — shared with desktop
frontend/    React + Vite workspace
android/     Android app: Kotlin WebView shell + foreground service + Chaquopy Python
  app/src/main/python/hypolab_android/server.py   starts the backend on 127.0.0.1
  app/requirements-android.txt                    Python packages bundled in the APK
  keystore/                                       release signing key (keep this repo private)
.github/workflows/android.yml                     CI: wheels → tests → APK → emulator → release
docs/ace-studio/                                  original ACE Studio developer documentation
```

Every push to `main` triggers GitHub Actions to:

1. compile **pydantic-core** and **jiter** for Android with cibuildwheel. They are Rust packages
   with no Android builds on PyPI. numpy, pandas and cryptography come from Chaquopy's repository.
2. run the backend tests with the same library versions the APK ships.
3. build the web UI, then the arm64 tablet APK and an x86_64 APK for testing.
4. boot the x86_64 APK on an **Android 16 emulator** and check the engine starts with all 20
   modules, the API rejects requests without the app token, and the UI renders.
5. publish `HypoLab-<version>-arm64.apk` as a GitHub Release.

Run logs and emulator screenshots are pushed to the `ci-logs` branch.

### Building locally

Requirements: Android Studio (or the Android SDK), JDK 17, Node 22, Python 3.13.

```bash
cd frontend && npm ci && npm run build && cd ..
# Put Android wheels for pydantic-core 2.46.5 and jiter 0.17.0 in android/wheels/
# (download the wheels-* artifacts from a CI run, or build them with cibuildwheel)
cd android && ./gradlew assembleRelease            # -PhypolabAbis=x86_64 for an emulator
```

### Backend tests

```bash
cd backend
pip install -r ../android/app/requirements-android.txt pytest   # on Python 3.12
python -m pytest tests -q
```

Tests that need BRAIN's operator reference are skipped on a clean machine. Point `ACE_DATA_DIR`
at a data folder from a logged-in install to run them too.

## Notes

- HypoLab is a research assistant. Internal gates do not guarantee that BRAIN accepts an alpha.
- Keep this repository **private**. It contains the ACE Studio source and the APK signing key.
