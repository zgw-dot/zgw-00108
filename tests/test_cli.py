"""Tests for CLI commands."""

import json
import tempfile
import os
from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner

from repair_cli.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help(runner: CliRunner):
    """Test that CLI help works."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "repair" in result.output.lower() or "Data repair" in result.output


def test_cli_output_formats(runner: CliRunner, db_path):
    """Test JSON output format."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_cli_table_output(runner: CliRunner, db_path):
    """Test table output format."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "table",
        "window", "list",
    ])
    assert result.exit_code == 0
    assert "+" in result.output or "---" in result.output


def test_full_workflow_via_cli(runner: CliRunner, db_path):
    """Test full workflow through CLI."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    result = runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "cli-test-window",
        "--description", "test window",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    assert "created" in result.output.lower()

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    assert result.exit_code == 0
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "cli-test-task",
        "--description", "test task",
        "--sql", "UPDATE users SET name = 'test'",
        "--rollback-sql", "UPDATE users SET name = 'original'",
        "--window-id", str(window_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    assert "created" in result.output.lower()

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    assert result.exit_code == 0
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]
    assert tasks[0]["status"] == "draft"

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    assert "submitted" in result.output.lower()

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "show", str(task_id),
    ])
    assert result.exit_code == 0
    task = json.loads(result.output)
    assert task["status"] == "pending_approval"

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "approve", str(task_id),
        "--as-user", "approver_user",
    ])
    assert result.exit_code == 0, result.output
    assert "approved" in result.output.lower()

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "show", str(task_id),
    ])
    assert result.exit_code == 0
    task = json.loads(result.output)
    assert task["status"] == "approved"
    assert task["approved_by"] == "approver_user"

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "run", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "show", str(task_id),
    ])
    assert result.exit_code == 0
    task = json.loads(result.output)
    assert task["status"] == "succeeded"
    assert task["executed_by"] == "operator_user"

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "rollback", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "show", str(task_id),
    ])
    assert result.exit_code == 0
    task = json.loads(result.output)
    assert task["status"] == "rollback_succeeded"


def test_cli_self_approval_rejected(runner: CliRunner, db_path):
    """Test that self-approval is rejected via CLI."""
    start = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "self-approve-test",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "self-approve-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "approver_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "approver_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "approve", str(task_id),
        "--as-user", "approver_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert not error["success"]
    assert "approve their own" in error["error"]


def test_cli_rollback_before_run_rejected(runner: CliRunner, db_path):
    """Test that rollback before run is rejected via CLI."""
    start = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "rollback-test",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "rollback-before-run",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--rollback-sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "rollback", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert not error["success"]
    assert "must be" in error["error"]


def test_cli_window_overlap_rejected(runner: CliRunner, db_path):
    """Test that overlapping windows are rejected via CLI."""
    start1 = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end1 = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    start2 = (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    end2 = (datetime.utcnow() + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "window1",
        "--start", start1,
        "--end", end1,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "create",
        "--name", "window2",
        "--start", start2,
        "--end", end2,
        "--as-user", "admin_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert not error["success"]
    assert "overlaps" in error["error"]


def test_cli_audit_logs(runner: CliRunner, db_path):
    """Test audit log viewing via CLI."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "audit-test-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "audit-test-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list",
    ])
    assert result.exit_code == 0
    audits = json.loads(result.output)
    assert len(audits) >= 1


def test_cli_export_import(runner: CliRunner, db_path):
    """Test export and import via CLI."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "export-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "export-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "operator_user",
    ])

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        result = runner.invoke(cli, [
            "--db", db_path,
            "plan", "export", export_path,
        ])
        assert result.exit_code == 0, result.output

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
            ])
            assert result.exit_code == 0, result.output

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "task", "list",
            ])
            assert result.exit_code == 0
            tasks = json.loads(result.output)
            assert len(tasks) == 1
            assert tasks[0]["name"] == "export-task"
        finally:
            try:
                os.unlink(new_db_path)
            except OSError:
                pass
    finally:
        try:
            os.unlink(export_path)
        except OSError:
            pass


def test_cli_roles(runner: CliRunner, db_path):
    """Test role management via CLI."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "role", "list",
    ])
    assert result.exit_code == 0
    roles = json.loads(result.output)
    assert len(roles) >= 3
    usernames = [r["username"] for r in roles]
    assert "admin_user" in usernames
    assert "approver_user" in usernames
    assert "operator_user" in usernames

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "role", "set", "new_user", "operator",
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "role", "list",
    ])
    roles = json.loads(result.output)
    usernames = [r["username"] for r in roles]
    assert "new_user" in usernames


def test_cli_admin_full_self_workflow(runner: CliRunner, db_path):
    """Test that admin can create, submit, approve, and run their own task."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "admin-self-workflow-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "admin-self-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--rollback-sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "approve", str(task_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["status"] == "approved"

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "run", str(task_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["status"] == "succeeded"


def test_cli_regular_user_self_approval_rejected(runner: CliRunner, db_path):
    """Test that regular user cannot approve their own task via CLI."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "regular-self-approve-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "regular-self-approve-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "approver_user",
    ])
    assert result.exit_code == 0, f"Task create failed: {result.output}"

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    assert result.exit_code == 0, f"Task list failed: {result.output}"
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "approver_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "approve", str(task_id),
        "--as-user", "approver_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert "cannot approve their own task" in error["error"]


def test_cli_failed_rollback_audit_log(runner: CliRunner, db_path):
    """Test that failed rollback writes audit log visible via audit list --task-id."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "failed-rollback-audit-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "failed-rollback-audit-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--rollback-sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]
    initial_status = tasks[0]["status"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list", "--task-id", str(task_id),
    ])
    assert result.exit_code == 0
    initial_audits = json.loads(result.output)
    initial_count = len(initial_audits)

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "rollback", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert "must be 'succeeded' or 'failed'" in error["error"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "show", str(task_id),
    ])
    assert result.exit_code == 0
    task = json.loads(result.output)
    assert task["status"] == initial_status

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list", "--task-id", str(task_id),
    ])
    assert result.exit_code == 0
    audits = json.loads(result.output)
    assert len(audits) == initial_count + 1

    failed_audit = audits[-1]
    assert failed_audit["action"] == "task_rollback_rejected"
    assert failed_audit["actor"] == "operator_user"
    assert failed_audit["old_status"] == initial_status
    assert failed_audit["new_status"] == initial_status
    assert "must be 'succeeded' or 'failed'" in (failed_audit["details"] or "")


def test_cli_policy_view_default(runner: CliRunner, db_path):
    """Test viewing default policy via CLI."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "view",
    ])
    assert result.exit_code == 0, result.output
    policy = json.loads(result.output)
    assert policy["allow_admin_self_approval"] is True
    assert policy["require_different_approver"] is True


def test_cli_policy_view_table_format(runner: CliRunner, db_path):
    """Test viewing policy in table format."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "table",
        "policy", "view",
    ])
    assert result.exit_code == 0, result.output
    assert "Allow Admin Self-Approval" in result.output
    assert "Require Different Approver" in result.output


def test_cli_policy_set_admin_only(runner: CliRunner, db_path):
    """Test that only admin can set policy via CLI."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "policy_update_denied"


def test_cli_policy_set_success(runner: CliRunner, db_path):
    """Test successful policy update via CLI."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--require-different-approver", "false",
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert "allow_admin_self_approval" in response["changed_fields"]
    assert "require_different_approver" in response["changed_fields"]
    assert response["new_policy"]["allow_admin_self_approval"] is False
    assert response["new_policy"]["require_different_approver"] is False

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "view",
    ])
    policy = json.loads(result.output)
    assert policy["allow_admin_self_approval"] is False
    assert policy["require_different_approver"] is False


def test_cli_policy_set_partial(runner: CliRunner, db_path):
    """Test partial policy update via CLI."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert "allow_admin_self_approval" in response["changed_fields"]
    assert "require_different_approver" not in response["changed_fields"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "view",
    ])
    policy = json.loads(result.output)
    assert policy["allow_admin_self_approval"] is False
    assert policy["require_different_approver"] is True


def test_cli_policy_set_missing_option(runner: CliRunner, db_path):
    """Test policy set with no options returns error."""
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "policy", "set",
        "--as-user", "admin_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "missing_policy_option"


def test_cli_policy_change_affects_approval(runner: CliRunner, db_path):
    """Test that policy change via CLI affects actual approval behavior."""
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", "policy-approval-test-win",
        "--start", start,
        "--end", end,
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "window", "list",
    ])
    windows = json.loads(result.output)
    window_id = windows[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "task", "create",
        "--name", "policy-approval-test-task",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--rollback-sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    task_id = tasks[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "admin_user",
    ])

    runner.invoke(cli, [
        "--db", db_path,
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "approve", str(task_id),
        "--as-user", "admin_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert "cannot approve their own task" in error["error"]


def test_cli_policy_update_audit_log(runner: CliRunner, db_path):
    """Test that policy updates are visible in audit log."""
    runner.invoke(cli, [
        "--db", db_path,
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list",
    ])
    assert result.exit_code == 0
    audits = json.loads(result.output)
    policy_audits = [a for a in audits if a["action"] == "policy_updated"]
    assert len(policy_audits) >= 1
    assert policy_audits[0]["actor"] == "admin_user"


def test_cli_policy_update_denied_audit_log(runner: CliRunner, db_path):
    """Test that denied policy updates are visible in audit log."""
    runner.invoke(cli, [
        "--db", db_path,
        "policy", "set",
        "--allow-admin-self-approval", "false",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list",
    ])
    assert result.exit_code == 0
    audits = json.loads(result.output)
    denied_audits = [a for a in audits if a["action"] == "policy_update_denied"]
    assert len(denied_audits) >= 1
    assert denied_audits[0]["actor"] == "operator_user"
