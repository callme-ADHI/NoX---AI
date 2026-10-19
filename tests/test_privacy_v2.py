"""Tests for NoX Privacy Policy v2 (Milestone 10, §1.2 & §1.3).

Covers:
- DataClass enum, ranking, and cloud permission rules.
- Content wrapper enhancements, constructors, and merge semantics.
- Sensitivity and secret detection, masking, and heuristic classification.
- EgressFilter choke point (private mode, secrets, local_only, fail-closed strings).
- Egress ledger audit persistence.
- Fact visibility migration and storage.
- 500-iteration property test for zero-leak cloud egress guarantee.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, PrivacyConfig, Settings
from noxai.db import get_db, run_migrations
from noxai.memory.store import create_fact, get_fact, update_fact
from noxai.models.base import Message, ToolCall
from noxai.security.egress_filter import (
    EgressDecision,
    EgressFilter,
    get_recent_egress_records,
    record_egress_decision,
)
from noxai.security.sensitivity import (
    is_sensitive_prompt,
    mask_secrets,
    scan_for_secrets,
)
from noxai.taint import (
    Content,
    DataClass,
    TaintFlag,
    combine_data_class,
)


def make_test_settings(privacy: PrivacyConfig | None = None) -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
            }
        ),
        privacy=privacy or PrivacyConfig(),
    )


# ---------------------------------------------------------------------------
# 1. DataClass and Content Tests
# ---------------------------------------------------------------------------


class TestTaintDataClass:
    def test_dataclass_ranks(self) -> None:
        """SECRET > LOCAL_ONLY > CHAT > PUBLIC."""
        assert DataClass.SECRET.rank > DataClass.LOCAL_ONLY.rank
        assert DataClass.LOCAL_ONLY.rank > DataClass.CHAT.rank
        assert DataClass.CHAT.rank > DataClass.PUBLIC.rank

    def test_is_cloud_allowed_rules(self) -> None:
        """Validate cloud allowance per DataClass under different modes."""
        # SECRET and LOCAL_ONLY are never allowed
        assert not DataClass.SECRET.is_cloud_allowed(chat_to_cloud=True, private_mode=False)
        assert not DataClass.LOCAL_ONLY.is_cloud_allowed(chat_to_cloud=True, private_mode=False)

        # PUBLIC allowed unless private_mode is active
        assert DataClass.PUBLIC.is_cloud_allowed(chat_to_cloud=True, private_mode=False)
        assert not DataClass.PUBLIC.is_cloud_allowed(chat_to_cloud=True, private_mode=True)

        # CHAT allowed only if chat_to_cloud is True and private_mode is False
        assert DataClass.CHAT.is_cloud_allowed(chat_to_cloud=True, private_mode=False)
        assert not DataClass.CHAT.is_cloud_allowed(chat_to_cloud=False, private_mode=False)
        assert not DataClass.CHAT.is_cloud_allowed(chat_to_cloud=True, private_mode=True)

    def test_content_constructors_and_properties(self) -> None:
        """Validate convenience constructors and taint/privacy properties."""
        s = Content.secret("my_api_key")
        assert s.get_data_class() == DataClass.SECRET
        assert s.is_private()
        assert not s.is_clean()

        c_loc = Content.local_only("system files")
        assert c_loc.get_data_class() == DataClass.LOCAL_ONLY
        assert c_loc.is_private()

        c = Content.chat("hello agent")
        assert c.get_data_class() == DataClass.CHAT
        assert not c.is_private()
        assert c.is_clean()

        p = Content.public("wikipedia article")
        assert p.get_data_class() == DataClass.PUBLIC
        assert not p.is_private()
        assert p.is_clean()

        u = Content.untrusted("web payload")
        assert u.get_data_class() == DataClass.PUBLIC
        assert u.is_untrusted()
        assert not u.is_private()

    def test_content_merge_escalation(self) -> None:
        """Merging contents preserves highest rank and union of taint flags."""
        c_pub = Content.public("public info")
        c_chat = Content.chat("user comment")
        c_local = Content.local_only("local state")
        c_secret = Content.secret("secret token")

        m1 = c_pub.merge(c_chat)
        assert m1.get_data_class() == DataClass.CHAT

        m2 = m1.merge(c_local)
        assert m2.get_data_class() == DataClass.LOCAL_ONLY
        assert m2.is_private()

        m3 = m2.merge(c_secret)
        assert m3.get_data_class() == DataClass.SECRET
        assert m3.is_private()

    def test_combine_data_class(self) -> None:
        c1 = Content.public("a")
        c2 = Content.chat("b")
        c3 = Content.local_only("c")
        assert combine_data_class(c1, c2) == DataClass.CHAT
        assert combine_data_class(c1, c2, c3) == DataClass.LOCAL_ONLY


# ---------------------------------------------------------------------------
# 2. Sensitivity and Secret Detector Tests
# ---------------------------------------------------------------------------


class TestSensitivityDetector:
    @pytest.mark.parametrize(
        "secret_text,pattern_name",
        [
            ("sk-proj-1234567890abcdefghijklmnopqrstuv", "openai_key"),
            ("sk-ant-api03-1234567890abcdefghijklmnopqrstuvwxyz12345", "anthropic_key"),
            ("nvapi-1234567890abcdefghijklmnopqrstuvwxyz", "nvidia_key"),
            ("ghp_1234567890abcdefghijklmnopqrstuvwxyz12", "github_token"),
            ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
            ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", "bearer_token"),
            ("api_key = 'abcdef1234567890abcdef'", "generic_api_key"),
            (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIEowIBAAKCAQEA...\n"
                "-----END RSA PRIVATE KEY-----",
                "private_key",
            ),
            ("password = 'SuperSecretPassword123!'", "password_assignment"),
            ("4111 2222 3333 4444", "credit_card"),
            ("123-45-6789", "ssn"),
        ],
    )
    def test_scan_for_secrets(self, secret_text: str, pattern_name: str) -> None:
        matches = scan_for_secrets(f"Here is confidential data: {secret_text} please keep safe.")
        assert len(matches) >= 1
        assert any(m.pattern_name == pattern_name for m in matches)

    def test_mask_secrets(self) -> None:
        raw = "My OpenAI key is sk-proj-1234567890abcdefghijklmnopqrstuv and ssn is 123-45-6789."
        masked = mask_secrets(raw)
        assert "sk-proj" not in masked
        assert "123-45-6789" not in masked
        assert "[REDACTED_SECRET]" in masked

    def test_sensitive_prompts_heuristic(self) -> None:
        sens, reason = is_sensitive_prompt("Can you review my medical records from yesterday?")
        assert sens is True
        assert "health_medical" in str(reason)

        sens, reason = is_sensitive_prompt("Please check my bank account balance and transactions.")
        assert sens is True
        assert "financial_banking" in str(reason)

        sens, reason = is_sensitive_prompt("What is the speed of light in vacuum?")
        assert sens is False
        assert reason is None


# ---------------------------------------------------------------------------
# 3. EgressFilter Unit Tests
# ---------------------------------------------------------------------------


class TestEgressFilter:
    def test_local_provider_always_allowed(self) -> None:
        filt = EgressFilter()
        msgs = [Message(role="user", content=Content.secret("top-secret-token"))]
        decision = filt.evaluate(msgs, provider_name="ollama", model="qwen2.5:14b", is_local=True)
        assert decision.allowed is True
        assert decision.decision == "allowed"

    def test_private_mode_blocks_cloud(self) -> None:
        cfg = make_test_settings(PrivacyConfig(private_mode=True))
        filt = EgressFilter(config=cfg)
        msgs = [Message(role="user", content=Content.public("safe public question"))]
        decision = filt.evaluate(
            msgs, provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert decision.allowed is False
        assert decision.decision == "rerouted_local"
        assert "Private mode" in decision.reason

    def test_chat_to_cloud_disabled_reroutes_chat(self) -> None:
        cfg = make_test_settings(PrivacyConfig(chat_to_cloud=False))
        filt = EgressFilter(config=cfg)
        msgs = [Message(role="user", content=Content.chat("hello"))]
        decision = filt.evaluate(
            msgs, provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert decision.allowed is False
        assert decision.decision == "rerouted_local"
        assert "chat_to_cloud" in decision.reason

    def test_plain_unlabeled_string_fails_closed(self) -> None:
        filt = EgressFilter()
        msgs = [Message(role="user", content="plain unlabeled string without Content wrapper")]
        decision = filt.evaluate(
            msgs, provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert decision.allowed is False
        assert decision.decision == "blocked"
        assert "fail closed" in decision.reason

    def test_explicit_secret_and_local_only_blocked(self) -> None:
        filt = EgressFilter()
        msg_secret = Message(role="user", content=Content.secret("my_pass"))
        dec1 = filt.evaluate(
            [msg_secret], provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert dec1.allowed is False
        assert dec1.decision == "blocked"

        msg_local = Message(role="user", content=Content.local_only("/etc/hosts content"))
        dec2 = filt.evaluate(
            [msg_local], provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert dec2.allowed is False
        assert dec2.decision == "blocked"

    def test_masking_secrets_in_chat(self) -> None:
        filt = EgressFilter()
        chat_msg = Message(
            role="user",
            content=Content.chat(
                "Here is my key: nvapi-1234567890abcdefghijklmnopqrstuvwxyz please test."
            ),
        )
        dec = filt.evaluate(
            [chat_msg], provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert dec.allowed is True
        assert dec.sanitized_messages is not None
        sanitized_text = dec.sanitized_messages[0].get_model_text()
        assert "nvapi-" not in sanitized_text
        assert "[REDACTED_SECRET]" in sanitized_text

    def test_sensitive_user_prompt_reroutes_local(self) -> None:
        filt = EgressFilter()
        sens_msg = Message(
            role="user",
            content=Content.chat("What are the side effects of my prescription medication?"),
        )
        dec = filt.evaluate(
            [sens_msg], provider_name="nvidia", model="llama-3.3-70b", is_local=False
        )
        assert dec.allowed is False
        assert dec.decision == "rerouted_local"
        assert "Sensitive prompt" in dec.reason


# ---------------------------------------------------------------------------
# 4. Egress Ledger and Fact Visibility DB Tests
# ---------------------------------------------------------------------------


class TestDatabasePrivacyV2:
    @pytest.mark.asyncio
    async def test_egress_ledger_persistence(self) -> None:
        await run_migrations()
        async with get_db() as db:
            dec = EgressDecision(
                allowed=True,
                decision="allowed",
                reason="Clean public turn",
                data_class=DataClass.PUBLIC,
                prompt_tokens_est=42,
                sha256_hash="deadbeefcafe",
            )
            row_id = await record_egress_decision(dec, provider="nvidia", model="llama-70b", db=db)
            assert row_id is not None

            records = await get_recent_egress_records(limit=10, db=db)
            assert len(records) >= 1
            latest = records[0]
            assert latest["provider"] == "nvidia"
            assert latest["decision"] == "allowed"
            assert latest["prompt_tokens_est"] == 42

    @pytest.mark.asyncio
    async def test_fact_visibility_defaults_to_local_only(self) -> None:
        await run_migrations()
        async with get_db() as db:
            fact = await create_fact(
                db,
                key="test_fact_priv",
                value="Private owner note",
                category="profile",
            )
            assert fact.visibility == "local_only"

            # Update to cloud_ok
            updated = await update_fact(db, fact.id, visibility="cloud_ok")
            assert updated is not None
            assert updated.visibility == "cloud_ok"

            fetched = await get_fact(db, fact.id)
            assert fetched is not None
            assert fetched.visibility == "cloud_ok"


# ---------------------------------------------------------------------------
# 5. Property-Based Privacy Invariant Test (500 Iterations)
# ---------------------------------------------------------------------------


class TestPropertyPrivacyV2:
    def test_500_random_payloads_zero_cloud_leak_guarantee(self) -> None:
        """Seeded random generator over 500 permutations verifies no leak is possible."""
        rng = random.Random(1337)

        data_classes = [DataClass.PUBLIC, DataClass.CHAT, DataClass.LOCAL_ONLY, DataClass.SECRET]
        taint_flags = [TaintFlag.NONE, TaintFlag.PRIVATE, TaintFlag.UNTRUSTED]
        roles = ["system", "user", "assistant", "tool"]
        secret_templates = [
            "nvapi-randomkey1234567890abcdef12345",
            "sk-ant-randomkey1234567890abcdef12345",
            "AKIAIOSFODNN7EXAMPLE",
        ]

        for iteration in range(500):
            private_mode = rng.random() < 0.25
            chat_to_cloud = rng.random() < 0.75
            cfg = make_test_settings(
                PrivacyConfig(private_mode=private_mode, chat_to_cloud=chat_to_cloud)
            )
            filt = EgressFilter(config=cfg)

            num_msgs = rng.randint(1, 4)
            messages: list[Message] = []
            has_secret_dataclass = False
            has_local_only_dataclass = False
            has_unmasked_secret = False
            has_plain_string = False
            has_chat = False
            has_sensitive_topic = False

            for _ in range(num_msgs):
                r = rng.choice(roles)
                is_plain = rng.random() < 0.2

                if is_plain:
                    has_plain_string = True
                    content: Any = f"Unlabeled text payload {iteration}"
                else:
                    dc = rng.choice(data_classes)
                    tf = rng.choice(taint_flags)

                    if dc == DataClass.SECRET or (tf & TaintFlag.PRIVATE):
                        has_local_only_dataclass = True
                    if dc == DataClass.SECRET:
                        has_secret_dataclass = True
                    if dc == DataClass.LOCAL_ONLY:
                        has_local_only_dataclass = True
                    if dc == DataClass.CHAT:
                        has_chat = True

                    txt = f"Structured text iter {iteration}"
                    # Maybe inject a secret
                    if rng.random() < 0.2:
                        txt += " " + rng.choice(secret_templates)
                        # In SECRET data class or allow_masking=False, secret is unmasked
                        if dc == DataClass.SECRET:
                            has_unmasked_secret = True

                    # Maybe inject sensitive topic in user message
                    if r == "user" and rng.random() < 0.15:
                        txt += " and my medical records from the hospital"
                        has_sensitive_topic = True

                    content = Content(text=txt, taint=tf, data_class=dc)

                # Tool calls on assistant
                tcs: list[ToolCall] = []
                if r == "assistant" and rng.random() < 0.3:
                    tc_priv = rng.random() < 0.5
                    if tc_priv:
                        has_local_only_dataclass = True
                    tcs.append(
                        ToolCall(
                            id="call_1",
                            name="fetch_file",
                            arguments={"path": "/etc/secret"},
                            taint=TaintFlag.PRIVATE if tc_priv else TaintFlag.NONE,
                        )
                    )

                messages.append(Message(role=r, content=content, tool_calls=tcs))

            decision = filt.evaluate(
                messages,
                provider_name="nvidia",
                model="llama-70b",
                is_local=False,
            )

            # --- INVARIANT ASSERTIONS ---
            if private_mode:
                assert decision.allowed is False
                assert decision.decision == "rerouted_local"

            if has_plain_string:
                assert decision.allowed is False

            if has_secret_dataclass or has_unmasked_secret:
                assert decision.allowed is False

            if has_local_only_dataclass:
                assert decision.allowed is False

            if has_chat and not chat_to_cloud:
                assert decision.allowed is False

            if has_sensitive_topic:
                assert decision.allowed is False

            if decision.allowed:
                # If allowed, none of the blocking conditions must have been met
                assert not private_mode
                assert not has_plain_string
                assert not has_secret_dataclass
                assert not has_local_only_dataclass
                assert not has_sensitive_topic
                if has_chat:
                    assert chat_to_cloud
