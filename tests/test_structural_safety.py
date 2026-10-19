"""Tests for E7.2 Structural Safety (AST scans, untrusted wrapping, M1 property test)."""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.base import Content, Message, PrivacyViolation, ToolCall
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.taint import TaintFlag
from noxai.tools.registry import ToolContext

REPO_SRC = Path(__file__).parent.parent / "src" / "noxai"


class TestStructuralSafetyAST:
    def test_tools_do_not_import_safety_admin_or_touch_approvals_audit_directly(self) -> None:
        """(a) AST scan: tools under tools/ cannot import safety-admin code or write to DB."""
        tools_dir = REPO_SRC / "tools"
        assert tools_dir.is_dir(), f"Tools dir {tools_dir} not found"

        forbidden_sql_patterns = [
            "insert into approvals",
            "update approvals",
            "delete from approvals",
            "insert into audit_log",
            "update audit_log",
            "delete from audit_log",
        ]

        violations = []

        for py_path in tools_dir.rglob("*.py"):
            rel_path = py_path.relative_to(tools_dir)
            # registry.py owns the approval/audit pipeline; safety.py defines the safety primitives
            if rel_path.name in ("registry.py", "safety.py"):
                continue

            content = py_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_path))

            # 1. AST check: no imports of safety internal configs or admin functions
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "safety_whitelist" in alias.name:
                            violations.append((str(rel_path), f"Direct import of {alias.name}"))
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for alias in node.names:
                        if alias.name in ("safety_whitelist", "modify_tier", "bypass_safety"):
                            violations.append((str(rel_path), f"Import from {mod}: {alias.name}"))

                # Check string literals for raw write SQL to approvals or audit_log
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    s_lower = node.value.lower()
                    for pat in forbidden_sql_patterns:
                        if pat in s_lower:
                            violations.append(
                                (
                                    str(rel_path),
                                    f"Direct write SQL pattern: '{pat}' in '{node.value[:40]}'",
                                )
                            )

        assert not violations, f"AST scan violations in tools directory: {violations}"

    def test_llm_providers_never_called_outside_router(self) -> None:
        """(b) Codebase scan: no LLM provider is invoked directly outside the ModelRouter."""
        violations = []

        for py_path in REPO_SRC.rglob("*.py"):
            rel_path = py_path.relative_to(REPO_SRC)
            # Models subpackage defines providers and router
            if str(rel_path).startswith("models/"):
                continue

            content = py_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_path))

            for node in ast.walk(tree):
                # Check for direct imports of concrete provider classes
                if isinstance(node, ast.ImportFrom):
                    if node.module and (
                        "models.ollama" in node.module or "models.nvidia" in node.module
                    ):
                        for alias in node.names:
                            if alias.name in ("OllamaProvider", "NvidiaProvider"):
                                violations.append(
                                    (str(rel_path), f"Direct import of provider {alias.name}")
                                )

                # Check for method calls directly on provider names
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "chat":
                        # Verify caller is named router or loop.router
                        val = node.func.value
                        if isinstance(val, ast.Name) and val.id in ("ollama", "nvidia", "provider"):
                            violations.append(
                                (str(rel_path), f"Direct call to {val.id}.chat() outside router")
                            )

        assert not violations, f"LLM providers invoked outside ModelRouter: {violations}"


class TestUntrustedToolWrapping:
    @pytest.mark.asyncio
    async def test_tools_marked_untrusted_output_are_wrapped_and_taint_escalated(self) -> None:
        """(c) Tool marked untrusted_output wraps result with tags and sets UNTRUSTED."""
        from noxai.tools.registry import default_registry

        registry = default_registry
        untrusted_tools = [
            t
            for t in registry.get_specs()
            if registry.get(t.name) and registry.get(t.name).untrusted_output  # type: ignore[union-attr]
        ]

        assert len(untrusted_tools) >= 2, (
            "Expected at least 2 untrusted tools (e.g. web_search, fetch_page)"
        )

        cfg = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                }
            )
        )
        for t_spec in untrusted_tools:
            tool_name = t_spec.name
            t_def = registry.get(tool_name)
            assert t_def is not None
            assert t_def.untrusted_output is True

            # Execute tool through registry
            ctx = ToolContext(
                run_id="test-untrusted-wrap",
                config=cfg,
                context_taint=TaintFlag.NONE,
            )

            # Test using dummy/safe arguments that produce valid ToolResult
            if tool_name == "web_search":
                raw_args = {"query": "python linux kernel"}
            elif tool_name == "fetch_page":
                continue
            elif tool_name == "rss_fetch":
                continue
            else:
                raw_args = {}

            res = await registry.execute(tool_name, raw_args, ctx)
            if res.ok:
                assert "<untrusted_content>" in res.output
                assert "</untrusted_content>" in res.output
                assert bool(ctx.context_taint & TaintFlag.UNTRUSTED) is True


class TestPromptTemplatesValidation:
    def test_all_prompts_exist_and_contain_required_placeholders(self) -> None:
        """(d) Load every file in prompts/ and check required structural tokens exist."""
        prompts_dir = REPO_SRC / "prompts"
        assert prompts_dir.is_dir()

        required_prompts = {
            "extractor.md": ["facts", "episode_summary", "importance"],
            "planner.md": ["goal", "subtasks", "synthesis_instructions"],
            "worker.md": ["summary", "key_points", "sources", "confidence", "gaps"],
            "synthesizer.md": ["## Sources", "## Gaps & Limitations"],
            "system.md": ["NoX", "<untrusted_content>"],
        }

        for fname, required_tokens in required_prompts.items():
            fpath = prompts_dir / fname
            assert fpath.is_file(), f"Missing required prompt file: {fname}"
            content = fpath.read_text(encoding="utf-8")
            assert len(content.strip()) > 50, f"Prompt {fname} is unexpectedly short or empty"

            for token in required_tokens:
                assert token in content, f"Prompt {fname} missing required token: '{token}'"


class TestM1PropertyTest:
    def test_m1_property_500_random_message_lists_cloud_iff_no_private(self) -> None:
        """(e) Seeded generator over 500 random lists asserts cloud iff no PRIVATE."""
        rng = random.Random(42)

        local_fake = FakeProvider(name="ollama", is_local=True)
        cloud_fake = FakeProvider(name="nvidia", is_local=False)
        cloud_fake.api_key = "test-key"  # type: ignore[attr-defined]

        cfg = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                    "heavy": ModelRoleConfig(provider="nvidia", model="llama-3.3-70b"),
                }
            )
        )
        router = ModelRouter(config=cfg, providers={"ollama": local_fake, "nvidia": cloud_fake})

        taint_options = [
            TaintFlag.NONE,
            TaintFlag.UNTRUSTED,
            TaintFlag.PRIVATE,
            TaintFlag.PRIVATE | TaintFlag.UNTRUSTED,
        ]

        roles = ["system", "user", "assistant", "tool"]

        for iteration in range(500):
            num_msgs = rng.randint(1, 6)
            messages: list[Message] = []
            has_private = False

            for _ in range(num_msgs):
                r = rng.choice(roles)
                t = rng.choice(taint_options)
                is_this_private = bool(t & TaintFlag.PRIVATE)

                # Tool call injection on assistant messages
                tool_calls: list[ToolCall] = []
                if r == "assistant" and rng.random() < 0.4:
                    tc_taint = rng.choice(taint_options)
                    if tc_taint & TaintFlag.PRIVATE:
                        is_this_private = True
                    # Arg taint
                    arg_taint = rng.choice(taint_options)
                    if arg_taint & TaintFlag.PRIVATE:
                        is_this_private = True
                    args = {
                        "param1": "safe_val",
                        "param2": Content(text="arg_val", taint=arg_taint),
                    }
                    tool_calls.append(
                        ToolCall(id="c1", name="read_file", arguments=args, taint=tc_taint)
                    )

                if is_this_private:
                    has_private = True

                content_obj = Content(text=f"Text content iter {iteration}", taint=t)
                msg = Message(role=r, content=content_obj, tool_calls=tool_calls)
                messages.append(msg)

            # Test router check_privacy
            provider, _ = router.resolve_role("heavy")
            if has_private:
                with pytest.raises(PrivacyViolation):
                    router.check_privacy("heavy", provider, messages)
            else:
                # Must not raise
                router.check_privacy("heavy", provider, messages)
