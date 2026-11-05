import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from noxai.jobs.executor import execute_planned_job
from noxai.jobs.planner import approve_job_plan, generate_plan_for_job
from noxai.jobs.watchdog import repair_orphaned_runs


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    """Set up isolated user environment with HOME at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    db_path = tmp_path / "nox_e2e.db"
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_path))

    # Initialize sqlite DB with base tables + migration 012
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instruction TEXT NOT NULL,
                schedule_type TEXT NOT NULL DEFAULT 'daily',
                schedule_expr TEXT NOT NULL DEFAULT '20:00',
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT,
                last_run_at TEXT,
                next_run_at TEXT,
                version INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                lane TEXT NOT NULL DEFAULT 'local',
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT,
                approval_id TEXT,
                error TEXT,
                result_json TEXT,
                cancellation_token TEXT,
                run_id TEXT,
                parent_task_id TEXT,
                subtask_ids_json TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 0,
                heartbeat_at TEXT
            );
            CREATE TABLE IF NOT EXISTS briefing_runs (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                slot TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                trigger TEXT NOT NULL DEFAULT 'scheduled',
                rev INTEGER NOT NULL DEFAULT 1,
                title TEXT,
                summary TEXT,
                markdown_path TEXT,
                generation_seconds REAL,
                sources_count INTEGER,
                task_id TEXT,
                feedback INTEGER,
                feedback_note TEXT,
                feedback_at TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at TEXT,
                status TEXT NOT NULL,
                task_run_id TEXT,
                output_markdown TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_run_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                markdown TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                feedback INTEGER,
                feedback_note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        mig_path = (
            Path(__file__).parent.parent
            / "src/noxai/migrations/012_planned_jobs_and_delete_controls.sql"
        )
        conn.executescript(mig_path.read_text())

    # Create user folders under ~/Documents
    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    folders = ["Certificates", "Study", "Obsidian Vault"]
    folder_paths = {}
    bare_remotes = {}

    monkeypatch.setenv("NOX_TEST_FAST_BACKOFF", "1")

    for f in folders:
        bare_repo = tmp_path / f"{f}.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True
        )
        # Also ensure HEAD points to main
        subprocess.run(
            ["git", "--git-dir", str(bare_repo), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
            capture_output=True,
        )
        bare_remotes[f] = str(bare_repo)

        work_dir = docs_dir / f
        work_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", "main", str(work_dir)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(work_dir), "config", "user.name", "NoX Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work_dir), "config", "user.email", "test@nox.local"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work_dir), "remote", "add", "origin", str(bare_repo)],
            check=True,
            capture_output=True,
        )

        readme = work_dir / "README.md"
        readme.write_text(f"Initial {f}")
        subprocess.run(["git", "-C", str(work_dir), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(work_dir), "commit", "-m", "init"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(work_dir), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
        )

        folder_paths[f] = f"~/Documents/{f}" if " " not in f else f'~/Documents/"{f}"'

    return {
        "db_path": str(db_path),
        "docs_dir": str(docs_dir),
        "folder_paths": folder_paths,
        "bare_remotes": bare_remotes,
    }


@pytest.mark.asyncio
async def test_full_planned_backup_execution_and_step_persistence(e2e_env):
    """VERIFIED-LIVE: Plan generation, approval, execution, step logging, and git push."""
    db_path = e2e_env["db_path"]
    fpaths = e2e_env["folder_paths"]

    job_id = "job_backup_e2e"
    instruction = f"Push folders to GitHub: {fpaths['Certificates']}, {fpaths['Study']}, {fpaths['Obsidian Vault']}"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, name, schedule_type, schedule_expr, timezone, instruction, enabled)
            VALUES (?, ?, 'cron', '0 20 * * *', 'Asia/Kolkata', ?, 1)
            """,
            (job_id, "Daily Backup E2E", instruction),
        )

    # 1. Generate plan using force_template=True
    plan = await generate_plan_for_job(job_id, instruction, force_template=True)
    assert plan.version == 1
    assert len(plan.steps) == 31  # 3 folders x 10 steps + 1 notification

    # 2. Approve plan
    approved = await approve_job_plan(job_id, version=1)
    assert approved.status == "approved"

    # 3. Add file to each folder so git has changes
    for f in ["Certificates", "Study", "Obsidian Vault"]:
        p = Path(e2e_env["docs_dir"]) / f / "note.txt"
        p.write_text(f"Hello from {f} at {datetime.now()}")

    # 4. Execute the planned job
    run_id = "run_backup_e2e_1"
    status = await execute_planned_job(job_id, run_id)
    assert status in ("ok", "succeeded")

    # 5. Verify database records
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        run_row = conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        assert run_row["status"] in ("ok", "succeeded")
        assert run_row["heartbeat_at"] is not None

        steps = conn.execute(
            "SELECT * FROM job_run_steps WHERE run_id = ? ORDER BY step_index ASC", (run_id,)
        ).fetchall()
        assert len(steps) == 31
        for s in steps:
            assert s["state"] == "ok"
            assert s["finished_at"] is not None

        notif = conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert notif is not None
        assert notif["level"] in ("info", "normal")
        assert "SUCCEEDED" in notif["message"]
        assert "Certificates" in notif["message"]
        assert "Study" in notif["message"]

    # 6. Verify bare remote git logs (push reached destination)
    for f_name, bare_repo in e2e_env["bare_remotes"].items():
        res = subprocess.run(
            ["git", "--git-dir", bare_repo, "log", "-n", "1", "--oneline", "main"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "NoX backup" in res.stdout


@pytest.mark.asyncio
async def test_secret_scan_excludes_sensitive_file_from_git_push(e2e_env):
    """VERIFIED-LIVE: Staging an AWS key causes scan_secrets to add it to .git/info/exclude."""
    db_path = e2e_env["db_path"]
    fpaths = e2e_env["folder_paths"]

    job_id = "job_secret_leak_test"
    instruction = f"Push folders to GitHub: {fpaths['Study']}"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, name, schedule_type, schedule_expr, timezone, instruction, enabled)
            VALUES (?, ?, 'cron', '0 20 * * *', 'Asia/Kolkata', ?, 1)
            """,
            (job_id, "Secret Leak Test", instruction),
        )

    await generate_plan_for_job(job_id, instruction, force_template=True)
    await approve_job_plan(job_id, version=1)

    # Place an AWS secret key file into Study
    study_path = Path(e2e_env["docs_dir"]) / "Study"
    secret_file = study_path / "credentials.txt"
    secret_file.write_text("AWS_KEY=AKIAIOSFODNN7EXAMPLE\n")

    # Also place a safe file
    safe_file = study_path / "safe.txt"
    safe_file.write_text("Safe content\n")

    run_id = "run_secret_leak_1"
    status = await execute_planned_job(job_id, run_id)
    assert status in ("ok", "succeeded")

    # Verify credentials.txt was added to .git/info/exclude
    exclude_file = study_path / ".git" / "info" / "exclude"
    assert exclude_file.exists()
    exclude_content = exclude_file.read_text()
    assert "credentials.txt" in exclude_content

    # Verify bare remote git repo has safe.txt but NEVER credentials.txt
    bare_study = e2e_env["bare_remotes"]["Study"]
    tree_res = subprocess.run(
        ["git", "--git-dir", bare_study, "ls-tree", "-r", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "safe.txt" in tree_res.stdout
    assert "credentials.txt" not in tree_res.stdout


@pytest.mark.asyncio
async def test_preflight_failure_stops_execution(e2e_env):
    """VERIFIED-LIVE: An invalid git remote causes preflight to fail and aborts folder push."""
    db_path = e2e_env["db_path"]
    fpaths = e2e_env["folder_paths"]

    # Break remote on Study folder
    study_path = Path(e2e_env["docs_dir"]) / "Study"
    subprocess.run(
        [
            "git",
            "-C",
            str(study_path),
            "remote",
            "set-url",
            "origin",
            "file:///nonexistent/repo.git",
        ],
        check=True,
    )

    job_id = "job_broken_remote_test"
    instruction = f"Push folders to GitHub: {fpaths['Study']}"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, name, schedule_type, schedule_expr, timezone, instruction, enabled)
            VALUES (?, ?, 'cron', '0 20 * * *', 'Asia/Kolkata', ?, 1)
            """,
            (job_id, "Broken Remote Test", instruction),
        )

    await generate_plan_for_job(job_id, instruction, force_template=True)
    await approve_job_plan(job_id, version=1)

    run_id = "run_broken_remote_1"
    status = await execute_planned_job(job_id, run_id)
    assert status == "failed"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        steps = conn.execute(
            "SELECT * FROM job_run_steps WHERE run_id = ? ORDER BY step_index ASC", (run_id,)
        ).fetchall()

        # Step 1: preflight fails
        assert steps[0]["state"] == "failed"

        # Subsequent steps skipped
        assert steps[1]["state"] == "skipped"


@pytest.mark.asyncio
async def test_watchdog_reconciliation_of_orphaned_runs(e2e_env):
    """VERIFIED-LIVE: Watchdog cleans up runs with stale heartbeats (>3 min)."""
    db_path = e2e_env["db_path"]
    job_id = "job_stale_heartbeat"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, name, schedule_type, schedule_expr, timezone, instruction, enabled)
            VALUES (?, ?, 'cron', '0 20 * * *', 'Asia/Kolkata', 'echo test', 1)
            """,
            (
                job_id,
                "Stale Job",
            ),
        )

        stale_time = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO job_runs (id, job_id, status, started_at, heartbeat_at, current_step_index)
            VALUES ('run_stale_1', ?, 'running', ?, ?, 2)
            """,
            (job_id, stale_time, stale_time),
        )

    repaired = await repair_orphaned_runs()
    assert any(r["run_id"] == "run_stale_1" for r in repaired)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM job_runs WHERE id = 'run_stale_1'").fetchone()
        assert r["status"] == "stalled"
        assert "heartbeat timed out" in r["error"]
