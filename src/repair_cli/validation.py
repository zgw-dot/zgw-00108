"""Business rule validation layer."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .models import (
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from .persistence import (
    RoleRepository,
    TaskRepository,
    WindowRepository,
)


class ValidationError(Exception):
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or "validation_error"
        self.message = message


class Validator:
    def __init__(
        self,
        task_repo: TaskRepository,
        window_repo: WindowRepository,
        role_repo: RoleRepository,
    ):
        self.task_repo = task_repo
        self.window_repo = window_repo
        self.role_repo = role_repo

    def validate_window_creation(self, window: MaintenanceWindow) -> None:
        if window.end_time <= window.start_time:
            raise ValidationError(
                f"Window end time ({window.end_time}) must be after start time ({window.start_time})",
                code="invalid_time_range",
            )

        duration = window.end_time - window.start_time
        if duration.total_seconds() < 60:
            raise ValidationError(
                f"Window duration must be at least 60 seconds (got {duration.total_seconds()}s)",
                code="window_too_short",
            )

        existing = self.window_repo.get_by_name(window.name)
        if existing:
            raise ValidationError(
                f"Window name '{window.name}' already exists (id={existing.id})",
                code="duplicate_window_name",
            )

        overlapping = self.window_repo.get_all_overlapping(window.start_time, window.end_time)
        if overlapping:
            names = [f"'{w.name}'(id={w.id})" for w in overlapping]
            raise ValidationError(
                f"Window overlaps with existing windows: {', '.join(names)}",
                code="window_overlap",
            )

    def validate_window_update(
        self,
        window_id: int,
        new_start_time: Optional[datetime] = None,
        new_end_time: Optional[datetime] = None,
        new_name: Optional[str] = None,
    ) -> None:
        existing = self.window_repo.get(window_id)
        if not existing:
            raise ValidationError(f"Window {window_id} not found", code="window_not_found")

        start_time = new_start_time or existing.start_time
        end_time = new_end_time or existing.end_time
        name = new_name or existing.name

        if end_time <= start_time:
            raise ValidationError(
                f"Window end time ({end_time}) must be after start time ({start_time})",
                code="invalid_time_range",
            )

        if new_name and new_name != existing.name:
            other = self.window_repo.get_by_name(new_name)
            if other:
                raise ValidationError(
                    f"Window name '{new_name}' already exists (id={other.id})",
                    code="duplicate_window_name",
                )

        if new_start_time or new_end_time:
            overlapping = self.window_repo.get_all_overlapping(
                start_time, end_time, exclude_id=window_id
            )
            if overlapping:
                names = [f"'{w.name}'(id={w.id})" for w in overlapping]
                raise ValidationError(
                    f"Window overlaps with existing windows: {', '.join(names)}",
                    code="window_overlap",
                )

        if new_start_time or new_end_time:
            tasks = self.task_repo.list_by_window(window_id)
            for task in tasks:
                if task.status in (
                    TaskStatus.PENDING_APPROVAL,
                    TaskStatus.APPROVED,
                    TaskStatus.RUNNING,
                ):
                    raise ValidationError(
                        f"Cannot modify window {window_id}: task {task.id} is in state {task.status.value}",
                        code="window_locked_by_active_task",
                    )

    def validate_task_creation(self, task: RepairTask) -> None:
        window = self.window_repo.get(task.window_id)
        if not window:
            raise ValidationError(
                f"Window {task.window_id} does not exist",
                code="window_not_found",
            )

    def validate_submit_for_approval(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        if task.status != TaskStatus.DRAFT:
            raise ValidationError(
                f"Task {task_id} is in state {task.status.value}, must be 'draft' to submit",
                code="invalid_state_transition",
            )

        if not self.role_repo.has_role(actor, Role.OPERATOR):
            raise ValidationError(
                f"User '{actor}' does not have operator role to submit tasks",
                code="permission_denied",
            )

        window = self.window_repo.get(task.window_id)
        if not window:
            raise ValidationError(
                f"Task references non-existent window {task.window_id}",
                code="window_not_found",
            )

        if window.end_time < datetime.utcnow():
            raise ValidationError(
                f"Window '{window.name}' has already ended",
                code="window_expired",
            )

        return task

    def validate_approve(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        if task.status != TaskStatus.PENDING_APPROVAL:
            raise ValidationError(
                f"Task {task_id} is in state {task.status.value}, must be 'pending_approval' to approve",
                code="invalid_state_transition",
            )

        if not self.role_repo.has_role(actor, Role.APPROVER):
            raise ValidationError(
                f"User '{actor}' does not have approver role",
                code="permission_denied",
            )

        if task.created_by == actor and not self.role_repo.has_role(actor, Role.ADMIN):
            raise ValidationError(
                f"User '{actor}' cannot approve their own task (created_by={task.created_by})",
                code="self_approval_not_allowed",
            )

        window = self.window_repo.get(task.window_id)
        if not window:
            raise ValidationError(
                f"Task references non-existent window {task.window_id}",
                code="window_not_found",
            )

        if window.end_time < datetime.utcnow():
            raise ValidationError(
                f"Window '{window.name}' has already ended",
                code="window_expired",
            )

        return task

    def validate_reject(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        if task.status != TaskStatus.PENDING_APPROVAL:
            raise ValidationError(
                f"Task {task_id} is in state {task.status.value}, must be 'pending_approval' to reject",
                code="invalid_state_transition",
            )

        if not self.role_repo.has_role(actor, Role.APPROVER):
            raise ValidationError(
                f"User '{actor}' does not have approver role",
                code="permission_denied",
            )

        if task.created_by == actor:
            raise ValidationError(
                f"User '{actor}' cannot reject their own task",
                code="self_rejection_not_allowed",
            )

        return task

    def validate_run(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        if task.status != TaskStatus.APPROVED:
            raise ValidationError(
                f"Task {task_id} is in state {task.status.value}, must be 'approved' to run",
                code="invalid_state_transition",
            )

        if not self.role_repo.has_role(actor, Role.OPERATOR):
            raise ValidationError(
                f"User '{actor}' does not have operator role to run tasks",
                code="permission_denied",
            )

        window = self.window_repo.get(task.window_id)
        if not window:
            raise ValidationError(
                f"Task references non-existent window {task.window_id}",
                code="window_not_found",
            )

        now = datetime.utcnow()
        if now < window.start_time:
            raise ValidationError(
                f"Window '{window.name}' has not started yet (starts at {window.start_time})",
                code="window_not_started",
            )
        if now > window.end_time:
            raise ValidationError(
                f"Window '{window.name}' has already ended (ended at {window.end_time})",
                code="window_expired",
            )

        return task

    def validate_rollback(self, task_id: int, actor: str) -> RepairTask:
        task = self.task_repo.get(task_id)
        if not task:
            raise ValidationError(f"Task {task_id} not found", code="task_not_found")

        if task.status not in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            raise ValidationError(
                f"Task {task_id} is in state {task.status.value}, must be 'succeeded' or 'failed' to rollback",
                code="invalid_state_transition",
            )

        if not task.rollback_sql:
            raise ValidationError(
                f"Task {task_id} has no rollback SQL configured",
                code="no_rollback_sql",
            )

        if not self.role_repo.has_role(actor, Role.OPERATOR):
            raise ValidationError(
                f"User '{actor}' does not have operator role to rollback tasks",
                code="permission_denied",
            )

        return task

    def validate_task_window_reference(
        self, task_data: dict, existing_windows: List[MaintenanceWindow]
    ) -> None:
        window_id = task_data.get("window_id")
        window_name = task_data.get("window_name")

        if window_id is not None:
            if not any(w.id == window_id for w in existing_windows):
                raise ValidationError(
                    f"Task '{task_data.get('name')}' references non-existent window id {window_id}",
                    code="import_window_not_found",
                )
        elif window_name is not None:
            if not any(w.name == window_name for w in existing_windows):
                raise ValidationError(
                    f"Task '{task_data.get('name')}' references non-existent window '{window_name}'",
                    code="import_window_not_found",
                )
        else:
            raise ValidationError(
                f"Task '{task_data.get('name')}' must reference a window by id or name",
                code="missing_window_reference",
            )
