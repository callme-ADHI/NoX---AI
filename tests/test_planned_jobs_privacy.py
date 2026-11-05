import pytest
from noxai.jobs.privacy import sanitize_instruction, resolve_plan, assert_no_paths_or_urls

def test_sanitize_and_assert_no_paths_or_urls():
    raw = (
        "Backup ~/Documents/Certificates, ~/Documents/Study, and ~/Documents/\"Obsidian Vault\" "
        "to https://github.com/myuser/myrepo.git every day at 8pm"
    )
    sanitized = sanitize_instruction(raw, user_home="/home/myuser")
    
    assert "/home/myuser" not in sanitized.sanitized_text
    assert "~/Documents" not in sanitized.sanitized_text
    assert "https://github.com" not in sanitized.sanitized_text
    assert "Certificates" not in sanitized.sanitized_text
    assert "Obsidian Vault" not in sanitized.sanitized_text
    assert "Study" not in sanitized.sanitized_text
    
    # Assert privacy verification passes
    assert_no_paths_or_urls(sanitized.sanitized_text, user_home="/home/myuser")
    
    # Assert un-sanitized string fails privacy check
    with pytest.raises(ValueError):
        assert_no_paths_or_urls(raw, user_home="/home/myuser")

def test_resolve_plan():
    raw = "Backup ~/Documents/Certificates to https://github.com/user/repo"
    sanitized = sanitize_instruction(raw, user_home="/home/myuser")
    
    dummy_plan = {
        "steps": [
            {"kind": "git.ensure_repo", "folder": "{{folder_1}}"},
            {"kind": "git.push", "remote": "{{remote_1}}"}
        ]
    }
    
    resolved = resolve_plan(dummy_plan, sanitized)
    assert resolved["steps"][0]["folder"] == "~/Documents/Certificates"
    assert resolved["steps"][1]["remote"] == "https://github.com/user/repo"


@pytest.mark.asyncio
async def test_outbound_cloud_privacy_recorded_body(monkeypatch, tmp_path):
    """MANDATORY & BLOCKING: Record the real request body sent to cloud planner and verify privacy (Rule 7, Section 8.1)."""
    import json
    from noxai.jobs.planner import generate_plan_for_job
    from noxai.config import Settings
    from noxai.jobs.schema import JobPlan
    from noxai.db import get_db, execute

    # Setup temp DB
    db_file = tmp_path / "test_privacy.db"
    import aiosqlite
    async with aiosqlite.connect(str(db_file)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instruction TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_plans (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                steps_json TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                approved_at TEXT,
                approved_by TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await conn.commit()

    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))

    # Spy to record outbound request body
    recorded_bodies: list[dict] = []

    class FakeClient:
        async def post(self, url, json=None, headers=None, timeout=None):
            recorded_bodies.append(json)
            # Return valid simulated cloud plan with placeholders
            fake_response_content = json_dump_simulated_cloud_plan()
            import httpx
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": fake_response_content
                            }
                        }
                    ]
                }
            )

    def json_dump_simulated_cloud_plan():
        return json.dumps({
            "steps": [
                {
                    "step_index": 1,
                    "kind": "git.preflight",
                    "folder_alias": "folder_1",
                    "folder_path": "{{folder_1}}",
                    "remote": "{{remote_1}}",
                    "timeout_seconds": 30
                },
                {
                    "step_index": 2,
                    "kind": "git.preflight",
                    "folder_alias": "folder_2",
                    "folder_path": "{{folder_2}}",
                    "remote": "{{remote_1}}",
                    "timeout_seconds": 30
                },
                {
                    "step_index": 3,
                    "kind": "git.push",
                    "folder_alias": "folder_1",
                    "folder_path": "{{folder_1}}",
                    "remote": "{{remote_1}}",
                    "params": {"branch": "main"},
                    "timeout_seconds": 120
                }
            ]
        })

    class FakeKeyState:
        api_key = "fake-key"

    class FakeKeyPool:
        def is_empty(self):
            return False
        @property
        def healthy_keys_count(self):
            return 1
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def execute_request(self, *args, **kwargs):
            yield FakeKeyState(), FakeClient()

    monkeypatch.setattr("noxai.jobs.planner.get_default_keypool", lambda cfg: FakeKeyPool())

    from noxai.config import get_config
    cfg = get_config()
    cfg.models.nvidia.enabled = True

    raw_instruction = (
        "Push ~/Documents/Certificates, ~/Documents/Study, and ~/Documents/\"Obsidian Vault\" "
        "to https://github.com/boss-user/private-notes.git every day after 20:00 Asia/Kolkata"
    )

    plan = await generate_plan_for_job(
        job_id="job_test_priv",
        instruction=raw_instruction,
        config=cfg,
        force_template=False,
    )

    assert len(recorded_bodies) == 1
    outbound = recorded_bodies[0]
    outbound_str = json.dumps(outbound)

    # 1. No absolute or home path substring
    for forbidden in ["/home", "/tmp", "/var", "/etc", "/Users", "~/Documents", "~/.config", "~/.local"]:
        assert forbidden not in outbound_str, f"Found forbidden path '{forbidden}' in outbound payload: {outbound_str}"

    # 2. No URL substring
    for forbidden in ["http://", "https://", "github.com", "git@", "ssh://"]:
        assert forbidden not in outbound_str, f"Found forbidden URL '{forbidden}' in outbound payload: {outbound_str}"

    # 3. No user-identifying strings
    for forbidden in ["boss-user", "private-notes", "Certificates", "Study", "Obsidian Vault"]:
        assert forbidden not in outbound_str, f"Found forbidden identifier '{forbidden}' in outbound payload: {outbound_str}"

    # 4. Plan resolution restored paths for local use
    assert plan.source == "cloud"
    step_folders = [s.folder_path for s in plan.steps if s.folder_path]
    assert any("Certificates" in f for f in step_folders)
    assert any("Obsidian Vault" in f for f in step_folders)

    # 5. Deterministic fallback produces template plan when cloud is disabled or returns garbage
    plan_fb = await generate_plan_for_job(
        job_id="job_test_priv",
        instruction=raw_instruction,
        config=cfg,
        force_template=True,
    )
    assert plan_fb.source == "template"
    assert len(plan_fb.steps) >= 9  # Canonical 3 folders * 3 steps each + notify


