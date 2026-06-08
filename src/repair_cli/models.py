"""Data models for repair scheduling system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLBACK_RUNNING = "rollback_running"
    ROLLBACK_SUCCEEDED = "rollback_succeeded"
    ROLLBACK_FAILED = "rollback_failed"


class Role(str, Enum):
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class RepairTask(BaseModel):
    id: Optional[int] = None
    name: str
    description: str
    created_by: str
    sql: str
    rollback_sql: Optional[str] = None
    window_id: int
    status: TaskStatus = TaskStatus.DRAFT
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    executed_by: Optional[str] = None
    rollback_at: Optional[datetime] = None
    rollback_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("name", "description", "sql", "created_by")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Cannot be empty")
        return v.strip()


class MaintenanceWindow(BaseModel):
    id: Optional[int] = None
    name: str
    description: str = ""
    start_time: datetime
    end_time: datetime
    created_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("name", "created_by")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Cannot be empty")
        return v.strip()


class AuditLog(BaseModel):
    id: Optional[int] = None
    task_id: Optional[int] = None
    action: str
    actor: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RoleRule(BaseModel):
    id: Optional[int] = None
    username: str
    role: Role
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("username")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Cannot be empty")
        return v.strip()


class ApprovalPolicy(BaseModel):
    id: Optional[int] = None
    allow_admin_self_approval: bool = True
    require_different_approver: bool = True
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PolicyUpdateResult(BaseModel):
    old_policy: ApprovalPolicy
    new_policy: ApprovalPolicy
    changed_fields: list[str]
