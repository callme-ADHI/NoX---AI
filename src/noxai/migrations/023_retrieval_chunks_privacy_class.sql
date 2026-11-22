-- Migration 023: Retrieval Chunks Privacy Class and Taint Tagging (C-8)
-- Ensures chunks and documents tables store privacy_class explicitly as 'local_only'.

ALTER TABLE documents ADD COLUMN privacy_class TEXT NOT NULL DEFAULT 'local_only';
ALTER TABLE chunks ADD COLUMN privacy_class TEXT NOT NULL DEFAULT 'local_only';
