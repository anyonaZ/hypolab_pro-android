"""Test setup: run against a throwaway data directory unless ACE_DATA_DIR is already set.

Some tests (evolution previews, template vectors) need BRAIN's operator reference, which the app
caches in the local database after you log in. On a clean machine/CI there is no session and no
cached reference, so those tests are reported as skipped rather than failed. To run them, point
ACE_DATA_DIR at a data folder whose database already holds the operator reference.
"""

from __future__ import annotations

import os
import tempfile

import pytest

if not os.environ.get("ACE_DATA_DIR"):
    _tmp = tempfile.mkdtemp(prefix="hypolab-tests-")
    os.environ["ACE_DATA_DIR"] = os.path.join(_tmp, "data")
    os.environ.setdefault("HOME", os.path.join(_tmp, "home"))

from app.db.base import init_db  # noqa: E402

init_db()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when == "call" and call.excinfo is not None and call.excinfo.typename == "SessionExpired":
        report.outcome = "skipped"
        report.longrepr = (str(item.fspath), item.location[1],
                           "Skipped: needs the BRAIN operator reference (cached after login)")
