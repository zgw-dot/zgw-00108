"""Core business logic service layer."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional, Tuple

from .models import (
    ApprovalPolicy,
    AuditLog,
    ChecklistItem,
    ChecklistSetResult,
    ChecklistUpdateResult,
    MaintenanceWindow,
    PolicyUpdateResult,
    RepairTask,
    Role,
    TaskStatus,
)
from .persistence import (
    AuditRepository,
    ChecklistRepository,
    Database,
    PolicyRepository,
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
        self.policy_repo = PolicyRepository(db)
        self.checklist_repo = ChecklistRepository(db)
        self.validator = Validator(
            self.task_repo,
            self.window_repo,
            self.role_repo,
            self.policy_repo,
        )
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
        task = self.task_repo.get(task_id)
        try:
            task = self.validator.validate_rollback(task_id, actor)
        except ValidationError as e:
            if task:
                self._audit(
                    task_id=task_id,
                    action="task_rollback_rejected",
                    actor=actor,
                    old_status=task.status.value,
                    new_status=task.status.value,
                    details=e.message,
                )
            raise
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

    def get_policy(self) -> ApprovalPolicy:
        return self.policy_repo.get()

    def update_policy(
        self,
        actor: str,
        allow_admin_self_approval: Optional[bool] = None,
        require_different_approver: Optional[bool] = None,
    ) -> PolicyUpdateResult:
        if not self.role_repo.has_role(actor, Role.ADMIN):
            self._audit(
                task_id=None,
                action="policy_update_denied",
                actor=actor,
                details=f"User '{actor}' attempted to update policy without admin permission",
            )
            raise ValidationError(
                f"User '{actor}' does not have admin role to update policy",
                code="policy_update_denied",
            )

        old_policy = self.policy_repo.get()
        changed_fields: list[str] = []

        if allow_admin_self_approval is not None and old_policy.allow_admin_self_approval != allow_admin_self_approval:
            changed_fields.append("allow_admin_self_approval")
        if require_different_approver is not None and old_policy.require_different_approver != require_different_approver:
            changed_fields.append("require_different_approver")

        new_policy = self.policy_repo.update(
            allow_admin_self_approval=allow_admin_self_approval,
            require_different_approver=require_different_approver,
            updated_by=actor,
        )

        self._audit(
            task_id=None,
            action="policy_updated",
            actor=actor,
            details=f"Policy updated. Changed fields: {', '.join(changed_fields) if changed_fields else 'none'}. "
                    f"Old: allow_admin_self_approval={old_policy.allow_admin_self_approval}, "
                    f"require_different_approver={old_policy.require_different_approver}. "
                    f"New: allow_admin_self_approval={new_policy.allow_admin_self_approval}, "
                    f"require_different_approver={new_policy.require_different_approver}",
        )

        return PolicyUpdateResult(
            old_policy=old_policy,
            new_policy=new_policy,
            changed_fields=changed_fields,
        )

    def _can_modify_checklist(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        user_role = self.role_repo.get_role(actor)
        if user_role not in (Role.OPERATOR, Role.ADMIN):
            self._audit(
                task_id=task_id,
                action="checklist_update_denied",
                actor=actor,
                details=f"User '{actor}' attempted to update checklist without operator permission",
            )
            raise ValidationError(
                f"User '{actor}' does not have operator role to update checklist",
                code="checklist_permission_denied",
            )

        if task.created_by != actor and user_role != Role.ADMIN:
            self._audit(
                task_id=task_id,
                action="checklist_update_denied",
                actor=actor,
                details=f"User '{actor}' attempted to update checklist for task created by '{task.created_by}'",
            )
            raise ValidationError(
                f"User '{actor}' cannot update checklist for task created by '{task.created_by}'",
                code="checklist_not_owner",
            )

        return task

    def set_checklist(self, task_id: int, items: list[ChecklistItem], actor: str) -> ChecklistSetResult:
        self._can_modify_checklist(task_id, actor)

        for item in items:
            if not item.name or not item.name.strip():
                raise ValidationError("Checklist item name cannot be empty", code="invalid_checklist_item")

        created, updated = self.checklist_repo.set_items(task_id, items)

        self._audit(
            task_id=task_id,
            action="checklist_set",
            actor=actor,
            details=f"Checklist set: {created} created, {updated} updated. Items: {[i.name for i in items]}",
        )

        return ChecklistSetResult(
            task_id=task_id,
            items_created=created,
            items_updated=updated,
        )

    def get_checklist(self, task_id: int, actor: str) -> list[ChecklistItem]:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        user_role = self.role_repo.get_role(actor)
        if user_role not in (Role.OPERATOR, Role.APPROVER, Role.ADMIN):
            self._audit(
                task_id=task_id,
                action="checklist_view_denied",
                actor=actor,
                details=f"User '{actor}' does not have permission to view checklist (role={user_role})",
            )
            raise ValidationError(
                f"User '{actor}' does not have permission to view checklist",
                code="checklist_view_denied",
            )

        return self.checklist_repo.get_by_task(task_id)

    def update_checklist_item(
        self,
        task_id: int,
        item_id: int,
        actor: str,
        completed: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> ChecklistUpdateResult:
        task = self._can_modify_checklist(task_id, actor)

        item = self.checklist_repo.get(item_id)
        if not item:
            self._audit(
                task_id=task_id,
                action="checklist_update_failed",
                actor=actor,
                details=f"Checklist item {item_id} not found",
            )
            raise ValidationError(f"Checklist item {item_id} not found", code="checklist_item_not_found")

        if item.task_id != task_id:
            self._audit(
                task_id=task_id,
                action="checklist_update_failed",
                actor=actor,
                details=f"Checklist item {item_id} does not belong to task {task_id}",
            )
            raise ValidationError(
                f"Checklist item {item_id} does not belong to task {task_id}",
                code="checklist_item_mismatch",
            )

        old_completed = item.completed
        new_completed = completed if completed is not None else old_completed

        updated = self.checklist_repo.update_item(
            item_id=item_id,
            actor=actor,
            completed=completed,
            notes=notes,
        )

        if not updated:
            self._audit(
                task_id=task_id,
                action="checklist_update_failed",
                actor=actor,
                details=f"Concurrent modification when updating checklist item {item_id}",
            )
            raise ValidationError(
                f"Checklist item {item_id} changed during update",
                code="concurrent_modification",
            )

        actual_old = getattr(updated, '_old_completed', old_completed)
        actual_new = updated.completed

        changes = []
        if completed is not None and actual_old != actual_new:
            changes.append(f"completed: {actual_old} -> {actual_new}")
        if notes is not None:
            changes.append(f"notes updated")

        self._audit(
            task_id=task_id,
            action="checklist_updated",
            actor=actor,
            details=f"Checklist item '{item.name}' updated: {', '.join(changes) if changes else 'no changes'}",
        )

        return ChecklistUpdateResult(
            task_id=task_id,
            item_id=item_id,
            old_value=actual_old,
            new_value=actual_new,
            updated_by=actor,
        )

    def _validate_checklist_required(self, task_id: int, action: str) -> None:
        incomplete = self.checklist_repo.get_incomplete_required(task_id)
        if incomplete:
            names = [f"'{i.name}'" for i in incomplete]
            raise ValidationError(
                f"Cannot {action} task {task_id}: required checklist items not completed: {', '.join(names)}",
                code="checklist_incomplete",
            )

    def submit_for_approval(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_submit_for_approval(task_id, actor)
        self._validate_checklist_required(task_id, "submit")
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

    def run_task(self, task_id: int, actor: str) -> RepairTask:
        task = self.validator.validate_run(task_id, actor)
        self._validate_checklist_required(task_id, "run")
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
