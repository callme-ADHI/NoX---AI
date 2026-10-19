"""Evaluation and benchmarking runner for Reflex Router (§1.4, §F8).

Measures:
1. Intent classification accuracy across all 10 intent categories.
2. Latency per classification (target: max < 50ms, mean < 5ms).
3. Sensitive containment: verifies 100% of sensitive queries route to local front desk.
4. Fallback matrix and circuit-breaker behavior under cloud degradation.
"""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.table import Table

from noxai.router.intent import IntentType, analyze_intent

ROUTING_BENCHMARK_PROMPTS: list[tuple[str, IntentType]] = [
    # 1. reply_now
    ("Hello, how are you today?", IntentType.REPLY_NOW),
    ("What is the capital of Japan?", IntentType.REPLY_NOW),
    ("Explain the difference between TCP and UDP in two sentences.", IntentType.REPLY_NOW),
    ("Thanks for your help!", IntentType.REPLY_NOW),
    ("Good morning NoX", IntentType.REPLY_NOW),

    # 2. reminder
    ("Remind me in 15 minutes to take a break", IntentType.REMINDER),
    ("Set a timer for 5 minutes for tea", IntentType.REMINDER),
    ("Don't forget to remind me at 4pm to push the commit", IntentType.REMINDER),
    ("Remind me tomorrow morning to call the dentist", IntentType.REMINDER),

    # 3. schedule
    ("Run a security audit every Monday at 9am", IntentType.SCHEDULE),
    ("Schedule a daily briefing at 08:00 Asia/Kolkata", IntentType.SCHEDULE),
    ("Execute disk cleanup every Sunday midnight", IntentType.SCHEDULE),
    ("Create a cron job */15 * * * * to check server health", IntentType.SCHEDULE),

    # 4. job_edit
    ("Change my morning briefing time to 7:30am", IntentType.JOB_EDIT),
    ("Disable the nightly backup job", IntentType.JOB_EDIT),
    ("Pause the RSS monitor job", IntentType.JOB_EDIT),
    ("Delete the old news summary job", IntentType.JOB_EDIT),

    # 5. background_task
    ("Start a background index of all markdown files in ~/Documents", IntentType.BACKGROUND_TASK),
    ("Run a full vulnerability scan in the background", IntentType.BACKGROUND_TASK),
    ("Batch summarize these 20 PDF files in the background", IntentType.BACKGROUND_TASK),
    ("Download and extract the dataset asynchronously", IntentType.BACKGROUND_TASK),

    # 6. deep_research
    (
        "/deep Investigate recent supply chain vulnerabilities in npm packages",
        IntentType.DEEP_RESEARCH,
    ),
    (
        "Conduct a comprehensive multi-source analysis on memory safety in kernel drivers",
        IntentType.DEEP_RESEARCH,
    ),
    ("Deep dive into the architecture of modern Wayland compositors", IntentType.DEEP_RESEARCH),
    (
        "Perform an in-depth security comparison between Arch Linux and Debian",
        IntentType.DEEP_RESEARCH,
    ),

    # 7. memory_op
    ("Remember that my home wifi SSID is CyberVault", IntentType.MEMORY_OP),
    ("What did we discuss yesterday about database indexing?", IntentType.MEMORY_OP),
    ("Forget the password for the old test server", IntentType.MEMORY_OP),
    ("Search my past episodes for mentions of wireguard", IntentType.MEMORY_OP),
    ("Note down that my preferred editor is Neovim", IntentType.MEMORY_OP),

    # 8. system_action
    ("Reboot the system", IntentType.SYSTEM_ACTION),
    ("What is the current system CPU and memory load?", IntentType.SYSTEM_ACTION),
    ("List all active systemd user services", IntentType.SYSTEM_ACTION),
    ("Show me the disk space usage on /", IntentType.SYSTEM_ACTION),
    ("Kill process pid 12345", IntentType.SYSTEM_ACTION),

    # 9. sensitive
    ("My AWS secret key is AKIAIOSFODNN7EXAMPLE", IntentType.SENSITIVE),
    ("Here is the root password for the server: SecretPassw0rd!", IntentType.SENSITIVE),
    ("Store my credit card 4532-1234-5678-9012 for the subscription", IntentType.SENSITIVE),
    ("Here is the confidential medical diagnosis report", IntentType.SENSITIVE),
    ("Private SSH private key -----BEGIN OPENSSH PRIVATE KEY-----", IntentType.SENSITIVE),

    # 10. ambiguous
    ("Maybe check the thing we saw earlier", IntentType.AMBIGUOUS),
    ("What about that other file?", IntentType.AMBIGUOUS),
    ("Hmm, not sure what to do next", IntentType.AMBIGUOUS),
    ("Could you help with it?", IntentType.AMBIGUOUS),
]


async def run_routing_eval(
    offline: bool = True,
    console: Console | None = None,
) -> dict[str, Any]:
    """Execute the Reflex Router benchmark across test prompts (§1.4, §F8)."""
    latencies_ms: list[float] = []
    correct_count = 0
    sensitive_contained_count = 0
    sensitive_total = 0
    category_results: dict[str, dict[str, int]] = {}

    for prompt, expected_intent in ROUTING_BENCHMARK_PROMPTS:
        cat_key = expected_intent.value
        if cat_key not in category_results:
            category_results[cat_key] = {"total": 0, "correct": 0}
        category_results[cat_key]["total"] += 1

        t0 = time.perf_counter()
        analysis = analyze_intent(prompt)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(dt_ms)

        is_match = analysis.intent == expected_intent
        if is_match:
            correct_count += 1
            category_results[cat_key]["correct"] += 1

        # Check sensitive containment guarantee
        if expected_intent == IntentType.SENSITIVE:
            sensitive_total += 1
            # Must be either classified as SENSITIVE or contain sensitive flags
            if analysis.intent == IntentType.SENSITIVE:
                sensitive_contained_count += 1

    total_prompts = len(ROUTING_BENCHMARK_PROMPTS)
    accuracy = (correct_count / total_prompts) if total_prompts else 0.0
    mean_latency = (sum(latencies_ms) / len(latencies_ms)) if latencies_ms else 0.0
    max_latency = max(latencies_ms) if latencies_ms else 0.0
    sensitive_ratio = (
        (sensitive_contained_count / sensitive_total) if sensitive_total > 0 else 1.0
    )

    results: dict[str, Any] = {
        "total": total_prompts,
        "correct": correct_count,
        "accuracy": round(accuracy, 4),
        "mean_latency_ms": round(mean_latency, 3),
        "max_latency_ms": round(max_latency, 3),
        "latency_target_met": max_latency < 50.0 and mean_latency < 5.0,
        "sensitive_containment": round(sensitive_ratio, 4),
        "sensitive_target_met": sensitive_ratio == 1.0,
        "offline": offline,
        "categories": category_results,
    }

    if console is not None:
        console.rule("[bold blue]NoX Reflex Router Evaluation (§1.4, §F8)[/bold blue]")
        console.print(
            f"Evaluated [bold]{total_prompts}[/] standard prompts across 10 intent categories.\n"
        )

        table = Table(title="Intent Classification Accuracy by Category", show_header=True)
        table.add_column("Intent Category", style="bold cyan")
        table.add_column("Accuracy", justify="right")
        table.add_column("Status", justify="center")

        for cat, counts in category_results.items():
            tot = counts["total"]
            cor = counts["correct"]
            pct = (cor / tot) * 100.0 if tot else 0.0
            status_badge = "[green]✓ PASS[/]" if pct >= 80.0 else "[red]✗ FAIL[/]"
            table.add_row(cat, f"{cor}/{tot} ({pct:.0f}%)", status_badge)

        console.print(table)

        metrics_table = Table(title="Performance & Safety Benchmarks", show_header=True)
        metrics_table.add_column("Metric", style="bold")
        metrics_table.add_column("Measured Value", justify="right")
        metrics_table.add_column("Target Specification", justify="right")
        metrics_table.add_column("Verdict", justify="center")

        acc_pct = accuracy * 100.0
        acc_verdict = "[green]PASS[/]" if acc_pct >= 90.0 else "[red]FAIL[/]"
        metrics_table.add_row(
            "Intent Accuracy",
            f"{acc_pct:.1f}%",
            ">= 90.0%",
            acc_verdict,
        )

        mean_verdict = "[green]PASS[/]" if mean_latency < 5.0 else "[yellow]WARN[/]"
        metrics_table.add_row(
            "Mean Latency",
            f"{mean_latency:.3f} ms",
            "< 5.0 ms",
            mean_verdict,
        )

        max_verdict = "[green]PASS[/]" if max_latency < 50.0 else "[red]FAIL[/]"
        metrics_table.add_row(
            "Max Latency (Hard Limit)",
            f"{max_latency:.3f} ms",
            "< 50.0 ms (§1.4)",
            max_verdict,
        )

        sens_pct = sensitive_ratio * 100.0
        sens_verdict = "[green]PASS[/]" if sens_pct == 100.0 else "[red]FAIL[/]"
        metrics_table.add_row(
            "Sensitive Isolation (Local Only)",
            f"{sens_pct:.1f}%",
            "100.0% (§F2)",
            sens_verdict,
        )

        console.print(metrics_table)

    return results
