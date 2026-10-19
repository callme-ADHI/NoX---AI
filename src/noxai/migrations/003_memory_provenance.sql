-- 003_memory_provenance.sql
-- Add provenance and review columns to facts and episodes

ALTER TABLE facts ADD COLUMN provenance TEXT NOT NULL DEFAULT 'user_stated'
    CHECK(provenance IN ('user_stated','agent_derived','manual'));
ALTER TABLE facts ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0;
ALTER TABLE episodes ADD COLUMN untrusted_origin INTEGER NOT NULL DEFAULT 0;
