#!/usr/bin/env bash
# Boot-test the x86_64 HypoLab APK on an Android emulator:
#   install → launch → wait for the embedded engine → check every backend module mounted,
#   check the API refuses requests without the app's token → screenshot the UI → collect logcat.
# Usage: emulator-smoke.sh <apk> <report-dir>
set -uo pipefail
APK="$1"; OUT="$2"; PKG="com.hypolab.app"
mkdir -p "$OUT"
status=0
log() { echo "[smoke] $*" | tee -a "$OUT/smoke.txt"; }

adb wait-for-device
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0
log "device: $(adb shell getprop ro.build.version.release) / sdk $(adb shell getprop ro.build.version.sdk) / $(adb shell getprop ro.product.cpu.abi)"
log "apk: $(ls -la "$APK")"

adb logcat -c || true
if ! adb install -r -g "$APK" > "$OUT/install.txt" 2>&1; then
  log "INSTALL FAILED"; cat "$OUT/install.txt" | tee -a "$OUT/smoke.txt"; exit 1
fi
log "installed"

t0=$(date +%s)
adb shell am start -W -n "$PKG/.MainActivity" | tee -a "$OUT/smoke.txt"
adb forward tcp:8766 tcp:8766

ready=0
for i in $(seq 1 240); do
  if curl -sf -m 3 http://127.0.0.1:8766/api -o "$OUT/api.json"; then ready=1; break; fi
  sleep 1
done
t1=$(date +%s)
if [ "$ready" != 1 ]; then
  log "ENGINE DID NOT START within 240s"
  status=1
else
  log "engine answered after $((t1 - t0))s: $(cat "$OUT/api.json")"
  n=$(python3 -c "import json;print(len(json.load(open('$OUT/api.json'))['modules']))")
  log "modules mounted: $n"
  if [ "$n" -lt 20 ]; then log "EXPECTED 20 MODULES"; status=1; fi
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/api/settings/keys)
  log "token gate (no token) -> HTTP $code"
  [ "$code" = "403" ] || { log "TOKEN GATE NOT ENFORCED"; status=1; }
  curl -s http://127.0.0.1:8766/api/health/ping | tee -a "$OUT/smoke.txt"; echo | tee -a "$OUT/smoke.txt"
fi

sleep 12   # let the WebView render the workspace
adb exec-out screencap -p > "$OUT/screen-home.png"
adb shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity" | head -3 | tee -a "$OUT/smoke.txt"

# Rotate the emulator to landscape (the app is landscape-only) in case the device is portrait.
adb shell settings put system accelerometer_rotation 0
adb shell settings put system user_rotation 1
sleep 4
adb exec-out screencap -p > "$OUT/screen-landscape.png"

adb shell dumpsys meminfo "$PKG" | head -30 > "$OUT/meminfo.txt" 2>&1 || true
adb shell pidof "$PKG" | tee -a "$OUT/smoke.txt"
adb logcat -d > "$OUT/logcat-full.txt" 2>&1 || true
grep -E "HypoLab|python\.std|chaquopy|AndroidRuntime|FATAL|HypoLabWeb" "$OUT/logcat-full.txt" | tail -n 600 > "$OUT/logcat-hypolab.txt" || true
if grep -q "FATAL EXCEPTION" "$OUT/logcat-full.txt"; then log "FATAL EXCEPTION in logcat"; status=1; fi
# keep the committed report small
tail -n 3000 "$OUT/logcat-full.txt" > "$OUT/logcat-tail.txt"; rm -f "$OUT/logcat-full.txt"

log "smoke status: $status"
exit $status
