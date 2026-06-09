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


def _create_task_for_checklist(runner: CliRunner, db_path: str, as_user: str = "operator_user") -> int:
    start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    runner.invoke(cli, [
        "--db", db_path,
        "window", "create",
        "--name", f"checklist-win-{as_user}",
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
        "--name", f"checklist-task-{as_user}",
        "--description", "test",
        "--sql", "UPDATE ...",
        "--rollback-sql", "UPDATE ...",
        "--window-id", str(window_id),
        "--as-user", as_user,
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "list",
    ])
    tasks = json.loads(result.output)
    return tasks[0]["id"]


def test_cli_checklist_set(runner: CliRunner, db_path):
    """Test setting checklist via CLI with --item flags."""
    task_id = _create_task_for_checklist(runner, db_path)

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--item", "验证测试环境:true",
        "--item", "通知相关人员:false",
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["items_created"] == 3
    assert response["items_updated"] == 0


def test_cli_checklist_set_with_json(runner: CliRunner, db_path):
    """Test setting checklist via CLI with --json input."""
    task_id = _create_task_for_checklist(runner, db_path)

    json_items = json.dumps([
        {"name": "确认备份已完成", "required": True},
        {"name": "验证测试环境", "required": True},
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "set", str(task_id),
        "--json", json_items,
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["items_created"] == 2


def test_cli_checklist_view(runner: CliRunner, db_path):
    """Test viewing checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--item", "验证测试环境:false",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    items = json.loads(result.output)
    assert len(items) == 2
    assert items[0]["name"] == "确认备份已完成"
    assert items[0]["required"] is True
    assert items[0]["completed"] is False


def test_cli_checklist_view_table_format(runner: CliRunner, db_path):
    """Test viewing checklist in table format."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "table",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    assert "确认备份已完成" in result.output
    assert "+" in result.output


def test_cli_checklist_update(runner: CliRunner, db_path):
    """Test updating checklist item via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--notes", "备份已完成，路径 /backup/20240101.sql",
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["item"]["completed"] is True
    assert response["item"]["notes"] == "备份已完成，路径 /backup/20240101.sql"
    assert response["item"]["updated_by"] == "operator_user"


def test_cli_checklist_block_submit(runner: CliRunner, db_path):
    """Test that incomplete required checklist blocks submit via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "submit", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "checklist_incomplete"
    assert "确认备份已完成" in error["error"]


def test_cli_checklist_allow_submit_when_complete(runner: CliRunner, db_path):
    """Test that submit works when required checklist is complete via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "submit", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True


def test_cli_checklist_block_run(runner: CliRunner, db_path):
    """Test that incomplete required checklist blocks run via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--item", "验证测试环境:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "submit", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["code"] == "checklist_incomplete"

    item_id_2 = items[1]["id"]
    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id_2),
        "--completed", "true",
        "--as-user", "operator_user",
    ])

    runner.invoke(cli, [
        "--db", db_path,
        "task", "submit", str(task_id),
        "--as-user", "operator_user",
    ])

    runner.invoke(cli, [
        "--db", db_path,
        "task", "approve", str(task_id),
        "--as-user", "approver_user",
    ])

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "false",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "run", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["code"] == "checklist_incomplete"


def test_cli_checklist_permission_denied_non_operator(runner: CliRunner, db_path):
    """Test that non-operator cannot update checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "no_role_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "checklist_permission_denied"


def test_cli_checklist_permission_denied_not_owner(runner: CliRunner, db_path):
    """Test that non-owner operator cannot update checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path, as_user="operator_user")

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user_2",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "checklist_not_owner"


def test_cli_checklist_approver_can_view_but_not_update(runner: CliRunner, db_path):
    """Test that approver can view but not update checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "approver_user",
    ])
    assert result.exit_code == 0, result.output
    items = json.loads(result.output)
    assert len(items) == 1

    item_id = items[0]["id"]
    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "approver_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["code"] == "checklist_permission_denied"


def test_cli_checklist_admin_can_update_any(runner: CliRunner, db_path):
    """Test that admin can update any checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path, as_user="operator_user")

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "admin_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "admin_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "admin_user",
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["success"] is True
    assert response["item"]["completed"] is True


def test_cli_checklist_audit_log(runner: CliRunner, db_path):
    """Test that checklist operations are audited via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "operator_user",
    ])

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "非法操作:true",
        "--as-user", "no_role_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "audit", "list",
    ])
    assert result.exit_code == 0
    audits = json.loads(result.output)

    checklist_audits = [a for a in audits if "checklist" in a["action"]]
    assert len(checklist_audits) >= 3

    actions = [a["action"] for a in checklist_audits]
    assert "checklist_set" in actions
    assert "checklist_updated" in actions
    assert "checklist_update_denied" in actions


def test_cli_checklist_import_export(runner: CliRunner, db_path):
    """Test that checklist is included in export/import via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--item", "验证测试环境:false",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--notes", "备份完成",
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

        with open(export_path, encoding="utf-8") as f:
            plan = json.load(f)
        assert "checklist_items" in plan
        assert len(plan["checklist_items"]) == 2
        assert plan["checklist_items"][0]["name"] == "确认备份已完成"
        assert plan["checklist_items"][0]["completed"] is True
        assert plan["checklist_items"][0]["notes"] == "备份完成"

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
            tasks = json.loads(result.output)
            new_task_id = tasks[0]["id"]

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "task", "checklist", "view", str(new_task_id),
                "--as-user", "admin_user",
            ])
            imported_items = json.loads(result.output)
            assert len(imported_items) == 2
            assert imported_items[0]["name"] == "确认备份已完成"
            assert imported_items[0]["required"] is True
            assert imported_items[0]["completed"] is True
            assert imported_items[0]["notes"] == "备份完成"
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


def test_cli_checklist_import_conflict(runner: CliRunner, db_path):
    """Test that checklist conflicts are detected during import via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "update", str(task_id), str(item_id),
        "--completed", "true",
        "--as-user", "operator_user",
    ])

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        runner.invoke(cli, [
            "--db", db_path,
            "plan", "export", export_path,
        ])

        with open(export_path, encoding="utf-8") as f:
            plan = json.load(f)
        plan["checklist_items"][0]["completed"] = False
        plan["checklist_items"][0]["required"] = False
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(plan, f)

        result = runner.invoke(cli, [
            "--db", db_path,
            "--format", "json",
            "plan", "import", export_path,
        ])
        assert result.exit_code != 0
        error = json.loads(result.output)
        assert error["success"] is False
        assert error["code"] == "checklist_conflict"
        assert "Checklist conflict" in error["error"]
    finally:
        try:
            os.unlink(export_path)
        except OSError:
            pass


def test_cli_checklist_import_dry_run(runner: CliRunner, db_path):
    """Test that import dry-run does not persist checklist via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        runner.invoke(cli, [
            "--db", db_path,
            "plan", "export", export_path,
        ])

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--dry-run",
            ])
            assert result.exit_code == 0, result.output
            response = json.loads(result.output)
            assert response["dry_run"] is True
            assert response["checklist_items_to_import"] == 1

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "task", "list",
            ])
            tasks = json.loads(result.output)
            assert len(tasks) == 0
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


def test_cli_checklist_set_missing_items(runner: CliRunner, db_path):
    """Test that checklist set with no items returns error via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "set", str(task_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "missing_checklist_items"


def test_cli_checklist_update_missing_options(runner: CliRunner, db_path):
    """Test that checklist update with no options returns error via CLI."""
    task_id = _create_task_for_checklist(runner, db_path)

    runner.invoke(cli, [
        "--db", db_path,
        "task", "checklist", "set", str(task_id),
        "--item", "确认备份已完成:true",
        "--as-user", "operator_user",
    ])

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "view", str(task_id),
        "--as-user", "operator_user",
    ])
    items = json.loads(result.output)
    item_id = items[0]["id"]

    result = runner.invoke(cli, [
        "--db", db_path,
        "--format", "json",
        "task", "checklist", "update", str(task_id), str(item_id),
        "--as-user", "operator_user",
    ])
    assert result.exit_code != 0
    error = json.loads(result.output)
    assert error["success"] is False
    assert error["code"] == "missing_update_option"
