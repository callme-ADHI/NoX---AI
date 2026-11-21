"""Mail profile, classification rules, phishing detection, and digest generator for NoX.

Handles rule-based triage, Indian banking isolation (strictly local_only),
homograph / lookalike phishing detection, deadline and action item extraction,
and daily morning / evening digests.
"""

from __future__ import annotations

import logging
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from noxai.db import get_db

log = logging.getLogger(__name__)

DEFAULT_MAIL_PROFILE_PATH = Path("~/.config/nox/mail_profile.toml").expanduser()

DEFAULT_PROFILE_TOML = """[profile]
name = "boss"
college_domains = ["edu", "ac.in", "university.edu"]
interests = ["AI", "Security", "Linux", "Open Source"]

[categories.College]
keywords = [
    "assignment", "exam", "grade", "professor", "lecture",
    "campus", "tuition", "semester", "syllabus"
]

[categories.Opportunities]
keywords = [
    "interview", "offer", "internship", "hiring", "job",
    "recruiter", "application status", "shortlisted", "referral"
]

[categories.Hackathons_CTF]
keywords = [
    "hackathon", "ctf", "devpost", "unstop", "kaggle",
    "bounty", "challenge", "flag", "codeforces", "leetcode"
]

[categories.GitHub_CI]
keywords = [
    "github.com", "pull request", "workflow run", "failed",
    "release", "issue", "dependabot", "actions"
]

[categories.Payments_Banking]
keywords = [
    "bank", "transaction", "debited", "credited", "upi",
    "statement", "invoice", "receipt", "neft", "imps",
    "otp", "card", "payment"
]

[categories.Newsletters]
keywords = [
    "unsubscribe", "digest", "newsletter", "edition",
    "substack", "tldr", "weekly", "monthly roundup"
]

[categories.Personal]
keywords = ["hey", "hello", "coffee", "meet", "weekend", "plans", "catch up"]

[categories.Noise]
keywords = [
    "sale", "discount", "promo", "coupon",
    "limited time offer", "shop now", "clearance"
]
"""

BRAND_DOMAINS = {
    "google": ["google.com", "google.co.in", "youtube.com", "gmail.com"],
    "github": ["github.com"],
    "apple": ["apple.com", "icloud.com"],
    "paypal": ["paypal.com"],
    "amazon": ["amazon.com", "amazon.in"],
    "microsoft": ["microsoft.com", "live.com", "office.com"],
    "sbi": ["sbi.co.in", "statebankofindia.com"],
    "hdfc": ["hdfcbank.com"],
    "icici": ["icicibank.com"],
    "netflix": ["netflix.com"],
}


class MailProfile:
    """Loads and caches user mail profile configuration."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or DEFAULT_MAIL_PROFILE_PATH
        self.config = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(DEFAULT_PROFILE_TOML, encoding="utf-8")
            except Exception as e:
                log.warning("could_not_write_default_mail_profile error=%s", e)
            return tomllib.loads(DEFAULT_PROFILE_TOML)

        try:
            with open(self.config_path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            log.error("failed_to_load_mail_profile path=%s error=%s", self.config_path, e)
            return tomllib.loads(DEFAULT_PROFILE_TOML)


class PhishingDetector:
    """Detects brand spoofing, lookalike domains, and phishing signals."""

    @classmethod
    def check_sender(
        cls, from_name: str, from_domain: str, subject: str, text: str
    ) -> dict[str, Any]:
        low_name = from_name.lower()
        low_domain = from_domain.lower()
        low_text = text.lower()
        low_subj = subject.lower()

        is_suspicious = False
        reasons: list[str] = []

        # 1. Lookalike brand spoofing in display name
        brand_names = {
            "google": "Google",
            "github": "GitHub",
            "apple": "Apple",
            "paypal": "PayPal",
            "amazon": "Amazon",
            "microsoft": "Microsoft",
            "sbi": "SBI",
            "hdfc": "HDFC",
            "icici": "ICICI",
            "netflix": "Netflix",
        }
        for brand, legitimate_domains in BRAND_DOMAINS.items():
            if brand in low_name:
                # If brand is in display name, sender domain MUST end with legitimate domain
                matches = any(
                    low_domain == leg or low_domain.endswith("." + leg)
                    for leg in legitimate_domains
                )
                if not matches and low_domain:
                    is_suspicious = True
                    b_name = brand_names.get(brand, brand.title())
                    reasons.append(
                        f"Display name mentions '{b_name}' but sender domain is '{from_domain}'"
                    )

        # 2. Punycode / IDN homoglyphs
        if "xn--" in low_domain:
            is_suspicious = True
            reasons.append(f"Sender domain uses Punycode/homoglyph ({from_domain})")

        # 3. Urgency / threat keywords
        threat_patterns = [
            "account suspended",
            "verify your password",
            "security alert: confirm immediately",
            "unauthorized access: click here",
            "action required within 24 hours",
        ]
        for pattern in threat_patterns:
            if pattern in low_text or pattern in low_subj:
                reasons.append(f"High-urgency phishing pattern detected: '{pattern}'")
                if is_suspicious:
                    is_suspicious = True

        return {
            "suspicious": is_suspicious,
            "risk_level": "high"
            if (is_suspicious and len(reasons) > 1)
            else ("medium" if is_suspicious else "low"),
            "reasons": reasons,
        }


class MailClassifier:
    """Classifies emails, extracts deadlines, events, and action items."""

    def __init__(self, profile: MailProfile | None = None) -> None:
        self.profile = profile or MailProfile()

    def classify(self, subject: str, text: str, from_domain: str) -> str:
        content = f"{subject} {text}".lower()
        cfg = self.profile.config

        # Check college domains first
        college_domains = cfg.get("profile", {}).get("college_domains", [])
        for cd in college_domains:
            if from_domain.endswith(cd):
                return "College"

        cats = cfg.get("categories", {})
        # Check high priority categories first
        priority_order = [
            "Payments_Banking",
            "Opportunities",
            "Hackathons_CTF",
            "GitHub_CI",
            "College",
            "Newsletters",
            "Personal",
            "Noise",
        ]

        for cat_key in priority_order:
            cat_data = cats.get(cat_key, {})
            keywords = cat_data.get("keywords", [])
            for kw in keywords:
                if kw.lower() in content:
                    return cat_key.replace("_", "/")

        return "Personal"

    def extract_action_items(self, text: str) -> list[str]:
        """Extract explicit action requests from email body."""
        action_patterns = [
            r"(?:please|kindly)\s+([^\n\.\?!]{10,100})",
            r"(?:action required|next steps?):\s*([^\n\.\?!]{10,100})",
            r"(?:complete|submit|review|reply by|register before)\s+([^\n\.\?!]{10,100})",
        ]
        items: list[str] = []
        for pat in action_patterns:
            matches = re.findall(pat, text, re.IGNORECASE)
            for m in matches:
                clean = m.strip()
                if clean and clean not in items:
                    items.append(clean)
        return items[:5]

    def extract_deadlines(self, subject: str, text: str) -> list[str]:
        """Extract date deadlines from email subject or body."""
        combined = f"{subject}\n{text}"
        patterns = [
            r"(?:due|deadline|by|before)\s+([A-Z][a-z]{2,8}\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,\s*\d{4})?)",
            r"(?:due|deadline|by|before)\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            r"(?:due|deadline|by|before)\s+(\d{4}-\d{2}-\d{2})",
        ]
        deadlines: list[str] = []
        for pat in patterns:
            for match in re.finditer(pat, combined, re.IGNORECASE):
                d_str = match.group(1).strip()
                if d_str not in deadlines:
                    deadlines.append(d_str)
        return deadlines[:3]


class DigestGenerator:
    """Generates morning and evening summaries from emails and calendar."""

    def __init__(self, profile: MailProfile | None = None) -> None:
        self.profile = profile or MailProfile()
        self.classifier = MailClassifier(self.profile)

    async def generate_digest(self, period: str = "morning") -> dict[str, Any]:
        """Generate a complete structured digest."""
        now = datetime.now(UTC)
        since = (now - timedelta(hours=24)).isoformat()
        upcoming_until = (now + timedelta(days=2)).isoformat()

        # 1. Fetch recent emails
        emails: list[dict[str, Any]] = []
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, account, message_id, subject, from_name, from_domain,
                       date, text_sanitized
                FROM mail_items
                WHERE created_at >= ? AND deleted_at IS NULL
                ORDER BY created_at DESC
                """,
                (since,),
            ) as cur:
                rows = await cur.fetchall()
                for r in rows:
                    emails.append(
                        {
                            "id": r[0],
                            "account": r[1],
                            "message_id": r[2],
                            "subject": r[3] or "",
                            "from_name": r[4] or "",
                            "from_domain": r[5] or "",
                            "date": r[6] or "",
                            "text": r[7] or "",
                        }
                    )

        # 2. Fetch calendar events
        calendar_events: list[dict[str, Any]] = []
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, title, start_time, end_time, location, description, source
                FROM calendar_events
                WHERE start_time >= ? AND start_time <= ?
                ORDER BY start_time ASC
                """,
                (now.isoformat(), upcoming_until),
            ) as cur:
                rows = await cur.fetchall()
                for r in rows:
                    calendar_events.append(
                        {
                            "id": r[0],
                            "title": r[1],
                            "start_time": r[2],
                            "end_time": r[3],
                            "location": r[4],
                            "description": r[5],
                            "source": r[6],
                        }
                    )

        # 3. Categorize & process emails
        categorized: dict[str, list[dict[str, Any]]] = {}
        all_actions: list[dict[str, Any]] = []
        all_deadlines: list[dict[str, Any]] = []
        phishing_alerts: list[dict[str, Any]] = []

        for m in emails:
            cat = self.classifier.classify(m["subject"], m["text"], m["from_domain"])
            actions = self.classifier.extract_action_items(m["text"])
            deadlines = self.classifier.extract_deadlines(m["subject"], m["text"])
            phish = PhishingDetector.check_sender(
                m["from_name"], m["from_domain"], m["subject"], m["text"]
            )

            m_summary = {
                "id": m["id"],
                "subject": m["subject"],
                "from": m["from_name"],
                "domain": m["from_domain"],
                "category": cat,
                "date": m["date"],
            }
            categorized.setdefault(cat, []).append(m_summary)

            if actions:
                for act in actions:
                    all_actions.append({"item": act, "subject": m["subject"], "id": m["id"]})
            if deadlines:
                for dl in deadlines:
                    all_deadlines.append({"deadline": dl, "subject": m["subject"], "id": m["id"]})
            if phish["suspicious"]:
                phishing_alerts.append(
                    {
                        "subject": m["subject"],
                        "from": m["from_name"],
                        "domain": m["from_domain"],
                        "reasons": phish["reasons"],
                        "risk": phish["risk_level"],
                    }
                )

        return {
            "period": period,
            "generated_at": now.isoformat(),
            "total_emails_24h": len(emails),
            "calendar_events_count": len(calendar_events),
            "calendar_events": calendar_events,
            "categories_breakdown": {k: len(v) for k, v in categorized.items()},
            "categorized_emails": categorized,
            "action_items": all_actions,
            "deadlines": all_deadlines,
            "phishing_alerts": phishing_alerts,
        }
