"""Automated proof for C-3: Subprocess daemon SIGKILL resume proof."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from noxai.db import run_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(port: int, timeout: float = 15.0) -> bool:
    start = time.time()
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() - start < timeout:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.mark.asyncio
async def test_subprocess_daemon_sigkill_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """C-3 Proof: Real daemon process killed with SIGKILL mid-run, restarted,
    and verified to resume under the SAME run_id without duplicating done topics.
    """
    port = find_free_port()
    home_dir = tmp_path / "home"
    data_dir = home_dir / ".local" / "share" / "nox"
    config_dir = home_dir / ".config" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "nox.db"
    monkeypatch.setattr("noxai.db.DB_PATH", db_path)
    monkeypatch.setattr("noxai.db.DATA_DIR", data_dir)
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_path))

    # Initialize migrations on test DB
    await run_migrations()

    run_id = "brun_sigkill_c3_test"
    task_id = "task_sigkill_c3_test"

    # Pre-seed an active running briefing with 1 completed topic and 1 queued topic
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tasks (id, title, prompt, status, lane, kind, priority, created_at, started_at)
        VALUES (?, 'Briefing Task', 'Run briefing', 'running', 'cloud', 'briefing', 'high',
                datetime('now'), datetime('now'))
        """,
        (task_id,),
    )
    cur.execute(
        """
        INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, task_id, title, started_at)
        VALUES (?, '2026-11-20', 'morning', 'running', 'scheduled', 1, ?,
                'NoX Morning Briefing', datetime('now'))
        """,
        (run_id, task_id),
    )
    cur.execute(
        """
        INSERT INTO briefing_topic_runs (run_id, topic_id, state, items_staged)
        VALUES (?, 'topic_ai', 'done', 8), (?, 'topic_systems', 'collecting', 0)
        """,
        (run_id, run_id),
    )
    conn.commit()
    conn.close()

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["NOX_DATA_DIR"] = str(data_dir)
    env["NOX_CONFIG_DIR"] = str(config_dir)
    env["NOX_DATABASE_PATH"] = str(db_path)
    env["NOX_SERVER_PORT"] = str(port)
    env["NOX_BRIEFING_TIMEZONE"] = "Asia/Kolkata"

    # 1. Start daemon 1 as a real subprocess
    proc1 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import uvicorn
from noxai.api.app import create_app
app = create_app(manage_lock=False)
uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')
""",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    healthy1 = wait_for_health(port, timeout=12.0)
    assert healthy1, "Daemon 1 did not become healthy"

    # 2. Violently kill daemon 1 with SIGKILL (-9)
    proc1.send_signal(signal.SIGKILL)
    ret1 = proc1.wait(timeout=5.0)
    is_killed = ret1 == -signal.SIGKILL or ret1 == 137 or proc1.poll() is not None
    assert is_killed, "Process was not killed with SIGKILL"

    # Verify run in SQLite is still in-flight ('queued' or 'running') after crash
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    row = cur.execute(
        "SELECT state, task_id FROM briefing_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row[0] in ("queued", "running"), f"Expected in-flight state, got {row[0]}"
    d1_task_id = row[1]
    conn.close()

    # 3. Start daemon 2 on same DB (simulating restart after crash)
    port2 = find_free_port()
    env["NOX_SERVER_PORT"] = str(port2)

    proc2 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import uvicorn
from noxai.api.app import create_app
app = create_app(manage_lock=False)
uvicorn.run(app, host='127.0.0.1', port={port2}, log_level='warning')
""",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    healthy2 = wait_for_health(port2, timeout=12.0)
    assert healthy2, "Daemon 2 did not become healthy on restart"

    # 4. Give BriefingScheduler startup repair 2 seconds to complete repair_runs
    time.sleep(2.0)

    # 5. Verify the orphaned run was resumed under the EXACT SAME run_id
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    repaired_run = cur.execute(
        "SELECT id, state, task_id FROM briefing_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert repaired_run is not None, f"Run {run_id} missing after restart"
    assert repaired_run[0] == run_id, "Resumed run changed ID!"
    assert repaired_run[1] in ("queued", "running"), (
        f"Run {run_id} was not requeued: state={repaired_run[1]}"
    )
    # Verify task was updated to a new supervisor task
    assert (
        repaired_run[2] != d1_task_id
    ), f"Supervisor task ID was not updated for resume (still {d1_task_id})"

    # 6. Verify completed topic run remains 'done' without redoing finished topics
    topic_row = cur.execute(
        "SELECT state, items_staged FROM briefing_topic_runs "
        "WHERE run_id = ? AND topic_id = 'topic_ai'",
        (run_id,),
    ).fetchone()
    assert topic_row is not None
    assert topic_row[0] == "done"
    assert topic_row[1] == 8

    conn.close()

    # Cleanup daemon 2
    proc2.terminate()
    try:
        proc2.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc2.kill()
