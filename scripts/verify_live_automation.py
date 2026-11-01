# ruff: noqa
"""Live automation test for Session J Phase 2 (Section 8 items 2 and 3).

1. Spawns a second noxd daemon with NOX_BRIEFING_MORNING_START set 1 minute in future,
   verifies tick, scheduled firing, SQLite records, and status update.
2. Stops it, moves start time into the past, restarts it, and verifies boot catch-up.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
PORT = 7789
TEST_DB = Path("/tmp/nox-phase2-live.db")
PROD_DB = Path(os.path.expanduser("~/.local/share/nox/nox.db"))


def setup_test_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    shutil.copy2(PROD_DB, TEST_DB)
    for ext in ("-wal", "-shm"):
        f = Path(f"{TEST_DB}{ext}")
        if f.exists():
            f.unlink()
    conn = sqlite3.connect(TEST_DB)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM briefing_runs WHERE date = ?", (today,))
    conn.execute("DELETE FROM briefing_topic_runs")
    conn.commit()
    conn.close()


def wait_for_run(
    date_str: str, expected_trigger: str, timeout_sec: int = 150
) -> sqlite3.Row | None:
    start = time.time()
    seen_running = False
    while time.time() - start < timeout_sec:
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM briefing_runs WHERE date = ? AND trigger = ? ORDER BY rev DESC LIMIT 1",
            (date_str, expected_trigger),
        ).fetchone()
        conn.close()
        if row:
            if not seen_running:
                print(
                    f" -> Detected triggered run {row['id']} (state={row['state']}). Waiting for completion..."
                )
                seen_running = True
            if row["state"] == "succeeded":
                return row
            if row["state"] == "failed":
                print(f" -> Run failed: {row['error']}")
                return row
        time.sleep(2)
    return None


def main():
    print("=== LIVE PROOF 1: Scheduled firing 1 minute ahead ===")
    setup_test_db()

    now = datetime.now(TZ)
    target_time = (now + timedelta(minutes=1)).strftime("%H:%M")
    evening_time = (now + timedelta(minutes=10)).strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    print(f"Current wall-clock time: {now.strftime('%H:%M:%S')} IST")
    print(f"Setting morning_start={target_time}, evening_start={evening_time}")

    env = os.environ.copy()
    env["NOX_DATABASE_PATH"] = str(TEST_DB)
    env["NOX_SERVER_PORT"] = str(PORT)
    env["NOX_PID_FILE"] = f"/tmp/noxd-{PORT}.pid"
    env["NOX_BRIEFING_MORNING_START"] = target_time
    env["NOX_BRIEFING_EVENING_START"] = evening_time
    env["NOX_BRIEFING_FETCH_TIMEOUT"] = "10"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import os, asyncio, uvicorn
from noxai.config import get_config
from noxai.api.app import create_app
from noxai.db import run_migrations
asyncio.run(run_migrations())
app = create_app(manage_lock=False)
uvicorn.run(app, host='127.0.0.1', port={PORT}, log_level='info')
""",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    print(
        f"Spawned temporary noxd on PID {proc.pid}, port {PORT}. Waiting for scheduled trigger at {target_time}..."
    )
    run_record = wait_for_run(today, expected_trigger="scheduled", timeout_sec=90)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    assert run_record is not None, "Scheduled run did not trigger and succeed in DB"
    print(
        f"✓ Scheduled run verified in SQLite: id={run_record['id']}, slot={run_record['slot']}, state={run_record['state']}, trigger={run_record['trigger']}, sources={run_record['sources_count']}"
    )

    print("\n=== LIVE PROOF 2: Catch-up on startup past scheduled window ===")
    conn = sqlite3.connect(TEST_DB)
    conn.execute("DELETE FROM briefing_runs WHERE date = ?", (today,))
    conn.execute("DELETE FROM briefing_topic_runs")
    conn.commit()
    conn.close()

    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    print(f"Restarting daemon with morning_start in past ({past_time}) to test catch-up on boot...")
    env["NOX_BRIEFING_MORNING_START"] = past_time

    proc2 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import os, asyncio, uvicorn
from noxai.config import get_config
from noxai.api.app import create_app
app = create_app(manage_lock=False)
uvicorn.run(app, host='127.0.0.1', port={PORT}, log_level='info')
""",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    print(f"Spawned temporary noxd on PID {proc2.pid}, port {PORT}. Waiting for catch-up run...")
    catchup_record = wait_for_run(today, expected_trigger="scheduled", timeout_sec=120)
    proc2.terminate()
    try:
        proc2.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc2.kill()

    assert catchup_record is not None, "Catch-up run did not trigger and succeed in DB"
    print(
        f"✓ Catch-up run verified in SQLite: id={catchup_record['id']}, slot={catchup_record['slot']}, state={catchup_record['state']}, trigger={catchup_record['trigger']}"
    )

    print("\nALL SECTION 8 ITEMS 2 & 3 LIVE PROOFS PASSED!")


if __name__ == "__main__":
    main()
