-- 004_trace_json.sql
-- Add trace_json column to runs table for per-stage latency telemetry

ALTER TABLE runs ADD COLUMN trace_json TEXT;
