"""Tests that execute README verification commands directly as subprocesses.

These tests ensure the documentation commands are actually copy-pasteable and work correctly.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


def run_command(args, cwd=None):
    """Run a command and return the result."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..", "src")
    env["PYTHONIOENCODING"] = "utf-8"
    if sys.platform == "win32":
        env["CHCP"] = "65001"
    result = subprocess.run(
        [sys.executable, "-m", "repair_cli"] + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
    )
    return result


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestReadmeCommands:
    """Test all README verification commands as actual subprocess calls."""

    def test_admin_self_workflow(self, temp_dir):
        """Test admin self-approval/execution workflow from README.

        Verifies:
        - Admin can create their own task
        - Admin can submit their own task
        - Admin can approve their own task
        - Admin can execute their own task
        - Final status is 'succeeded' with correct metadata
        """
        db_path = os.path.join(temp_dir, "verify_admin.db")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Create window (admin)
        result = run_command([
            "--db", db_path,
            "window", "create",
            "--name", "admin-test-window",
            "--description", "Admin self-workflow test",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        # 2. Admin creates their own task
        result = run_command([
            "--db", db_path,
            "task", "create",
            "--name", "admin-self-task",
            "--description", "Test admin self-approval",
            "--sql", "UPDATE users SET email = LOWER(email)",
            "--rollback-sql", "UPDATE users SET email = UPPER(email)",
            "--window-id", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        # 3. Admin submits their own task
        result = run_command([
            "--db", db_path,
            "task", "submit", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Task submit failed: {result.stderr}"

        # 4. Admin approves their own task
        result = run_command([
            "--db", db_path,
            "task", "approve", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Task approve failed: {result.stderr}"

        # 5. Admin executes their own task
        result = run_command([
            "--db", db_path,
            "task", "run", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Task run failed: {result.stderr}"

        # 6. Verify status with JSON output
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "show", "1",
        ])
        assert result.returncode == 0, f"Task show failed: {result.stderr}"

        task = json.loads(result.stdout)
        assert task["id"] == 1
        assert task["name"] == "admin-self-task"
        assert task["status"] == "succeeded"
        assert task["created_by"] == "admin_user"
        assert task["approved_by"] == "admin_user"
        assert task["executed_by"] == "admin_user"

    def test_failed_rollback_audit_logging(self, temp_dir):
        """Test failed rollback audit logging from README.

        Verifies:
        - Rollback before execution fails
        - Task status remains unchanged (draft)
        - Audit log contains the failure record with correct fields
        """
        db_path = os.path.join(temp_dir, "verify_rollback.db")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Create window
        result = run_command([
            "--db", db_path,
            "window", "create",
            "--name", "rollback-test-window",
            "--description", "Rollback audit test",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        # 2. Create task
        result = run_command([
            "--db", db_path,
            "task", "create",
            "--name", "rollback-test-task",
            "--description", "Test failed rollback audit",
            "--sql", "UPDATE ...",
            "--rollback-sql", "UPDATE ...",
            "--window-id", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        # 3. Get initial audit count
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "audit", "list",
            "--task-id", "1",
        ])
        assert result.returncode == 0, f"Audit list failed: {result.stderr}"
        initial_audits = json.loads(result.stdout)
        initial_count = len(initial_audits)
        assert initial_count == 1  # task_created

        # 4. Try to rollback before execution (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "rollback", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode != 0, "Rollback should have failed"
        error = json.loads(result.stderr)
        assert error["success"] is False
        assert "must be 'succeeded' or 'failed' to rollback" in error["error"]

        # 5. Verify task status is unchanged
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "show", "1",
        ])
        assert result.returncode == 0, f"Task show failed: {result.stderr}"
        task = json.loads(result.stdout)
        assert task["status"] == "draft"

        # 6. Verify audit log has the failure record
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "audit", "list",
            "--task-id", "1",
        ])
        assert result.returncode == 0, f"Audit list failed: {result.stderr}"
        audits = json.loads(result.stdout)
        assert len(audits) == initial_count + 1

        # Find the rollback rejected audit entry
        rejected_audits = [a for a in audits if a["action"] == "task_rollback_rejected"]
        assert len(rejected_audits) == 1
        rejected = rejected_audits[0]
        assert rejected["task_id"] == 1
        assert rejected["actor"] == "operator_user"
        assert rejected["old_status"] == "draft"
        assert rejected["new_status"] == "draft"
        assert "must be 'succeeded' or 'failed' to rollback" in rejected["details"]

    def test_regular_user_self_approval_blocked(self, temp_dir):
        """Test regular user self-approval is still blocked from README.

        Verifies:
        - Regular user cannot approve their own task
        - Error JSON has correct format and message
        """
        db_path = os.path.join(temp_dir, "verify_regular.db")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Create window
        result = run_command([
            "--db", db_path,
            "window", "create",
            "--name", "regular-test-window",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        # 2. Create task as approver (so they can try to approve their own task)
        result = run_command([
            "--db", db_path,
            "task", "create",
            "--name", "regular-self-task",
            "--description", "Test regular user self-approval blocked",
            "--sql", "UPDATE ...",
            "--rollback-sql", "UPDATE ...",
            "--window-id", "1",
            "--as-user", "approver_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        # 3. Submit task
        result = run_command([
            "--db", db_path,
            "task", "submit", "1",
            "--as-user", "approver_user",
        ])
        assert result.returncode == 0, f"Task submit failed: {result.stderr}"

        # 4. Regular user (approver) tries to approve their own task (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "approve", "1",
            "--as-user", "approver_user",
        ])
        assert result.returncode != 0, "Self-approval should have failed"
        error = json.loads(result.stderr)
        assert error["success"] is False
        assert "cannot approve their own task" in error["error"]
        assert error["code"] == "self_approval_not_allowed"

    def test_format_option_misplaced_fails(self, temp_dir):
        """Test that --format after subcommand fails as expected.

        This confirms why the README fix was needed.
        """
        db_path = os.path.join(temp_dir, "test_misplaced.db")

        # This should FAIL because --format is after the subcommand
        result = run_command([
            "--db", db_path,
            "window", "list",
            "--format", "json",
        ])
        assert result.returncode != 0
        # Click writes option errors to stderr
        # Handle both old ("No such option: --format") and new ("No such option '--format'.") Click formats
        assert ("--format" in result.stderr and "such option" in result.stderr) or \
               ("--format" in result.stdout and "such option" in result.stdout)

        # This should SUCCEED because --format is before the subcommand
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "window", "list",
        ])
        assert result.returncode == 0
        # Should be valid JSON
        windows = json.loads(result.stdout)
        assert isinstance(windows, list)
