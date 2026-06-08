"""Tests for validation layer."""

from datetime import datetime, timedelta

import pytest

from repair_cli.models import (
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.validation import ValidationError, Validator


def test_window_overlap_validation(service, future_window):
    validator = service.validator
    now = datetime.utcnow()

    overlapping = MaintenanceWindow(
        name="overlapping",
        start_time=future_window.start_time + timedelta(minutes=30),
        end_time=future_window.end_time + timedelta(minutes=30),
        created_by="admin_user",
    )
    with pytest.raises(ValidationError, match="overlaps"):
        validator.validate_window_creation(overlapping)

    non_overlapping = MaintenanceWindow(
        name="non-overlapping",
        start_time=future_window.end_time + timedelta(hours=1),
        end_time=future_window.end_time + timedelta(hours=2),
        created_by="admin_user",
    )
    validator.validate_window_creation(non_overlapping)


def test_window_invalid_time_range(service):
    validator = service.validator
    now = datetime.utcnow()

    invalid = MaintenanceWindow(
        name="invalid",
        start_time=now + timedelta(hours=2),
        end_time=now + timedelta(hours=1),
        created_by="admin_user",
    )
    with pytest.raises(ValidationError, match="end time.*must be after start time"):
        validator.validate_window_creation(invalid)


def test_duplicate_window_name(service, future_window):
    validator = service.validator

    duplicate = MaintenanceWindow(
        name=future_window.name,
        start_time=future_window.end_time + timedelta(hours=1),
        end_time=future_window.end_time + timedelta(hours=2),
        created_by="admin_user",
    )
    with pytest.raises(ValidationError, match="already exists"):
        validator.validate_window_creation(duplicate)


def test_self_approval_not_allowed(service, future_window, role_repo):
    role_repo.set_role("approver_creator", Role.APPROVER)
    task = service.create_task(RepairTask(
        name="self-approve-test",
        description="test",
        created_by="approver_creator",
        sql="UPDATE ...",
        window_id=future_window.id,
    ))
    task = service.submit_for_approval(task.id, "approver_creator")

    validator = service.validator
    with pytest.raises(ValidationError, match="cannot approve their own task"):
        validator.validate_approve(task.id, "approver_creator")


def test_approve_wrong_status(service, draft_task):
    validator = service.validator
    with pytest.raises(ValidationError, match="must be.*pending_approval"):
        validator.validate_approve(draft_task.id, "approver_user")


def test_approve_permission_denied(service, pending_task):
    validator = service.validator
    with pytest.raises(ValidationError, match="does not have approver role"):
        validator.validate_approve(pending_task.id, "operator_user")


def test_submit_wrong_status(service, pending_task):
    validator = service.validator
    with pytest.raises(ValidationError, match="must be.*draft"):
        validator.validate_submit_for_approval(pending_task.id, "operator_user")


def test_rollback_before_execution(service, approved_task):
    validator = service.validator
    with pytest.raises(ValidationError, match="must be.*succeeded.*failed"):
        validator.validate_rollback(approved_task.id, "operator_user")


def test_rollback_no_rollback_sql(service, active_window):
    task = service.create_task(RepairTask(
        name="no-rollback",
        description="test",
        created_by="operator_user",
        sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "operator_user")
    task = service.approve_task(task.id, "approver_user")
    task = service.run_task(task.id, "operator_user")

    validator = service.validator
    with pytest.raises(ValidationError, match="no rollback SQL"):
        validator.validate_rollback(task.id, "operator_user")


def test_run_before_approval(service, draft_task, active_window):
    service.task_repo.update_fields(draft_task.id, window_id=active_window.id)
    validator = service.validator
    with pytest.raises(ValidationError, match="must be.*approved"):
        validator.validate_run(draft_task.id, "operator_user")


def test_run_outside_window(service, approved_task, future_window):
    service.task_repo.update_fields(approved_task.id, window_id=future_window.id)
    validator = service.validator
    with pytest.raises(ValidationError, match="not started yet"):
        validator.validate_run(approved_task.id, "operator_user")


def test_task_creation_nonexistent_window(service):
    validator = service.validator
    task = RepairTask(
        name="test",
        description="test",
        created_by="operator_user",
        sql="UPDATE ...",
        window_id=9999,
    )
    with pytest.raises(ValidationError, match="does not exist"):
        validator.validate_task_creation(task)


def test_import_window_not_found(service):
    validator = service.validator
    existing_windows = service.window_repo.list()
    task_data = {
        "name": "test-task",
        "window_name": "non-existent-window",
    }
    with pytest.raises(ValidationError, match="references non-existent window"):
        validator.validate_task_window_reference(task_data, existing_windows)


def test_update_window_with_active_tasks(service, pending_task, future_window):
    validator = service.validator
    new_start = future_window.start_time + timedelta(hours=1)
    new_end = future_window.end_time + timedelta(hours=1)
    with pytest.raises(ValidationError, match="Cannot modify window"):
        validator.validate_window_update(
            future_window.id,
            new_start_time=new_start,
            new_end_time=new_end,
        )


def test_update_window_no_active_tasks(service, draft_task, future_window):
    validator = service.validator
    new_start = future_window.start_time + timedelta(hours=1)
    new_end = future_window.end_time + timedelta(hours=1)
    validator.validate_window_update(
        future_window.id,
        new_start_time=new_start,
        new_end_time=new_end,
    )
