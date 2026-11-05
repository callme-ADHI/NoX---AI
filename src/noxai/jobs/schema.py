"""Schema definitions for Planned Jobs (Phase 1)."""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class StepKind(str, Enum):
    FS_SCAN_CHANGES = "fs.scan_changes"
    SCAN_SECRETS = "scan.secrets"
    SCAN_LARGE_FILES = "scan.large_files"
    GIT_PREFLIGHT = "git.preflight"
    GIT_ENSURE_REPO = "git.ensure_repo"
    GIT_VERIFY_REMOTE_PRIVATE = "git.verify_remote_private"
    GIT_STAGE = "git.stage"
    GIT_COMMIT = "git.commit"
    GIT_PUSH = "git.push"
    GIT_VERIFY_PUSHED = "git.verify_pushed"
    NOTIFY = "notify"


WHITELISTED_STEP_KINDS = {k.value for k in StepKind}


class StepRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_base_seconds: float = Field(default=2.0, ge=0.5, le=60.0)
    max_backoff_seconds: float = Field(default=300.0, ge=1.0, le=1800.0)


class JobStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_index: int = Field(..., ge=1)
    kind: str
    folder_alias: str = Field(default="", description="Human-friendly alias, e.g. Certificates")
    folder_path: str = Field(..., description="Target directory path or placeholder")
    remote: str | None = Field(default=None, description="Remote Git URL or placeholder")
    timeout_seconds: int = Field(default=60, ge=5, le=1800)
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    params: dict[str, Any] = Field(default_factory=dict)


class JobPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    version: int = Field(default=1, ge=1)
    source: str = Field(..., pattern="^(cloud|template)$")
    status: str = Field(default="draft", pattern="^(draft|approved|superseded)$")
    description: str = Field(default="")
    steps: list[JobStep] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
