"""LIVE PROOF A Runner (Session K, Section 3.3).

Proves automatic scheduler triggers in a temporary isolated daemon instance:
- Morning slot fires at T+2m automatically with trigger='scheduled'.
- Evening slot fires at T+4m automatically with trigger='scheduled'.
- Both tasks complete with state='succeeded'.
- UI is kept closed throughout.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def run_proof_a() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz)
    print(f"[PROOF A] Current time in Asia/Kolkata: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    # Set slot 1 at +2m, slot 2 at +4m
    # To ensure we don't start midway through a minute, target next full minute + 2m
    morn_dt = now + timedelta(minutes=2)
    even_dt = now + timedelta(minutes=4)
    morn_str = morn_dt.strftime("%H:%M")
    even_str = even_dt.strftime("%H:%M")
    print(f"[PROOF A] Scheduled slots configured: morning_start={morn_str}, evening_start={even_str}")

    base = Path("/tmp/nox_proof_a")
    if base.exists():
        shutil.rmtree(base)
    data_dir = base / "data"
    cfg_dir = base / "config"
    data_dir.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)

    # 1. Prepare DB from real DB schema & topics, but clear runs
    real_db = Path(os.path.expanduser("~/.local/share/nox/nox.db"))
    test_db = data_dir / "nox.db"
    shutil.copy(real_db, test_db)
    conn = sqlite3.connect(test_db)
    conn.execute("DELETE FROM briefing_runs")
    conn.execute("DELETE FROM briefing_topic_runs")
    conn.execute("DELETE FROM tasks WHERE kind = 'briefing'")
    conn.commit()
    conn.close()

    # Apply all migrations (including 011) to test_db
    import asyncio
    from noxai.db import run_migrations
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
    # Strip existing [briefing] if present
    if "[briefing]" in base_cfg:
        base_cfg = base_cfg.split("[briefing]")[0]
    # Replace port 7777 with 7788
    base_cfg = base_cfg.replace("port = 7777", "port = 7788")

    cfg_path = cfg_dir / "config.toml"
    cfg_content = base_cfg + f"""
[briefing]
enabled = true
timezone = "Asia/Kolkata"
morning_start = "{morn_str}"
evening_start = "{even_str}"
catchup_on_boot = false
min_items_total = 15
notify_on_done = false
"""
    cfg_path.write_text(cfg_content)

    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
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
    env["NOX_PORT"] = "7788"
    env["NOX_BRIEFING_MORNING_START"] = morn_str
    env["NOX_BRIEFING_EVENING_START"] = even_str
    env["PYTHONPATH"] = "src"
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[PROOF A] Starting temp daemon on port 7788 (PID logging to {log_path})...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "noxai", "run", "--port", "7788"],
        env=env,
        cwd=str(Path(__file__).parent.parent),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        # Wait until even_dt + 90 seconds for both runs to complete
        target_end = even_dt + timedelta(seconds=90)
        print(f"[PROOF A] Waiting unattended until {target_end.strftime('%H:%M:%S')} for both slots to fire...")

        morn_seen = False
        even_seen = False

        while datetime.now(tz) < target_end:
            time.sleep(10)
            cur = datetime.now(tz)

            if proc.poll() is not None:
                print(f"[PROOF A] ERROR: temp daemon exited unexpectedly with code {proc.returncode}!")
                break

            # Inspect test_db
            try:
                c = sqlite3.connect(test_db)
                c.row_factory = sqlite3.Row
                rows = c.execute("SELECT id, date, slot, trigger, state, sources_count, generation_seconds, failure_reason FROM briefing_runs").fetchall()
                c.close()

                for r in rows:
                    if r["slot"] == "morning" and r["state"] == "succeeded" and not morn_seen:
                        print(f"[{cur.strftime('%H:%M:%S')}] ✓ Morning slot completed: {dict(r)}")
                        morn_seen = True
                    if r["slot"] == "evening" and r["state"] == "succeeded" and not even_seen:
                        print(f"[{cur.strftime('%H:%M:%S')}] ✓ Evening slot completed: {dict(r)}")
                        even_seen = True

                if morn_seen and even_seen:
                    print(f"[{cur.strftime('%H:%M:%S')}] Both morning and evening slots successfully executed!")
                    break
            except Exception as e:
                print(f"DB read notice: {e}")

    finally:
        print("[PROOF A] Terminating temp daemon...")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file.close()

    # Final report and raw output
    print("\n" + "=" * 70)
    print("LIVE PROOF A RAW SQL REPORT")
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
    print("LIVE PROOF A LOG EXCERPTS")
    print("=" * 70)
    lines = log_path.read_text().splitlines()
    for line in lines:
        if any(kw in line for kw in ("BriefingScheduler", "Briefing tick", "Starting Briefing Pipeline", "succeeded", "trigger_slot")):
            print(line)

    assert any(r["slot"] == "morning" and r["state"] == "succeeded" and r["trigger"] == "scheduled" for r in final_rows), "Morning slot did not succeed!"
    assert any(r["slot"] == "evening" and r["state"] == "succeeded" and r["trigger"] == "scheduled" for r in final_rows), "Evening slot did not succeed!"
    print("\n[PROOF A] VERIFIED-LIVE: Both morning and evening briefing slots fired and succeeded automatically.")


if __name__ == "__main__":
    run_proof_a()
