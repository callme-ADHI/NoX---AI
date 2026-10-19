"""Comprehensive tests for CLI commands and sub-apps."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from noxai.cli import app
from noxai.memory.vector import VectorIndex


class DummyVectorIndex:
    async def upsert(self, row_id: int, embedding: list[float]) -> None:
        pass

    async def query(self, embedding: list[float], k: int = 10) -> list[tuple[int, float]]:
        return [(1, 0.99)]

    async def delete(self, row_id: int) -> None:
        pass


def test_vector_index_protocol_runtime_checkable() -> None:
    """VectorIndex satisfies runtime_checkable protocol checks."""
    dummy = DummyVectorIndex()
    assert isinstance(dummy, VectorIndex)


def test_force_numpy_vectors_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting NOX_FORCE_NUMPY_VECTORS=1 forces numpy backend."""
    from noxai import db as db_mod

    monkeypatch.setenv("NOX_FORCE_NUMPY_VECTORS", "1")
    backend = db_mod._detect_vector_backend()
    assert backend == "numpy"


def test_test_mode_switch_requires_both_env_and_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify test-only switch requires BOTH NOX_TEST_MODE=1 and --test-db."""
    runner = CliRunner()
    fake_db = tmp_path / "fake.db"
    fake_db.touch()

    # Case 1: NOX_TEST_MODE=1 but no --test-db flag -> ignored
    monkeypatch.setenv("NOX_TEST_MODE", "1")
    with (
        patch("noxai.api.app.create_app") as mock_create_app,
        patch("uvicorn.run"),
        patch("noxai.lock.DaemonLock.acquire"),
        patch("noxai.lock.DaemonLock.release"),
    ):
        result = runner.invoke(app, ["run"])
        assert "Warning" in result.output or "--test-db flag was not provided" in result.output
        mock_create_app.assert_called_once_with(manage_lock=False, test_mode=False, test_db=None)

    # Case 2: --test-db passed but NOX_TEST_MODE not set -> ignored
    monkeypatch.delenv("NOX_TEST_MODE", raising=False)
    with (
        patch("noxai.api.app.create_app") as mock_create_app,
        patch("uvicorn.run"),
        patch("noxai.lock.DaemonLock.acquire"),
        patch("noxai.lock.DaemonLock.release"),
    ):
        result = runner.invoke(app, ["run", "--test-db", str(fake_db)])
        assert (
            "Warning" in result.output
            or "ignored because NOX_TEST_MODE is not set" in result.output
        )
        mock_create_app.assert_called_once_with(manage_lock=False, test_mode=False, test_db=None)

    # Case 3: BOTH NOX_TEST_MODE=1 and --test-db passed -> test mode activated!
    monkeypatch.setenv("NOX_TEST_MODE", "1")
    with (
        patch("noxai.api.app.create_app") as mock_create_app,
        patch("uvicorn.run"),
        patch("noxai.lock.DaemonLock.acquire"),
        patch("noxai.lock.DaemonLock.release"),
    ):
        runner.invoke(app, ["run", "--test-db", str(fake_db)])
        mock_create_app.assert_called_once_with(
            manage_lock=False, test_mode=True, test_db=str(fake_db)
        )


def test_status_command() -> None:
    runner = CliRunner()
    # 1. 200 OK
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    with patch("httpx.get", return_value=mock_resp):
        res = runner.invoke(app, ["status"])
        assert res.exit_code == 0
        assert "status" in res.output

    # 2. ConnectError
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        res = runner.invoke(app, ["status"])
        assert res.exit_code == 1
        assert "Cannot reach daemon" in res.output

    # 3. Generic error
    with patch("httpx.get", side_effect=RuntimeError("unknown error")):
        res = runner.invoke(app, ["status"])
        assert res.exit_code == 1
        assert "unknown error" in res.output


def test_token_command(tmp_path: Path) -> None:
    runner = CliRunner()
    token_file = tmp_path / "session_token"

    with patch("noxai.cli.TOKEN_PATH", token_file):
        # 1. File does not exist
        res = runner.invoke(app, ["token"])
        assert res.exit_code == 1
        assert "No token file" in res.output

        # 2. File exists
        token_file.write_text("12345678abcdefgh")
        res = runner.invoke(app, ["token"])
        assert res.exit_code == 0
        assert "1234...efgh" in res.output


def test_doctor_command() -> None:
    runner = CliRunner()
    with patch("noxai.doctor.run_doctor") as mock_run:
        res = runner.invoke(app, ["doctor"])
        assert res.exit_code == 0
        mock_run.assert_called_once()

    with patch("noxai.doctor.run_doctor") as mock_run:
        res = runner.invoke(app, ["doctor", "--json"])
        assert res.exit_code == 0


def test_open_command() -> None:
    runner = CliRunner()
    mock_cfg = SimpleNamespace(
        server=SimpleNamespace(host="127.0.0.1", port=7777),
        ui=SimpleNamespace(launcher_cmd=["chromium", "--app={url}"]),
    )
    with (
        patch("noxai.config.get_config", return_value=mock_cfg),
        patch("noxai.api.auth.load_session_token", return_value="tok123"),
        patch("subprocess.Popen") as mock_popen,
    ):
        res = runner.invoke(app, ["open"])
        assert res.exit_code == 0
        assert mock_popen.called

    with (
        patch("noxai.config.get_config", return_value=mock_cfg),
        patch("noxai.api.auth.load_session_token", return_value=None),
        patch("noxai.api.auth.generate_session_token", return_value="tok456"),
        patch("subprocess.Popen", side_effect=OSError("no chromium")),
        patch("webbrowser.open") as mock_wb,
    ):
        res = runner.invoke(app, ["open"])
        assert res.exit_code == 0
        assert mock_wb.called


def test_run_command_failures(tmp_path: Path) -> None:
    runner = CliRunner()
    # 1. Unsafe secrets file permissions
    sec_file = tmp_path / "secrets.env"
    sec_file.write_text("SECRET=1")
    sec_file.chmod(0o644)
    with patch("noxai.config.SECRETS_FILE", sec_file), patch("noxai.cli.SECRETS_FILE", sec_file):
        res = runner.invoke(app, ["run"])
        assert res.exit_code == 1
        assert "unsafe" in res.output and "0600" in res.output

    # 2. Config PermissionError
    sec_file.chmod(0o600)
    with (
        patch("noxai.config.SECRETS_FILE", sec_file),
        patch("noxai.cli.SECRETS_FILE", sec_file),
        patch("noxai.config.get_config", side_effect=PermissionError("denied")),
    ):
        res = runner.invoke(app, ["run"])
        assert res.exit_code == 1
        assert "denied" in res.output

    # 3. DaemonLock error
    with (
        patch("noxai.config.SECRETS_FILE", sec_file),
        patch("noxai.cli.SECRETS_FILE", sec_file),
        patch(
            "noxai.config.get_config",
            return_value=SimpleNamespace(
                logging=SimpleNamespace(level="INFO"),
                server=SimpleNamespace(host="127.0.0.1", port=7777),
            ),
        ),
        patch(
            "noxai.lock.DaemonLock.acquire", side_effect=RuntimeError("Already running (PID 99)")
        ),
    ):
        res = runner.invoke(app, ["run"])
        assert res.exit_code == 1
        assert "Already running" in res.output


def test_chat_command() -> None:
    runner = CliRunner()
    mock_result = SimpleNamespace(
        citations=["https://example.com/source"],
        tool_calls_count=1,
        steps=2,
        duration_seconds=1.2,
    )
    with (
        patch("noxai.agent.loop.AgentLoop.run", new_callable=AsyncMock, return_value=mock_result),
        patch("noxai.db.run_migrations", new_callable=AsyncMock),
        patch("noxai.db.get_db"),
    ):
        res = runner.invoke(app, ["chat", "Hello NoX"])
        assert res.exit_code == 0
        assert "You:" in res.output
        assert "https://example.com/source" in res.output


def test_eval_tools_command() -> None:
    runner = CliRunner()
    with patch("noxai.agent.eval.run_tool_eval", new_callable=AsyncMock) as mock_eval:
        res = runner.invoke(app, ["eval-tools", "qwen2.5:14b"])
        assert res.exit_code == 0
        assert mock_eval.called


def test_tools_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    db_file = tmp_path / "nox.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            args_hash TEXT,
            reason TEXT,
            requested_at TEXT NOT NULL,
            expires_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            decided_at TEXT,
            decided_by TEXT,
            run_id TEXT
        )"""
    )
    now_iso = datetime.now(UTC).isoformat()
    future_iso = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    past_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

    conn.execute(
        "INSERT INTO approvals (tool_name, args_json, args_hash, reason, requested_at, "
        "expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "write_file",
            '{"path": "/tmp/a"}',
            "abc12345",
            "test write",
            now_iso,
            future_iso,
            "pending",
        ),
    )
    conn.execute(
        "INSERT INTO approvals (tool_name, args_json, args_hash, reason, requested_at, "
        "expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "delete_file",
            '{"path": "/tmp/b"}',
            "def67890",
            "expired delete",
            now_iso,
            past_iso,
            "pending",
        ),
    )
    conn.commit()
    conn.close()

    with patch("noxai.cli.DB_PATH", db_file):
        # 1. List pending
        res = runner.invoke(app, ["tools", "list"])
        assert res.exit_code == 0
        assert "write_file" in res.output

        # 2. Approve expired item (id 2) -> marked expired and fails
        res = runner.invoke(app, ["tools", "approve", "2"])
        assert res.exit_code == 1
        assert "expired" in res.output

        # 3. Approve valid item (id 1)
        res = runner.invoke(app, ["tools", "approve", "1"])
        assert res.exit_code == 0
        assert "Approved" in res.output

        # 4. Approve already approved item (id 1)
        res = runner.invoke(app, ["tools", "approve", "1"])
        assert res.exit_code == 0
        assert "already" in res.output

        # 5. Deny non-existent item (id 999)
        res = runner.invoke(app, ["tools", "deny", "999"])
        assert res.exit_code == 1

        # 6. Re-open connection to add a pending row to deny
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "INSERT INTO approvals (id, tool_name, args_json, requested_at, status) "
            "VALUES (3, 'cmd', '{}', ?, 'pending')",
            (now_iso,),
        )
        conn.commit()
        conn.close()

        res = runner.invoke(app, ["tools", "deny", "3"])
        assert res.exit_code == 0
        assert "Denied" in res.output


def test_models_commands() -> None:
    runner = CliRunner()

    # 1. models list
    res = runner.invoke(app, ["models", "list"])
    assert res.exit_code == 0
    assert "Role" in res.output or "Model" in res.output

    # 2. models privacy-test
    res = runner.invoke(app, ["models", "privacy-test"])
    assert res.exit_code == 0
    assert "PASS" in res.output

    # 3. models verify-cloud without key
    with patch(
        "noxai.commands.models.get_config", return_value=SimpleNamespace(nvidia_api_key=None)
    ):
        res = runner.invoke(app, ["models", "verify-cloud"])
        assert res.exit_code == 0
        assert "No NVIDIA API key configured" in res.output

    # 4. models test with mocked router
    mock_prov = MagicMock()
    mock_prov.embed = AsyncMock(return_value=[[0.1] * 768])

    async def fake_chat_stream(*args, **kwargs):
        yield SimpleNamespace(text="ok")

    with (
        patch(
            "noxai.models.router.ModelRouter.resolve_role",
            return_value=(mock_prov, "nomic-embed-text"),
        ),
        patch("noxai.models.router.ModelRouter.chat", side_effect=fake_chat_stream),
    ):
        res = runner.invoke(app, ["models", "test", "--role", "brain"])
        assert res.exit_code == 0

        res = runner.invoke(app, ["models", "test", "--role", "embed"])
        assert res.exit_code == 0

        res = runner.invoke(app, ["models", "test", "--role", "heavy"])
        assert res.exit_code == 0

        res = runner.invoke(app, ["models", "test", "--role", "all"])
        assert res.exit_code == 0


def test_jobs_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    db_file = tmp_path / "jobs.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            schedule_type TEXT NOT NULL,
            schedule_expr TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
            catch_up TEXT NOT NULL DEFAULT 'once',
            delivery_channel TEXT NOT NULL DEFAULT 'feed',
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            instruction TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            last_run_at TEXT,
            next_run_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE job_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            changed_by TEXT NOT NULL DEFAULT 'agent',
            change_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE job_runs (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            error TEXT,
            output_json TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO jobs (id, name, description, schedule_type, schedule_expr, instruction) "
        "VALUES ('job_1', 'daily-briefing', 'Daily briefing', 'cron', '0 7 * * *', "
        "'Summarize news')",
    )
    snap = json.dumps(
        {
            "schedule_type": "cron",
            "schedule_expr": "0 7 * * *",
            "timezone": "Asia/Kolkata",
            "instruction": "Summarize news",
            "config_json": "{}",
            "enabled": 1,
        }
    )
    conn.execute(
        "INSERT INTO job_versions (job_id, version, snapshot_json, changed_by) "
        "VALUES ('job_1', 1, ?, 'seed')",
        (snap,),
    )
    conn.commit()
    conn.close()

    with patch("noxai.commands.jobs.DB_PATH", db_file):
        # list
        res = runner.invoke(app, ["jobs", "list"])
        assert res.exit_code == 0
        assert "daily-brie" in res.output or "daily-briefing" in res.output

        # show
        res = runner.invoke(app, ["jobs", "show", "daily-briefing"])
        assert res.exit_code == 0
        assert "daily-briefing" in res.output

        # disable
        res = runner.invoke(app, ["jobs", "disable", "daily-briefing"])
        assert res.exit_code == 0
        assert "disabled" in res.output.lower()

        # enable
        res = runner.invoke(app, ["jobs", "enable", "daily-briefing"])
        assert res.exit_code == 0
        assert "enabled" in res.output.lower()

        # history
        res = runner.invoke(app, ["jobs", "history", "daily-briefing"])
        assert res.exit_code == 0

        # restore
        res = runner.invoke(app, ["jobs", "restore", "daily-briefing", "1"])
        assert res.exit_code == 0
        assert "Restored" in res.output

        # run
        with patch(
            "noxai.scheduler.scheduler.JobScheduler.run_job",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ) as mock_run:
            res = runner.invoke(app, ["jobs", "run", "daily-briefing"])
            assert res.exit_code == 0
            assert mock_run.called


def test_memory_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    db_file = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT NOT NULL DEFAULT 'user_stated',
            pinned INTEGER NOT NULL DEFAULT 0,
            provenance TEXT NOT NULL DEFAULT 'user_stated',
            needs_review INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        "INSERT INTO facts (category, key, value, confidence, provenance, needs_review) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("preference", "coffee", "Owner likes black coffee", 0.95, "agent_derived", 1),
    )
    conn.commit()
    conn.close()

    with patch("noxai.commands.memory.DB_PATH", db_file):
        # review
        res = runner.invoke(app, ["memory", "review"])
        assert res.exit_code == 0
        assert "coffee" in res.output

        # search with mocked recall_memories
        mock_mem = SimpleNamespace(
            facts=[
                SimpleNamespace(
                    key="coffee",
                    category="preference",
                    value="Owner likes black coffee",
                    pinned=1,
                    confidence=0.95,
                )
            ],
            episodes=[],
        )
        with patch(
            "noxai.commands.memory.recall_memories",
            new_callable=AsyncMock,
            return_value=mock_mem,
        ):
            res = runner.invoke(app, ["memory", "search", "coffee"])
            assert res.exit_code == 0
            assert "coffee" in res.output

        # confirm
        res = runner.invoke(app, ["memory", "confirm", "1"])
        assert res.exit_code == 0
        assert "Confirmed" in res.output

        # reject (add another row)
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "INSERT INTO facts (id, category, key, value, needs_review) "
            "VALUES (2, 'cat', 'k', 'v', 1)"
        )
        conn.commit()
        conn.close()

        res = runner.invoke(app, ["memory", "reject", "2"])
        assert res.exit_code == 0
        assert "Rejected" in res.output


def test_reminders_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    db_file = tmp_path / "reminders.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            schedule_type TEXT NOT NULL,
            schedule_expr TEXT,
            instruction TEXT,
            config_json TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            next_run_at TEXT
        )"""
    )
    future_iso = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO jobs (id, name, description, schedule_type, instruction, enabled, "
        "next_run_at) VALUES (?, ?, ?, 'once', ?, 1, ?)",
        ("rem_123", "Reminder", "drink water", "drink water", future_iso),
    )
    conn.commit()
    conn.close()

    with patch("noxai.commands.reminders.DB_PATH", db_file):
        # list
        res = runner.invoke(app, ["reminders", "list"])
        assert res.exit_code == 0
        assert "drink water" in res.output

        # snooze
        res = runner.invoke(app, ["reminders", "snooze", "rem_123", "--minutes", "15"])
        assert res.exit_code == 0
        assert "Snoozed" in res.output

        # cancel
        res = runner.invoke(app, ["reminders", "cancel", "rem_123"])
        assert res.exit_code == 0
        assert "cancelled" in res.output

        # remind
        with patch(
            "noxai.commands.reminders.parse_natural_time",
            return_value=datetime.now(UTC) + timedelta(minutes=5),
        ):
            res = runner.invoke(app, ["remind", "in 5 minutes take a break"])
            assert res.exit_code == 0
            assert "Reminder scheduled" in res.output


def test_sources_and_briefings_and_audit(tmp_path: Path) -> None:
    runner = CliRunner()
    db_file = tmp_path / "aux.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'rss',
            category TEXT NOT NULL DEFAULT 'tech',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT,
            last_ok_at TEXT,
            last_error TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE outputs (
            id TEXT PRIMARY KEY,
            title TEXT,
            markdown TEXT,
            sources_json TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            tool TEXT NOT NULL,
            tier TEXT NOT NULL,
            decision TEXT NOT NULL,
            approver TEXT,
            ok INTEGER NOT NULL DEFAULT 1,
            result_summary TEXT,
            run_id TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO sources (name, url, kind) "
        "VALUES ('TechNews', 'https://example.com/rss', 'rss')"
    )
    conn.execute(
        "INSERT INTO outputs (id, title, markdown, sources_json, created_at) "
        "VALUES (1, 'daily-briefing', '# News summary', '[]', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO audit_log (ts, tool, tier, decision, ok) "
        "VALUES ('2026-01-01T00:00:00Z', 'write_file', 'dangerous', 'approved', 1)"
    )
    conn.commit()
    conn.close()

    # sources check with mocked httpx.get
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<rss><channel><title>T</title></channel></rss>"
        mock_get.return_value = mock_resp
        res = runner.invoke(app, ["sources", "check"])
        assert res.exit_code == 0

    # briefings list and show
    with patch("noxai.commands.briefings.DB_PATH", db_file):
        res = runner.invoke(app, ["briefings", "list"])
        assert res.exit_code == 0
        assert "daily-briefing" in res.output

        res = runner.invoke(app, ["briefings", "show", "1"])
        assert res.exit_code == 0
        assert "News summary" in res.output

    # audit tail
    with patch("noxai.commands.audit.DB_PATH", db_file):
        res = runner.invoke(app, ["audit", "tail", "-n", "10"])
        assert res.exit_code == 0
        assert "write_file" in res.output or "write_f" in res.output


def test_service_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    unit_file = tmp_path / "noxd.service"

    with (
        patch("noxai.commands.service.SERVICE_UNIT_PATH", unit_file),
        patch("noxai.commands.service.SYSTEMD_USER_DIR", tmp_path),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n")

        # install
        res = runner.invoke(app, ["service", "install"])
        assert res.exit_code == 0
        assert unit_file.exists()
        assert "Wrote" in res.output
        assert "Service installed" in res.output

        # status
        res = runner.invoke(app, ["service", "status"])
        assert res.exit_code == 0
        assert "active" in res.output

        # logs
        res = runner.invoke(app, ["service", "logs", "-n", "10"])
        assert res.exit_code == 0

        # uninstall
        res = runner.invoke(app, ["service", "uninstall"])
        assert res.exit_code == 0
        assert not unit_file.exists()

    # session-env
    with (
        patch("subprocess.run") as mock_run,
        patch("shutil.which", return_value="/usr/bin/dbus-update-activation-environment"),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        res = runner.invoke(app, ["session-env"])
        assert res.exit_code == 0
        assert "Imported session environment" in res.output
