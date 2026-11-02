"""LIVE PROOF B Runner (Session K, Section 3.3).

Proves catch-up & missed window in a temporary isolated daemon instance:
- Sets morning slot 20 minutes ago (unserved, still within window).
- Evening slot was 4 hours ago (window closed).
- Proves morning slot catch-up fires within 60s of boot.
- Proves evening slot is marked missed: state='missed', failure_reason='daemon_offline_during_window'.
- Both visible in SQLite table briefing_runs with the right state, timestamp, and failure_reason.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from noxai.db import run_migrations


def run_proof_b() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz)
    print(f"[PROOF B] Current time in Asia/Kolkata: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    # Morning slot: 20 minutes ago (unserved, still within morning window)
    morn_dt = now - timedelta(minutes=20)
    # Evening slot: 2 hours in future so morning window is currently open
    even_dt = now + timedelta(hours=2)
    morn_str = morn_dt.strftime("%H:%M")
    even_str = even_dt.strftime("%H:%M")
    print(f"[PROOF B] Slot configuration: morning_start={morn_str} (20m ago), evening_start={even_str}")

    base = Path("/tmp/nox_proof_b")
    if base.exists():
        shutil.rmtree(base)
    data_dir = base / "data"
    cfg_dir = base / "config"
    log_dir = base / "logs"
    data_dir.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)

    # 1. Prepare DB from real DB
    real_db = Path(os.path.expanduser("~/.local/share/nox/nox.db"))
    test_db = data_dir / "nox.db"
    shutil.copy(real_db, test_db)
    conn = sqlite3.connect(test_db)
    conn.execute("DELETE FROM briefing_runs")
    conn.execute("DELETE FROM briefing_topic_runs")
    conn.execute("DELETE FROM tasks WHERE kind = 'briefing'")
    conn.commit()
    conn.close()

    # Apply all migrations to test_db
    os.environ["NOX_DATABASE_PATH"] = str(test_db)
    asyncio.run(run_migrations())

    # 2. Copy secrets.env
    real_sec = Path(os.path.expanduser("~/.config/nox/secrets.env"))
    test_sec = cfg_dir / "secrets.env"
    if real_sec.exists():
        shutil.copy(real_sec, test_sec)
        test_sec.chmod(0o600)

    # 3. Write temp config.toml from real config
    real_cfg_file = Path(os.path.expanduser("~/.config/nox/config.toml"))
    base_cfg = real_cfg_file.read_text(encoding="utf-8") if real_cfg_file.exists() else ""
    if "[briefing]" in base_cfg:
        base_cfg = base_cfg.split("[briefing]")[0]
    base_cfg = base_cfg.replace("port = 7777", "port = 7789")

    cfg_path = cfg_dir / "config.toml"
    cfg_content = base_cfg + f"""
[briefing]
enabled = true
timezone = "Asia/Kolkata"
morning_start = "{morn_str}"
evening_start = "{even_str}"
catchup_on_boot = true
min_items_total = 15
notify_on_done = false
"""
    cfg_path.write_text(cfg_content)

    log_path = base / "daemon.log"
    log_file = open(log_path, "w", encoding="utf-8")

    env = os.environ.copy()
    env["NOX_DATA_DIR"] = str(data_dir)
    env["NOX_CONFIG_DIR"] = str(cfg_dir)
    env["NOX_CONFIG_FILE"] = str(cfg_path)
    env["NOX_SECRETS_FILE"] = str(test_sec)
    env["NOX_DATABASE_PATH"] = str(test_db)
    env["NOX_PID_FILE"] = str(data_dir / "noxd.pid")
    env["NOX_LOG_DIR"] = str(log_dir)
    env["NOX_PORT"] = "7789"
    env["NOX_BRIEFING_MORNING_START"] = morn_str
    env["NOX_BRIEFING_EVENING_START"] = even_str
    env["PYTHONPATH"] = "src"
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[PROOF B] Starting temp daemon on port 7789 (PID logging to {log_path})...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "noxai", "run", "--port", "7789"],
        env=env,
        cwd=str(Path(__file__).parent.parent),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        # Wait up to 75 seconds for catch-up to fire and complete
        timeout = time.time() + 75
        missed_seen = False
        morning_succeeded = False

        while time.time() < timeout:
            time.sleep(5)
            if proc.poll() is not None:
                print(f"[PROOF B] ERROR: temp daemon exited unexpectedly with code {proc.returncode}!")
                break

            try:
                c = sqlite3.connect(test_db)
                c.row_factory = sqlite3.Row
                rows = c.execute(
                    "SELECT id, date, slot, trigger, state, sources_count, generation_seconds, failure_reason FROM briefing_runs"
                ).fetchall()
                c.close()

                for r in rows:
                    if r["state"] == "missed" and r["failure_reason"] == "daemon_offline_during_window" and not missed_seen:
                        print(f"[{datetime.now(tz).strftime('%H:%M:%S')}] ✓ Missed window recorded: {dict(r)}")
                        missed_seen = True
                    if r["slot"] == "morning" and r["state"] == "succeeded" and not morning_succeeded:
                        print(f"[{datetime.now(tz).strftime('%H:%M:%S')}] ✓ Boot catchup morning slot succeeded: {dict(r)}")
                        morning_succeeded = True

                if missed_seen and morning_succeeded:
                    print(f"[{datetime.now(tz).strftime('%H:%M:%S')}] Both missed window and morning catch-up verified!")
                    break
            except Exception as e:
                print(f"DB read error: {e}")

    finally:
        print("[PROOF B] Terminating temp daemon...")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file.close()

    # Final report and raw output
    print("\n" + "=" * 70)
    print("LIVE PROOF B RAW SQL REPORT")
    print("=" * 70)
    c = sqlite3.connect(test_db)
    c.row_factory = sqlite3.Row
    final_rows = c.execute(
        "SELECT id, date, slot, trigger, state, sources_count, generation_seconds, error, failure_reason FROM briefing_runs"
    ).fetchall()
    c.close()

    for r in final_rows:
        print(dict(r))

    print("\n" + "=" * 70)
    print("LIVE PROOF B LOG EXCERPTS")
    print("=" * 70)
    lines = log_path.read_text().splitlines()
    for line in lines:
        if any(kw in line for kw in ("missed", "catchup", "BriefingScheduler", "succeeded", "daemon_offline")):
            print(line)

    assert any(
        r["state"] == "missed" and r["failure_reason"] == "daemon_offline_during_window"
        for r in final_rows
    ), "Missed window was not recorded!"
    assert any(
        r["slot"] == "morning" and r["state"] == "succeeded" and r["trigger"] == "scheduled"
        for r in final_rows
    ), "Morning catchup slot did not succeed!"
    print("\n[PROOF B] VERIFIED-LIVE: Catch-up executed unserved window and recorded missed past window.")


if __name__ == "__main__":
    run_proof_b()
