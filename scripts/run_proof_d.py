"""Execute LIVE PROOF D: real content briefing run on real network."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("proof_d")

TEMP_DIR = Path("/tmp/nox_proof_d")
if TEMP_DIR.exists():
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = TEMP_DIR / "data"
CONFIG_DIR = TEMP_DIR / "config"
VAULT_DIR = TEMP_DIR / "vault"
BRIEFINGS_DIR = TEMP_DIR / "briefings"
STAGING_DIR = TEMP_DIR / "staging"

for d in (DATA_DIR, CONFIG_DIR, VAULT_DIR, BRIEFINGS_DIR, STAGING_DIR):
    d.mkdir(parents=True, exist_ok=True)

os.environ["NOX_DATA_DIR"] = str(DATA_DIR)
os.environ["NOX_CONFIG_DIR"] = str(CONFIG_DIR)

import noxai.db as db_mod
import noxai.briefing.engine as engine_mod
from noxai.config import get_config
from noxai.db import init_db, get_db, fetch_one, fetch_all, execute
from noxai.briefing.engine import BriefingPipeline
from noxai.notes.vault import NotesVault
from noxai.research.crawler import WebResearcher

db_mod.DB_PATH = DATA_DIR / "nox_proof.db"
engine_mod.BRIEFINGS_DIR = BRIEFINGS_DIR
engine_mod.STAGING_DIR = STAGING_DIR

async def main():
    await init_db()
    cfg = get_config()
    cfg.notes.vault_path = str(VAULT_DIR)
    
    pipeline = BriefingPipeline(config=cfg)
    vault = NotesVault(vault_path=VAULT_DIR, config=cfg)
    
    log.info("Starting Run 1 (morning)...")
    t0 = time.time()
    run_1_id = await pipeline.execute_run(slot="morning", date_str="2026-11-02", force=True)
    t1 = time.time()
    log.info("Run 1 completed in %.2fs with ID: %s", t1 - t0, run_1_id)
    
    async with get_db() as db:
        r1 = await fetch_one(db, "SELECT * FROM briefing_runs WHERE id = ?", (run_1_id,))
        items1 = await fetch_all(db, "SELECT * FROM briefing_items WHERE briefing_id = ?", (run_1_id,))
        reg1 = await fetch_all(db, "SELECT * FROM opportunity_registry")

    print("\n" + "="*80)
    print(f"RUN 1 STATE: {r1['state']} (items: {len(items1)}, sources: {r1['sources_count']})")
    print(f"OPPORTUNITY REGISTRY ITEMS: {len(reg1)}")
    print("="*80)

    # Run 2 for carryover verification
    log.info("Starting Run 2 (evening) for carry-over and duplicate testing...")
    t2 = time.time()
    run_2_id = await pipeline.execute_run(slot="evening", date_str="2026-11-02", force=True)
    t3 = time.time()
    log.info("Run 2 completed in %.2fs with ID: %s", t3 - t2, run_2_id)

    async with get_db() as db:
        r2 = await fetch_one(db, "SELECT * FROM briefing_runs WHERE id = ?", (run_2_id,))
        items2 = await fetch_all(db, "SELECT * FROM briefing_items WHERE briefing_id = ?", (run_2_id,))
        reg2 = await fetch_all(db, "SELECT * FROM opportunity_registry")

    print("\n" + "="*80)
    print(f"RUN 2 STATE: {r2['state']} (items: {len(items2)}, sources: {r2['sources_count']})")
    print(f"OPPORTUNITY REGISTRY ITEMS AFTER RUN 2: {len(reg2)}")
    print("="*80)

    # URL Verification Table for Run 1 items
    researcher = WebResearcher(config=cfg)
    url_results = []
    print("\nVerifying all URLs from Run 1...")
    for it in items1:
        u = it["url"]
        start_u = time.time()
        ok, code, reason = await researcher.verify_link(u, known_fetched=True)
        dur_ms = int((time.time() - start_u) * 1000)
        url_results.append({
            "title": it["title"],
            "category": it["category"],
            "url": u,
            "status_code": code,
            "reason": reason,
            "time_ms": dur_ms,
        })

    out = {
        "run_1_id": run_1_id,
        "run_1_state": r1["state"],
        "run_1_seconds": r1["generation_seconds"],
        "run_1_items_count": len(items1),
        "run_2_id": run_2_id,
        "run_2_state": r2["state"],
        "run_2_seconds": r2["generation_seconds"],
        "run_2_items_count": len(items2),
        "registry_count_r1": len(reg1),
        "registry_count_r2": len(reg2),
        "urls": url_results,
    }
    with open(TEMP_DIR / "proof_d_summary.json", "w") as f:
        json.dump(out, f, indent=2)

    log.info("Proof D finished successfully.")

if __name__ == "__main__":
    asyncio.run(main())
