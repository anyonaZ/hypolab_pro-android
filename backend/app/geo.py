"""Geographic access gate — restrict the app to a single country (Kenya by default).

On launch the app asks a few free IP-geolocation services for the caller's country. If a country
is DETERMINED and it isn't the allowed one, the app refuses to start. If NONE of the services can
be reached — i.e. there is no working internet connection — it FAILS CLOSED and refuses to start
too (you can't verify the region offline, and the app needs the network for BRAIN anyway). Set
ACE_GEO_FAIL_OPEN=1 to allow running when the region can't be verified.

HONEST LIMITATION: this is client-side and easily bypassed (a VPN to the allowed country defeats
it, and the .exe can be patched). It deters casual sharing outside the country, not a determined
bypass. Set ACE_GEO_DISABLE=1 to turn the gate off entirely (e.g. for your own dev machine).
"""

from __future__ import annotations

import json
import os
import urllib.request

ALLOWED = os.environ.get("ACE_GEO_ALLOW", "KE").upper()
# Fail CLOSED by default: if the region can't be verified (no internet), the app does NOT start.
_FAIL_OPEN = os.environ.get("ACE_GEO_FAIL_OPEN", "0") != "0"
_TIMEOUT = float(os.environ.get("ACE_GEO_TIMEOUT", "5"))

# (url, how-to-read-the-response)
_PROVIDERS = [
    ("https://ipapi.co/country/", "text"),
    ("http://ip-api.com/json/?fields=status,countryCode", "json:countryCode"),
    ("https://ipwho.is/?fields=country_code", "json:country_code"),
]


def detect_country() -> str | None:
    """Best-effort 2-letter country code from IP, or None if nothing answered."""
    for url, how in _PROVIDERS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ace-studio"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                body = r.read().decode("utf-8", "replace").strip()
            if how == "text":
                cc = body[:2]
            else:
                cc = str(json.loads(body).get(how.split(":", 1)[1], ""))
            cc = cc.strip().upper()
            if len(cc) == 2 and cc.isalpha():
                return cc
        except Exception:
            continue
    return None


def enforce_region() -> None:
    """Exit the process if the detected country is not the allowed one."""
    if os.environ.get("ACE_GEO_DISABLE") == "1":
        return
    cc = detect_country()
    if cc is None:
        if _FAIL_OPEN:
            print("[geo] could not verify your location; continuing (ACE_GEO_FAIL_OPEN=1).")
            return
        print("\n  ACE Studio needs an internet connection to start — it verifies your region on launch.")
        print("  No connection could be reached, so the app will not start. Connect and try again.\n")
        raise SystemExit(4)
    if cc != ALLOWED:
        print(f"\n  ACE Studio is licensed for use in {ALLOWED} only.")
        print(f"  Your detected region is {cc}, so the app will not start.\n")
        raise SystemExit(4)
