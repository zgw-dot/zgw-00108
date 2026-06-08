"""Tests for persistence layer."""

from datetime import datetime, timedelta

import pytest

from repair_cli.models import (
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.persistence import (
    AuditRepository,
    PolicyRepository,
    RoleRepository,
    TaskRepository,
    WindowRepository,
)


def test_window_create_and_get(db):
    repo = WindowRepository(db)
    start = datetime.utcnow()
    end = start + timedelta(hours=2)
    w = MaintenanceWindow(
        name="test-win",
        description="test",
        start_time=start,
        end_time=end,
        created_by="admin_user",
    )
    created = repo.create(w)
    assert created.id is not None
    assert created.name == "test-win"

    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "test-win"
    assert fetched.description == "test"


def test_window_overlap_detection(db):
    repo = WindowRepository(db)
    now = datetime.utcnow()
    w1 = MaintenanceWindow(
        name="win1",
        start_time=now,
        end_time=now + timedelta(hours=2),
        created_by="admin",
    )
    repo.create(w1)

    overlapping = repo.get_all_overlapping(
        now + timedelta(hours=1),
        now + timedelta(hours=3),
    )
    assert len(overlapping) == 1

    non_overlapping = repo.get_all_overlapping(
        now + timedelta(hours=3),
        now + timedelta(hours=4),
    )
    assert len(non_overlapping) == 0


def test_task_create_and_get(db, future_window):
    repo = TaskRepository(db)
    t = RepairTask(
        name="test-task",
        description="test",
        created_by="operator",
        sql="UPDATE ...",
        window_id=future_window.id,
    )
    created = repo.create(t)
    assert created.id is not None
    assert created.name == "test-task"
    assert created.status == TaskStatus.DRAFT

    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "test-task"


def test_task_status_update(db, draft_task):
    repo = TaskRepository(db)
    updated = repo.update_status(
        draft_task.id,
        old_status=TaskStatus.DRAFT,
        new_status=TaskStatus.PENDING_APPROVAL,
    )
    assert updated is not None
    assert updated.status == TaskStatus.PENDING_APPROVAL

    updated2 = repo.update_status(
        draft_task.id,
        old_status=TaskStatus.DRAFT,
        new_status=TaskStatus.APPROVED,
    )
    assert updated2 is None


def test_audit_logging(db, draft_task):
    repo = AuditRepository(db)
    from repair_cli.models import AuditLog
    initial_count = len(repo.list_by_task(draft_task.id))

    log = AuditLog(
        task_id=draft_task.id,
        action="test_action",
        actor="test_user",
        old_status=TaskStatus.DRAFT.value,
        new_status=TaskStatus.PENDING_APPROVAL.value,
        details="test details",
    )
    created = repo.log(log)
    assert created.id is not None

    logs = repo.list_by_task(draft_task.id)
    assert len(logs) == initial_count + 1
    test_logs = [l for l in logs if l.action == "test_action"]
    assert len(test_logs) == 1


def test_role_repository(db):
    repo = RoleRepository(db)
    assert repo.get_role("admin_user") == Role.ADMIN
    assert repo.get_role("approver_user") == Role.APPROVER
    assert repo.get_role("operator_user") == Role.OPERATOR
    assert repo.get_role("unknown_user") is None

    assert repo.has_role("admin_user", Role.ADMIN)
    assert repo.has_role("admin_user", Role.APPROVER)
    assert repo.has_role("admin_user", Role.OPERATOR)
    assert repo.has_role("approver_user", Role.APPROVER)
    assert repo.has_role("approver_user", Role.OPERATOR)
    assert not repo.has_role("operator_user", Role.APPROVER)
    assert not repo.has_role("operator_user", Role.ADMIN)

    repo.set_role("test_user", Role.APPROVER)
    assert repo.get_role("test_user") == Role.APPROVER


def test_task_list_by_window(db, future_window, draft_task):
    repo = TaskRepository(db)
    tasks = repo.list_by_window(future_window.id)
    assert len(tasks) == 1
    assert tasks[0].id == draft_task.id


def test_window_get_by_name(db, future_window):
    repo = WindowRepository(db)
    fetched = repo.get_by_name(future_window.name)
    assert fetched is not None
    assert fetched.id == future_window.id

    not_found = repo.get_by_name("non-existent")
    assert not_found is None


def test_window_update(db, future_window):
    repo = WindowRepository(db)
    updated = repo.update(future_window.id, description="new description")
    assert updated is not None
    assert updated.description == "new description"

    not_found = repo.update(9999, description="test")
    assert not_found is None


def test_concurrent_update_optimistic_lock(db, draft_task):
    repo = TaskRepository(db)
    updated = repo.update_status(
        draft_task.id,
        old_status=TaskStatus.DRAFT,
        new_status=TaskStatus.PENDING_APPROVAL,
    )
    assert updated is not None
    assert updated.status == TaskStatus.PENDING_APPROVAL

    second_update = repo.update_status(
        draft_task.id,
        old_status=TaskStatus.DRAFT,
        new_status=TaskStatus.APPROVED,
    )
    assert second_update is None

    refreshed = repo.get(draft_task.id)
    assert refreshed.status == TaskStatus.PENDING_APPROVAL


def test_policy_default_values(db):
    """Test that policy has correct default values."""
    repo = PolicyRepository(db)
    policy = repo.get()
    assert policy.allow_admin_self_approval is True
    assert policy.require_different_approver is True
    assert policy.updated_by is None


def test_policy_update_persistence(db):
    """Test that policy updates are persisted."""
    repo = PolicyRepository(db)

    updated = repo.update(
        allow_admin_self_approval=False,
        require_different_approver=False,
        updated_by="admin_user",
    )
    assert updated.allow_admin_self_approval is False
    assert updated.require_different_approver is False
    assert updated.updated_by == "admin_user"

    fetched = repo.get()
    assert fetched.allow_admin_self_approval is False
    assert fetched.require_different_approver is False
    assert fetched.updated_by == "admin_user"


def test_policy_partial_update(db):
    """Test partial policy update only changes specified fields."""
    repo = PolicyRepository(db)

    repo.update(
        allow_admin_self_approval=False,
        updated_by="admin_user",
    )

    policy = repo.get()
    assert policy.allow_admin_self_approval is False
    assert policy.require_different_approver is True

    repo.update(
        require_different_approver=False,
        updated_by="admin_user",
    )

    policy = repo.get()
    assert policy.allow_admin_self_approval is False
    assert policy.require_different_approver is False


def test_policy_persistence_across_reconnect(db, db_path):
    """Test that policy persists after database reconnect."""
    from repair_cli.persistence import Database

    repo = PolicyRepository(db)
    repo.update(
        allow_admin_self_approval=False,
        require_different_approver=False,
        updated_by="test_admin",
    )

    new_db = Database(f"sqlite:///{db_path}")
    new_repo = PolicyRepository(new_db)
    policy = new_repo.get()

    assert policy.allow_admin_self_approval is False
    assert policy.require_different_approver is False
    assert policy.updated_by == "test_admin"
