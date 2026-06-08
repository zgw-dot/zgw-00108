"""Tests for service layer - main workflow."""

from datetime import datetime, timedelta

import pytest

from repair_cli.models import (
    ApprovalPolicy,
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.service import RepairService
from repair_cli.validation import ValidationError


def test_full_workflow_create_approve_run(service, active_window):
    """Test the full main workflow: create -> submit -> approve -> run."""
    task = service.create_task(RepairTask(
        name="full-workflow-task",
        description="Test full workflow",
        created_by="operator_user",
        sql="UPDATE users SET name = 'test' WHERE id = 1",
        rollback_sql="UPDATE users SET name = 'original' WHERE id = 1",
        window_id=active_window.id,
    ))
    assert task.status == TaskStatus.DRAFT
    assert task.id is not None

    task = service.submit_for_approval(task.id, "operator_user")
    assert task.status == TaskStatus.PENDING_APPROVAL

    task = service.approve_task(task.id, "approver_user")
    assert task.status == TaskStatus.APPROVED
    assert task.approved_by == "approver_user"
    assert task.approved_at is not None

    task = service.run_task(task.id, "operator_user")
    assert task.status == TaskStatus.SUCCEEDED
    assert task.executed_at is not None
    assert task.executed_by == "operator_user"

    audits = service.audit_repo.list_by_task(task.id)
    actions = [a.action for a in audits]
    assert "task_created" in actions
    assert "task_submitted" in actions
    assert "task_approved" in actions
    assert "task_run_started" in actions
    assert "task_run_succeeded" in actions


def test_rollback_after_success(service, succeeded_task):
    """Test rollback after successful execution."""
    assert succeeded_task.status == TaskStatus.SUCCEEDED

    rolled_back = service.rollback_task(succeeded_task.id, "operator_user")
    assert rolled_back.status == TaskStatus.ROLLBACK_SUCCEEDED
    assert rolled_back.rollback_at is not None
    assert rolled_back.rollback_by == "operator_user"

    audits = service.audit_repo.list_by_task(succeeded_task.id)
    actions = [a.action for a in audits]
    assert "task_rollback_started" in actions
    assert "task_rollback_succeeded" in actions


def test_reject_task(service, pending_task):
    """Test task rejection."""
    rejected = service.reject_task(pending_task.id, "approver_user", reason="Not needed")
    assert rejected.status == TaskStatus.REJECTED

    audits = service.audit_repo.list_by_task(pending_task.id)
    reject_audits = [a for a in audits if a.action == "task_rejected"]
    assert len(reject_audits) == 1
    assert reject_audits[0].details == "Not needed"


def test_failed_task_execution(service, active_window):
    """Test task execution failure."""
    task = service.create_task(RepairTask(
        name="fail-task",
        description="Task that will fail",
        created_by="operator_user",
        sql="FAIL This should fail",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "operator_user")
    task = service.approve_task(task.id, "approver_user")

    result = service.run_task(task.id, "operator_user")
    assert result.status == TaskStatus.FAILED


def test_rollback_after_failure(service, active_window):
    """Test rollback after failed execution."""
    task = service.create_task(RepairTask(
        name="fail-rollback-task",
        description="Task that will fail and be rolled back",
        created_by="operator_user",
        sql="FAIL This should fail",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "operator_user")
    task = service.approve_task(task.id, "approver_user")
    task = service.run_task(task.id, "operator_user")
    assert task.status == TaskStatus.FAILED

    rolled_back = service.rollback_task(task.id, "operator_user")
    assert rolled_back.status == TaskStatus.ROLLBACK_SUCCEEDED


def test_self_approval_blocked(service, future_window, role_repo):
    """Test that a user cannot approve their own task."""
    role_repo.set_role("approver_creator", Role.APPROVER)
    task = service.create_task(RepairTask(
        name="self-approve-test",
        description="test",
        created_by="approver_creator",
        sql="UPDATE ...",
        window_id=future_window.id,
    ))
    task = service.submit_for_approval(task.id, "approver_creator")

    with pytest.raises(ValidationError, match="cannot approve their own task"):
        service.approve_task(task.id, "approver_creator")

    refreshed = service.task_repo.get(task.id)
    assert refreshed.status == TaskStatus.PENDING_APPROVAL


def test_rollback_before_run_blocked(service, approved_task):
    """Test that rollback before execution is blocked."""
    with pytest.raises(ValidationError, match="must be.*succeeded.*failed"):
        service.rollback_task(approved_task.id, "operator_user")

    refreshed = service.task_repo.get(approved_task.id)
    assert refreshed.status == TaskStatus.APPROVED


def test_window_overlap_blocked(service, future_window):
    """Test that overlapping windows are blocked."""
    overlapping = MaintenanceWindow(
        name="overlapping-window",
        start_time=future_window.start_time + timedelta(minutes=30),
        end_time=future_window.end_time + timedelta(hours=1),
        created_by="admin_user",
    )
    with pytest.raises(ValidationError, match="overlaps"):
        service.create_window(overlapping)

    windows = service.window_repo.list()
    names = [w.name for w in windows]
    assert "overlapping-window" not in names


def test_set_user_role_admin_required(service):
    """Test that only admins can set roles."""
    with pytest.raises(ValidationError, match="does not have admin role"):
        service.set_user_role("test_user", Role.APPROVER, "operator_user")

    service.set_user_role("test_user", Role.APPROVER, "admin_user")
    assert service.role_repo.get_role("test_user") == Role.APPROVER


def test_audit_records_all_actions(service, active_window):
    """Test that all actions produce audit records."""
    task = service.create_task(RepairTask(
        name="audit-test",
        description="test",
        created_by="operator_user",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    initial_audit_count = len(service.audit_repo.list_by_task(task.id))

    service.submit_for_approval(task.id, "operator_user")
    service.approve_task(task.id, "approver_user")
    service.run_task(task.id, "operator_user")
    service.rollback_task(task.id, "operator_user")

    audits = service.audit_repo.list_by_task(task.id)
    assert len(audits) == initial_audit_count + 6

    actions = [(a.action, a.actor) for a in audits]
    assert ("task_created", "operator_user") in actions
    assert ("task_submitted", "operator_user") in actions
    assert ("task_approved", "approver_user") in actions
    assert ("task_run_started", "operator_user") in actions
    assert ("task_run_succeeded", "operator_user") in actions
    assert ("task_rollback_started", "operator_user") in actions
    assert ("task_rollback_succeeded", "operator_user") in actions


def test_database_persistence_across_reconnect(service, db_path, active_window):
    """Test that data persists after reconnecting to the database."""
    from repair_cli.persistence import Database

    task = service.create_task(RepairTask(
        name="persistence-test",
        description="Test persistence across reconnects",
        created_by="operator_user",
        sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task_id = task.id

    new_db = Database(f"sqlite:///{db_path}")
    new_db.init_db()
    new_service = RepairService(new_db)

    fetched = new_service.task_repo.get(task_id)
    assert fetched is not None
    assert fetched.name == "persistence-test"
    assert fetched.status == TaskStatus.DRAFT


def test_concurrent_status_update_protection(service, draft_task):
    """Test that concurrent status updates are properly handled."""
    task = service.submit_for_approval(draft_task.id, "operator_user")
    assert task.status == TaskStatus.PENDING_APPROVAL

    updated = service.task_repo.update_status(
        draft_task.id,
        old_status=TaskStatus.DRAFT,
        new_status=TaskStatus.APPROVED,
    )
    assert updated is None

    refreshed = service.task_repo.get(draft_task.id)
    assert refreshed.status == TaskStatus.PENDING_APPROVAL


def test_invalid_role_permissions(service, draft_task):
    """Test that invalid role permissions are rejected."""
    pending = service.submit_for_approval(draft_task.id, "operator_user")

    with pytest.raises(ValidationError, match="does not have approver role"):
        service.approve_task(pending.id, "operator_user")

    refreshed = service.task_repo.get(pending.id)
    assert refreshed.status == TaskStatus.PENDING_APPROVAL

    service.task_repo.update_fields(
        pending.id,
        status=TaskStatus.DRAFT.value,
    )
    draft_task2 = service.task_repo.get(pending.id)

    with pytest.raises(ValidationError, match="does not have operator role"):
        service.submit_for_approval(draft_task2.id, "unknown_user")


def test_admin_can_approve_own_task(service, active_window, role_repo):
    """Test that admin users can approve their own created tasks."""
    role_repo.set_role("admin_creator", Role.ADMIN)
    task = service.create_task(RepairTask(
        name="admin-self-approve",
        description="Test admin self-approval",
        created_by="admin_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "admin_creator")
    assert task.status == TaskStatus.PENDING_APPROVAL

    task = service.approve_task(task.id, "admin_creator")
    assert task.status == TaskStatus.APPROVED
    assert task.approved_by == "admin_creator"


def test_admin_can_execute_own_task(service, active_window, role_repo):
    """Test that admin users can execute their own created tasks (full workflow)."""
    role_repo.set_role("admin_creator", Role.ADMIN)
    task = service.create_task(RepairTask(
        name="admin-self-execute",
        description="Test admin self-execution",
        created_by="admin_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "admin_creator")
    task = service.approve_task(task.id, "admin_creator")

    task = service.run_task(task.id, "admin_creator")
    assert task.status == TaskStatus.SUCCEEDED
    assert task.executed_by == "admin_creator"


def test_failed_rollback_writes_audit_log(service, approved_task):
    """Test that rollback before execution fails and writes audit log."""
    initial_audits = service.audit_repo.list_by_task(approved_task.id)
    initial_count = len(initial_audits)

    initial_status = approved_task.status

    with pytest.raises(ValidationError, match="must be 'succeeded' or 'failed'"):
        service.rollback_task(approved_task.id, "operator_user")

    refreshed = service.task_repo.get(approved_task.id)
    assert refreshed.status == initial_status

    audits = service.audit_repo.list_by_task(approved_task.id)
    assert len(audits) == initial_count + 1

    failed_audit = audits[-1]
    assert failed_audit.action == "task_rollback_rejected"
    assert failed_audit.actor == "operator_user"
    assert failed_audit.old_status == initial_status.value
    assert failed_audit.new_status == initial_status.value
    assert "must be 'succeeded' or 'failed'" in (failed_audit.details or "")


def test_get_policy_default(service):
    """Test get_policy returns default policy."""
    policy = service.get_policy()
    assert isinstance(policy, ApprovalPolicy)
    assert policy.allow_admin_self_approval is True
    assert policy.require_different_approver is True


def test_update_policy_requires_admin(service):
    """Test that only admin can update policy."""
    with pytest.raises(ValidationError) as exc_info:
        service.update_policy(
            actor="operator_user",
            allow_admin_self_approval=False,
        )
    assert exc_info.value.code == "policy_update_denied"

    audits = service.audit_repo.list()
    denied_audits = [a for a in audits if a.action == "policy_update_denied"]
    assert len(denied_audits) >= 1
    assert denied_audits[-1].actor == "operator_user"


def test_update_policy_success_and_audit(service):
    """Test successful policy update and audit logging."""
    initial_audits = service.audit_repo.list()

    result = service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=True,
    )

    assert result.old_policy.allow_admin_self_approval is True
    assert result.new_policy.allow_admin_self_approval is False
    assert "allow_admin_self_approval" in result.changed_fields
    assert "require_different_approver" not in result.changed_fields

    audits = service.audit_repo.list()
    assert len(audits) > len(initial_audits)
    policy_audits = [a for a in audits if a.action == "policy_updated"]
    assert len(policy_audits) == 1
    assert policy_audits[0].actor == "admin_user"
    assert "allow_admin_self_approval=True" in (policy_audits[0].details or "")
    assert "allow_admin_self_approval=False" in (policy_audits[0].details or "")


def test_policy_disable_admin_self_approval(service, active_window, role_repo):
    """Test that disabling admin self-approval blocks admin from approving own tasks."""
    role_repo.set_role("admin_creator", Role.ADMIN)

    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
    )

    task = service.create_task(RepairTask(
        name="admin-blocked-self-approve",
        description="Test admin self-approval blocked by policy",
        created_by="admin_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "admin_creator")

    with pytest.raises(ValidationError) as exc_info:
        service.approve_task(task.id, "admin_creator")
    assert exc_info.value.code == "self_approval_not_allowed"

    refreshed = service.task_repo.get(task.id)
    assert refreshed.status == TaskStatus.PENDING_APPROVAL


def test_policy_enable_admin_self_approval(service, active_window, role_repo):
    """Test that enabling admin self-approval allows admin to approve own tasks (default)."""
    role_repo.set_role("admin_creator", Role.ADMIN)

    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=True,
    )

    task = service.create_task(RepairTask(
        name="admin-allowed-self-approve",
        description="Test admin self-approval allowed by policy",
        created_by="admin_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "admin_creator")

    approved = service.approve_task(task.id, "admin_creator")
    assert approved.status == TaskStatus.APPROVED
    assert approved.approved_by == "admin_creator"


def test_policy_disable_require_different_approver(service, active_window, role_repo):
    """Test that disabling require_different_approver allows regular users to approve own tasks."""
    role_repo.set_role("approver_creator", Role.APPROVER)

    service.update_policy(
        actor="admin_user",
        require_different_approver=False,
    )

    task = service.create_task(RepairTask(
        name="self-approve-allowed",
        description="Test self-approval allowed when policy disabled",
        created_by="approver_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "approver_creator")

    approved = service.approve_task(task.id, "approver_creator")
    assert approved.status == TaskStatus.APPROVED
    assert approved.approved_by == "approver_creator"


def test_policy_disable_require_different_approver_reject(service, active_window, role_repo):
    """Test that disabling require_different_approver allows users to reject own tasks."""
    role_repo.set_role("approver_creator", Role.APPROVER)

    service.update_policy(
        actor="admin_user",
        require_different_approver=False,
    )

    task = service.create_task(RepairTask(
        name="self-reject-allowed",
        description="Test self-rejection allowed when policy disabled",
        created_by="approver_creator",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "approver_creator")

    rejected = service.reject_task(task.id, "approver_creator", reason="Changed my mind")
    assert rejected.status == TaskStatus.REJECTED


def test_policy_change_audited_properly(service):
    """Test that policy changes are properly audited with before/after values."""
    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=False,
    )

    audits = service.audit_repo.list()
    policy_audit = [a for a in audits if a.action == "policy_updated"][-1]

    assert "allow_admin_self_approval=True" in (policy_audit.details or "")
    assert "allow_admin_self_approval=False" in (policy_audit.details or "")
    assert "require_different_approver=True" in (policy_audit.details or "")
    assert "require_different_approver=False" in (policy_audit.details or "")
    assert "Changed fields: allow_admin_self_approval, require_different_approver" in (policy_audit.details or "")


def test_policy_update_no_changes(service):
    """Test updating policy with same values returns empty changed_fields."""
    result = service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=True,
        require_different_approver=True,
    )
    assert len(result.changed_fields) == 0

    audits = service.audit_repo.list()
    policy_audits = [a for a in audits if a.action == "policy_updated"]
    assert len(policy_audits) >= 1
    assert "Changed fields: none" in (policy_audits[-1].details or "")


def test_policy_persistence_across_service_reconnect(service, db_path, active_window, role_repo):
    """Test that policy persists across service reconnections and affects approval."""
    from repair_cli.persistence import Database

    role_repo.set_role("admin_creator", Role.ADMIN)

    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=True,
    )

    task = service.create_task(RepairTask(
        name="persistence-test-task",
        description="Test policy persistence",
        created_by="admin_creator",
        sql="UPDATE ...",
        window_id=active_window.id,
    ))
    task = service.submit_for_approval(task.id, "admin_creator")

    new_db = Database(f"sqlite:///{db_path}")
    new_db.init_db()
    new_db.init_default_policy()
    new_service = RepairService(new_db)

    policy = new_service.get_policy()
    assert policy.allow_admin_self_approval is False

    with pytest.raises(ValidationError, match="cannot approve their own task"):
        new_service.approve_task(task.id, "admin_creator")
