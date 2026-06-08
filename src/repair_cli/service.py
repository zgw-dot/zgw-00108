"""Core business logic service layer."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional, Tuple

from .models import (
    AuditLog,
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from .persistence import (
    AuditRepository,
    Database,
    RoleRepository,
    TaskRepository,
    WindowRepository,
)
from .validation import ValidationError, Validator


class RepairService:
    def __init__(self, db: Database, sql_executor: Optional[Callable[[str], Tuple[bool, str]]] = None):
        self.db = db
        self.task_repo = TaskRepository(db)
        self.window_repo = WindowRepository(db)
        self.audit_repo = AuditRepository(db)
        self.role_repo = RoleRepository(db)
        self.validator = Validator(self.task_repo, self.window_repo, self.role_repo)
        self.sql_executor = sql_executor or self._default_sql_executor

    @staticmethod
    def _default_sql_executor(sql: str) -> Tuple[bool, str]:
        return True, f"Executed: {sql[:50]}..."

    def _audit(self, task_id: Optional[int], action: str, actor: str,
               old_status: Optional[str] = None, new_status: Optional[str] = None,
               details: Optional[str] = None) -> None:
        self.audit_repo.log(AuditLog(
            task_id=task_id,
            action=action,
            actor=actor,
            old_status=old_status,
            new_status=new_status,
            details=details,
        ))

    def create_window(self, window: MaintenanceWindow) -> MaintenanceWindow:
        self.validator.validate_window_creation(window)
        created = self.window_repo.create(window)
        self._audit(
            task_id=None,
            action="window_created",
            actor=window.created_by,
            details=f"Window '{window.name}' created from {window.start_time} to {window.end_time}",
        )
        return created

    def update_window(self, window_id: int, actor: str, **kwargs) -> MaintenanceWindow:
        self.validator.validate_window_update(
            window_id,
            new_start_time=kwargs.get("start_time"),
            new_end_time=kwargs.get("end_time"),
            new_name=kwargs.get("name"),
        )
        updated = self.window_repo.update(window_id, **kwargs)
        if not updated:
            raise ValidationError(f"Window {window_id} not found", code="window_not_found")
        self._audit(
            task_id=None,
            action="window_updated",
            actor=actor,
            details=f"Window {window_id} updated: {kwargs}",
        )
        return updated

    def create_task(self, task: RepairTask) -> RepairTask:
        self.validator.validate_task_creation(task)
        created = self.task_repo.create(task)
        self._audit(
            task_id=created.id,
            action="task_created",
            actor=task.created_by,
            old_status=None,
            new_status=created.status.value,
            details=f"Task '{task.name}' created",
        )
        return created

    def submit_for_approval(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_submit_for_approval(task_id, actor)
        old_status = task.status.value
        updated = self.task_repo.update_status(
            task_id,
            old_status=task.status,
            new_status=TaskStatus.PENDING_APPROVAL,
        )
        if not updated:
            raise ValidationError(
                f"Task {task_id} status changed during validation",
                code="concurrent_modification",
            )
        self._audit(
            task_id=task_id,
            action="task_submitted",
            actor=actor,
            old_status=old_status,
            new_status=TaskStatus.PENDING_APPROVAL.value,
        )
        return updated

    def approve_task(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_approve(task_id, actor)
        old_status = task.status.value
        now = datetime.utcnow()
        updated = self.task_repo.update_status(
            task_id,
            old_status=task.status,
            new_status=TaskStatus.APPROVED,
            approved_by=actor,
            approved_at=now,
        )
        if not updated:
            raise ValidationError(
                f"Task {task_id} status changed during validation",
                code="concurrent_modification",
            )
        self._audit(
            task_id=task_id,
            action="task_approved",
            actor=actor,
            old_status=old_status,
            new_status=TaskStatus.APPROVED.value,
        )
        return updated

    def reject_task(self, task_id: int, actor: str, reason: str = "") -> RepairTask:
        task = self.validator.validate_reject(task_id, actor)
        old_status = task.status.value
        updated = self.task_repo.update_status(
            task_id,
            old_status=task.status,
            new_status=TaskStatus.REJECTED,
        )
        if not updated:
            raise ValidationError(
                f"Task {task_id} status changed during validation",
                code="concurrent_modification",
            )
        self._audit(
            task_id=task_id,
            action="task_rejected",
            actor=actor,
            old_status=old_status,
            new_status=TaskStatus.REJECTED.value,
            details=reason or None,
        )
        return updated

    def run_task(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_run(task_id, actor)
        old_status = task.status.value
        now = datetime.utcnow()

        updated = self.task_repo.update_status(
            task_id,
            old_status=task.status,
            new_status=TaskStatus.RUNNING,
            executed_by=actor,
        )
        if not updated:
            raise ValidationError(
                f"Task {task_id} status changed during validation",
                code="concurrent_modification",
            )
        self._audit(
            task_id=task_id,
            action="task_run_started",
            actor=actor,
            old_status=old_status,
            new_status=TaskStatus.RUNNING.value,
        )

        try:
            success, result = self.sql_executor(task.sql)
            if success:
                final_status = TaskStatus.SUCCEEDED
                action = "task_run_succeeded"
            else:
                final_status = TaskStatus.FAILED
                action = "task_run_failed"

            updated = self.task_repo.update_fields(
                task_id,
                status=final_status.value,
                executed_at=now,
            )
            self._audit(
                task_id=task_id,
                action=action,
                actor=actor,
                old_status=TaskStatus.RUNNING.value,
                new_status=final_status.value,
                details=result,
            )
            return updated
        except Exception as e:
            updated = self.task_repo.update_fields(
                task_id,
                status=TaskStatus.FAILED.value,
                executed_at=now,
            )
            self._audit(
                task_id=task_id,
                action="task_run_failed",
                actor=actor,
                old_status=TaskStatus.RUNNING.value,
                new_status=TaskStatus.FAILED.value,
                details=str(e),
            )
            raise

    def rollback_task(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_rollback(task_id, actor)
        old_status = task.status.value
        now = datetime.utcnow()

        updated = self.task_repo.update_status(
            task_id,
            old_status=task.status,
            new_status=TaskStatus.ROLLBACK_RUNNING,
        )
        if not updated:
            raise ValidationError(
                f"Task {task_id} status changed during validation",
                code="concurrent_modification",
            )
        self._audit(
            task_id=task_id,
            action="task_rollback_started",
            actor=actor,
            old_status=old_status,
            new_status=TaskStatus.ROLLBACK_RUNNING.value,
        )

        try:
            assert task.rollback_sql is not None
            success, result = self.sql_executor(task.rollback_sql)
            if success:
                final_status = TaskStatus.ROLLBACK_SUCCEEDED
                action = "task_rollback_succeeded"
            else:
                final_status = TaskStatus.ROLLBACK_FAILED
                action = "task_rollback_failed"

            updated = self.task_repo.update_fields(
                task_id,
                status=final_status.value,
                rollback_at=now,
                rollback_by=actor,
            )
            self._audit(
                task_id=task_id,
                action=action,
                actor=actor,
                old_status=TaskStatus.ROLLBACK_RUNNING.value,
                new_status=final_status.value,
                details=result,
            )
            return updated
        except Exception as e:
            updated = self.task_repo.update_fields(
                task_id,
                status=TaskStatus.ROLLBACK_FAILED.value,
                rollback_at=now,
                rollback_by=actor,
            )
            self._audit(
                task_id=task_id,
                action="task_rollback_failed",
                actor=actor,
                old_status=TaskStatus.ROLLBACK_RUNNING.value,
                new_status=TaskStatus.ROLLBACK_FAILED.value,
                details=str(e),
            )
            raise

    def set_user_role(self, username: str, role: Role, actor: str) -> None:
        if not self.role_repo.has_role(actor, Role.ADMIN):
            raise ValidationError(
                f"User '{actor}' does not have admin role to set roles",
                code="permission_denied",
            )
        self.role_repo.set_role(username, role)
        self._audit(
            task_id=None,
            action="role_set",
            actor=actor,
            details=f"Set role '{role.value}' for user '{username}'",
        )
