"""Tests for pre-execution checklist functionality."""

from __future__ import annotations

import pytest

from repair_cli.models import (
    ChecklistItem,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.validation import ValidationError


def test_set_checklist_basic(service, draft_task):
    """Test basic checklist setting."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Test on staging", required=True),
        ChecklistItem(task_id=draft_task.id, name="Notify stakeholders", required=False),
    ]
    result = service.set_checklist(draft_task.id, items, "operator_user")
    assert result.task_id == draft_task.id
    assert result.items_created == 3
    assert result.items_updated == 0

    saved = service.get_checklist(draft_task.id, "operator_user")
    assert len(saved) == 3
    assert saved[0].name == "Verify backup"
    assert saved[0].required is True
    assert saved[0].completed is False
    assert saved[2].required is False


def test_set_checklist_replaces_existing(service, draft_task):
    """Test that setting checklist replaces existing items."""
    items1 = [
        ChecklistItem(task_id=draft_task.id, name="Item 1", required=True),
        ChecklistItem(task_id=draft_task.id, name="Item 2", required=True),
    ]
    service.set_checklist(draft_task.id, items1, "operator_user")

    items2 = [
        ChecklistItem(task_id=draft_task.id, name="Item 1", required=True),
        ChecklistItem(task_id=draft_task.id, name="Item 3", required=True),
    ]
    result = service.set_checklist(draft_task.id, items2, "operator_user")
    assert result.items_created == 1
    assert result.items_updated == 0

    saved = service.get_checklist(draft_task.id, "operator_user")
    assert len(saved) == 2
    names = [i.name for i in saved]
    assert "Item 1" in names
    assert "Item 3" in names
    assert "Item 2" not in names


def test_update_checklist_item_completed(service, draft_task):
    """Test marking a checklist item as completed."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    item_id = saved[0].id

    result = service.update_checklist_item(
        task_id=draft_task.id,
        item_id=item_id,
        actor="operator_user",
        completed=True,
    )
    assert result.old_value is False
    assert result.new_value is True
    assert result.updated_by == "operator_user"

    refreshed = service.get_checklist(draft_task.id, "operator_user")
    assert refreshed[0].completed is True


def test_update_checklist_item_with_notes(service, draft_task):
    """Test updating checklist item with notes."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    item_id = saved[0].id

    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=item_id,
        actor="operator_user",
        notes="Backup verified at 2025-01-15 03:00 UTC",
    )

    refreshed = service.get_checklist(draft_task.id, "operator_user")
    assert refreshed[0].notes == "Backup verified at 2025-01-15 03:00 UTC"
    assert refreshed[0].updated_by == "operator_user"


def test_submit_blocked_by_incomplete_required_checklist(service, draft_task):
    """Test that submit is blocked when required checklist items are incomplete."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Notify stakeholders", required=False),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    with pytest.raises(ValidationError) as exc_info:
        service.submit_for_approval(draft_task.id, "operator_user")
    assert exc_info.value.code == "checklist_incomplete"
    assert "Verify backup" in exc_info.value.message

    refreshed = service.task_repo.get(draft_task.id)
    assert refreshed.status == TaskStatus.DRAFT


def test_submit_allowed_when_required_checklist_complete(service, draft_task):
    """Test that submit succeeds when all required checklist items are complete."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Notify stakeholders", required=False),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    for item in saved:
        if item.required:
            service.update_checklist_item(
                task_id=draft_task.id,
                item_id=item.id,
                actor="operator_user",
                completed=True,
            )

    submitted = service.submit_for_approval(draft_task.id, "operator_user")
    assert submitted.status == TaskStatus.PENDING_APPROVAL


def test_run_blocked_by_incomplete_required_checklist(service, active_window):
    """Test that run is blocked when required checklist items are incomplete."""
    task = service.create_task(RepairTask(
        name="checklist-run-test",
        description="Test run with checklist",
        created_by="operator_user",
        sql="UPDATE users SET email = 'test@example.com'",
        rollback_sql="UPDATE users SET email = 'old@example.com'",
        window_id=active_window.id,
    ))

    items = [
        ChecklistItem(task_id=task.id, name="Verify backup", required=True),
    ]
    service.set_checklist(task.id, items, "operator_user")

    saved = service.get_checklist(task.id, "operator_user")
    for item in saved:
        service.update_checklist_item(
            task_id=task.id,
            item_id=item.id,
            actor="operator_user",
            completed=True,
        )

    task = service.submit_for_approval(task.id, "operator_user")
    task = service.approve_task(task.id, "approver_user")

    service.update_checklist_item(
        task_id=task.id,
        item_id=saved[0].id,
        actor="operator_user",
        completed=False,
    )

    with pytest.raises(ValidationError) as exc_info:
        service.run_task(task.id, "operator_user")
    assert exc_info.value.code == "checklist_incomplete"


def test_checklist_permission_denied_non_operator(service, draft_task, role_repo):
    """Test that non-operator users cannot update checklist."""
    role_repo.set_role("readonly_user", Role.OPERATOR)

    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]

    with pytest.raises(ValidationError) as exc_info:
        service.set_checklist(draft_task.id, items, "unknown_user")
    assert exc_info.value.code == "checklist_permission_denied"

    audits = service.audit_repo.list()
    denied_audits = [a for a in audits if a.action == "checklist_update_denied"]
    assert len(denied_audits) >= 1
    assert denied_audits[-1].actor == "unknown_user"


def test_checklist_permission_denied_not_owner(service, draft_task, role_repo):
    """Test that non-owner operators cannot update checklist."""
    role_repo.set_role("other_operator", Role.OPERATOR)

    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]

    with pytest.raises(ValidationError) as exc_info:
        service.set_checklist(draft_task.id, items, "other_operator")
    assert exc_info.value.code == "checklist_not_owner"

    audits = service.audit_repo.list()
    denied_audits = [a for a in audits if a.action == "checklist_update_denied"]
    assert len(denied_audits) >= 1


def test_admin_can_update_any_checklist(service, draft_task, role_repo):
    """Test that admin can update checklist for any task."""
    role_repo.set_role("admin_user", Role.ADMIN)

    items = [
        ChecklistItem(task_id=draft_task.id, name="Admin test", required=True),
    ]

    result = service.set_checklist(draft_task.id, items, "admin_user")
    assert result.items_created == 1

    saved = service.get_checklist(draft_task.id, "admin_user")
    assert len(saved) == 1
    assert saved[0].name == "Admin test"


def test_approver_can_view_but_not_update_checklist(service, draft_task, role_repo):
    """Test that approver can view but not update checklist."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "approver_user")
    assert len(saved) == 1

    with pytest.raises(ValidationError) as exc_info:
        service.update_checklist_item(
            task_id=draft_task.id,
            item_id=saved[0].id,
            actor="approver_user",
            completed=True,
        )
    assert exc_info.value.code == "checklist_permission_denied"

    refreshed = service.get_checklist(draft_task.id, "operator_user")
    assert refreshed[0].completed is False


def test_checklist_persistence_across_reconnect(service, db_path, draft_task):
    """Test that checklist persists across database reconnections."""
    from repair_cli.persistence import Database

    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Test on staging", required=False),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    item_id = saved[0].id
    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=item_id,
        actor="operator_user",
        completed=True,
        notes="All good",
    )

    new_db = Database(f"sqlite:///{db_path}")
    new_db.init_db()
    from repair_cli.service import RepairService
    new_service = RepairService(new_db)

    fetched = new_service.get_checklist(draft_task.id, "operator_user")
    assert len(fetched) == 2
    assert fetched[0].name == "Verify backup"
    assert fetched[0].completed is True
    assert fetched[0].notes == "All good"
    assert fetched[1].name == "Test on staging"
    assert fetched[1].completed is False


def test_checklist_audit_logging(service, draft_task):
    """Test that checklist operations are properly audited."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=saved[0].id,
        actor="operator_user",
        completed=True,
        notes="Done",
    )

    audits = service.audit_repo.list_by_task(draft_task.id)
    actions = [a.action for a in audits]
    assert "checklist_set" in actions
    assert "checklist_updated" in actions

    set_audit = [a for a in audits if a.action == "checklist_set"][0]
    assert set_audit.actor == "operator_user"
    assert "Verify backup" in (set_audit.details or "")

    update_audit = [a for a in audits if a.action == "checklist_updated"][0]
    assert update_audit.actor == "operator_user"
    assert "completed: False -> True" in (update_audit.details or "")
    assert "notes updated" in (update_audit.details or "")


def test_checklist_update_failure_audited(service, draft_task):
    """Test that failed checklist updates are audited."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    with pytest.raises(ValidationError):
        service.update_checklist_item(
            task_id=draft_task.id,
            item_id=99999,
            actor="operator_user",
            completed=True,
        )

    audits = service.audit_repo.list_by_task(draft_task.id)
    failed_audits = [a for a in audits if a.action == "checklist_update_failed"]
    assert len(failed_audits) >= 1
    assert "99999" in (failed_audits[0].details or "")


def test_submit_without_checklist_allowed(service, draft_task):
    """Test that tasks without checklist can be submitted."""
    submitted = service.submit_for_approval(draft_task.id, "operator_user")
    assert submitted.status == TaskStatus.PENDING_APPROVAL


def test_checklist_view_permission(service, draft_task, role_repo):
    """Test checklist view permissions."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    result = service.get_checklist(draft_task.id, "operator_user")
    assert len(result) == 1

    result = service.get_checklist(draft_task.id, "approver_user")
    assert len(result) == 1

    result = service.get_checklist(draft_task.id, "admin_user")
    assert len(result) == 1

    role_repo.set_role("nobody", Role.OPERATOR)
    with pytest.raises(ValidationError) as exc_info:
        service.get_checklist(draft_task.id, "unknown_user")
    assert exc_info.value.code == "checklist_view_denied"


def test_checklist_item_not_found(service, draft_task):
    """Test updating non-existent checklist item."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Test", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    with pytest.raises(ValidationError) as exc_info:
        service.update_checklist_item(
            task_id=draft_task.id,
            item_id=99999,
            actor="operator_user",
            completed=True,
        )
    assert exc_info.value.code == "checklist_item_not_found"


def test_checklist_item_mismatch(service, draft_task, future_window):
    """Test updating checklist item for wrong task."""
    task1 = draft_task
    task2 = service.create_task(RepairTask(
        name="task2",
        description="Second task",
        created_by="operator_user",
        sql="SELECT 1",
        window_id=future_window.id,
    ))

    items = [
        ChecklistItem(task_id=task1.id, name="Test", required=True),
    ]
    service.set_checklist(task1.id, items, "operator_user")

    saved = service.get_checklist(task1.id, "operator_user")

    with pytest.raises(ValidationError) as exc_info:
        service.update_checklist_item(
            task_id=task2.id,
            item_id=saved[0].id,
            actor="operator_user",
            completed=True,
        )
    assert exc_info.value.code == "checklist_item_mismatch"


def test_empty_checklist_item_name_rejected(service, draft_task):
    """Test that empty checklist item names are rejected."""
    with pytest.raises((ValidationError, Exception)) as exc_info:
        items = [
            ChecklistItem(task_id=draft_task.id, name="", required=True),
        ]
        service.set_checklist(draft_task.id, items, "operator_user")
    
    if isinstance(exc_info.value, ValidationError):
        assert exc_info.value.code == "invalid_checklist_item"
    else:
        assert "Cannot be empty" in str(exc_info.value)
