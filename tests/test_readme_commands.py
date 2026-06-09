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

    def test_checklist_full_workflow(self, temp_dir):
        """Test full checklist workflow with required items blocking from README.

        Verifies:
        - Set checklist with --item flags
        - View checklist (JSON format)
        - Submit blocked by incomplete required items
        - Update checklist items (mark complete, add notes)
        - Submit succeeds after all required items complete
        - Approve and run succeed
        - Checklist persists across connections
        - Audit logs recorded for all operations
        """
        db_path = os.path.join(temp_dir, "verify_checklist.db")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Create window and task
        result = run_command([
            "--db", db_path,
            "window", "create",
            "--name", "checklist-test-window",
            "--description", "Checklist workflow test",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        result = run_command([
            "--db", db_path,
            "task", "create",
            "--name", "checklist-test-task",
            "--description", "Test pre-execution checklist",
            "--sql", "UPDATE users SET email = LOWER(email)",
            "--rollback-sql", "UPDATE users SET email = UPPER(email)",
            "--window-id", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        # 2. Set checklist with required items
        result = run_command([
            "--db", db_path,
            "task", "checklist", "set", "1",
            "--item", "Verify backup exists:true",
            "--item", "Test SQL on staging:true",
            "--item", "Notify stakeholders:false",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist set failed: {result.stderr}"

        # 3. View checklist (JSON format)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "view", "1",
        ])
        assert result.returncode == 0, f"Checklist view failed: {result.stderr}"
        checklist = json.loads(result.stdout)
        assert len(checklist) == 3
        assert checklist[0]["name"] == "Verify backup exists"
        assert checklist[0]["required"] is True
        assert checklist[0]["completed"] is False
        assert checklist[2]["name"] == "Notify stakeholders"
        assert checklist[2]["required"] is False

        # 4. Try to submit without completing required items (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "submit", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode != 0, "Submit should have failed"
        error = json.loads(result.stderr)
        assert error["success"] is False
        assert error["code"] == "checklist_incomplete"
        assert "required checklist items not completed" in error["error"]
        assert "Verify backup exists" in error["error"]
        assert "Test SQL on staging" in error["error"]

        # 5. Complete first required item with notes
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "update", "1", "1",
            "--completed", "true",
            "--notes", "Backup verified at s3://backup/2025-01-15",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist update failed: {result.stderr}"
        update_result = json.loads(result.stdout)
        assert update_result["success"] is True
        assert update_result["item"]["completed"] is True
        assert update_result["item"]["notes"] == "Backup verified at s3://backup/2025-01-15"
        assert update_result["item"]["updated_by"] == "operator_user"

        # 6. Try to submit again - still missing one required item (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "submit", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode != 0, "Submit should have failed"
        error = json.loads(result.stderr)
        assert error["code"] == "checklist_incomplete"
        assert "required checklist items not completed" in error["error"]
        assert "Test SQL on staging" in error["error"]
        assert "Verify backup exists" not in error["error"]

        # 7. Complete second required item
        result = run_command([
            "--db", db_path,
            "task", "checklist", "update", "1", "2",
            "--completed", "true",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist update failed: {result.stderr}"

        # 8. Submit now succeeds
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "submit", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Submit failed: {result.stderr}"
        submit_result = json.loads(result.stdout)
        assert submit_result["success"] is True
        assert submit_result["status"] == "pending_approval"

        # 9. Approve and run
        result = run_command([
            "--db", db_path,
            "task", "approve", "1",
            "--as-user", "approver_user",
        ])
        assert result.returncode == 0, f"Approve failed: {result.stderr}"

        result = run_command([
            "--db", db_path,
            "task", "run", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Run failed: {result.stderr}"

        # 10. Verify checklist persistence across restart
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "view", "1",
        ])
        assert result.returncode == 0, f"Checklist view after restart failed: {result.stderr}"
        checklist_after = json.loads(result.stdout)
        assert checklist_after[0]["completed"] is True
        assert checklist_after[1]["completed"] is True
        assert checklist_after[0]["notes"] == "Backup verified at s3://backup/2025-01-15"

        # 11. Verify audit logs for checklist operations
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "audit", "list",
            "--task-id", "1",
        ])
        assert result.returncode == 0, f"Audit list failed: {result.stderr}"
        audits = json.loads(result.stdout)
        actions = [a["action"] for a in audits]
        assert "checklist_set" in actions
        assert "checklist_updated" in actions

    def test_checklist_permission_enforcement(self, temp_dir):
        """Test checklist permission enforcement and audit logging from README.

        Verifies:
        - Non-owner operator cannot update checklist
        - Approver cannot update checklist (read-only)
        - Approver can view checklist
        - Admin can update any checklist
        - Failed operations are audited
        """
        db_path = os.path.join(temp_dir, "verify_checklist_perms.db")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Setup: Create window and task as operator_user
        result = run_command([
            "--db", db_path,
            "window", "create",
            "--name", "perm-test-window",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        result = run_command([
            "--db", db_path,
            "task", "create",
            "--name", "perm-test-task",
            "--description", "Test",
            "--sql", "SELECT 1",
            "--rollback-sql", "SELECT 1",
            "--window-id", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        result = run_command([
            "--db", db_path,
            "task", "checklist", "set", "1",
            "--item", "Check backup:true",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist set failed: {result.stderr}"

        # 2. operator_user_2 (different operator) tries to update (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "update", "1", "1",
            "--completed", "true",
            "--as-user", "operator_user_2",
        ])
        assert result.returncode != 0, "Non-owner update should have failed"
        error = json.loads(result.stderr)
        assert error["code"] == "checklist_not_owner"
        assert "cannot update checklist for task created by" in error["error"]
        assert "operator_user" in error["error"]

        # 3. approver_user tries to update (should fail)
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "update", "1", "1",
            "--completed", "true",
            "--as-user", "approver_user",
        ])
        assert result.returncode != 0, "Approver update should have failed"
        error = json.loads(result.stderr)
        assert error["code"] == "checklist_permission_denied"
        assert "does not have operator role to update checklist" in error["error"]

        # 4. approver_user CAN view the checklist
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "view", "1",
            "--as-user", "approver_user",
        ])
        assert result.returncode == 0, "Approver view should work"
        checklist = json.loads(result.stdout)
        assert len(checklist) == 1

        # 5. admin_user CAN update any checklist
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "task", "checklist", "update", "1", "1",
            "--completed", "true",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, "Admin update should work"
        update_result = json.loads(result.stdout)
        assert update_result["item"]["completed"] is True
        assert update_result["item"]["updated_by"] == "admin_user"

        # 6. Verify audit logs show denied attempts
        result = run_command([
            "--db", db_path,
            "--format", "json",
            "audit", "list",
            "--task-id", "1",
        ])
        assert result.returncode == 0, f"Audit list failed: {result.stderr}"
        audits = json.loads(result.stdout)
        actions = [a["action"] for a in audits]
        assert "checklist_set" in actions
        assert "checklist_update_denied" in actions
        assert "checklist_updated" in actions

        # Verify there are two denied entries
        denied_audits = [a for a in audits if a["action"] == "checklist_update_denied"]
        assert len(denied_audits) == 2

    def test_checklist_export_import(self, temp_dir):
        """Test checklist export/import with conflict detection from README.

        Verifies:
        - Export includes checklist items with all fields
        - Import to new database preserves checklist state
        - Conflict detection works when local checklist differs
        - Dry-run does not persist changes
        - --ignore-checklist-conflict flag works
        """
        db_export = os.path.join(temp_dir, "verify_checklist_export.db")
        db_import = os.path.join(temp_dir, "verify_checklist_import.db")
        db_dry = os.path.join(temp_dir, "verify_checklist_dry.db")
        plan_file = os.path.join(temp_dir, "checklist_plan.json")
        start = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Setup: Create window, task, and checklist
        result = run_command([
            "--db", db_export,
            "window", "create",
            "--name", "export-test-window",
            "--start", start,
            "--end", end,
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Window create failed: {result.stderr}"

        result = run_command([
            "--db", db_export,
            "task", "create",
            "--name", "export-test-task",
            "--description", "test",
            "--sql", "SELECT 1",
            "--rollback-sql", "SELECT 1",
            "--window-id", "1",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Task create failed: {result.stderr}"

        result = run_command([
            "--db", db_export,
            "task", "checklist", "set", "1",
            "--item", "Verify backup:true",
            "--item", "Test SQL:true",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist set failed: {result.stderr}"

        result = run_command([
            "--db", db_export,
            "task", "checklist", "update", "1", "1",
            "--completed", "true",
            "--notes", "Backup verified",
            "--as-user", "operator_user",
        ])
        assert result.returncode == 0, f"Checklist update failed: {result.stderr}"

        # 2. Export plan (includes checklist)
        result = run_command([
            "--db", db_export,
            "plan", "export", plan_file,
        ])
        assert result.returncode == 0, f"Plan export failed: {result.stderr}"

        # 3. Verify exported checklist in file
        with open(plan_file, "r", encoding="utf-8") as f:
            plan = json.load(f)
        assert "checklist_items" in plan
        assert len(plan["checklist_items"]) == 2
        assert plan["checklist_items"][0]["name"] == "Verify backup"
        assert plan["checklist_items"][0]["completed"] is True
        assert plan["checklist_items"][0]["notes"] == "Backup verified"
        assert plan["checklist_items"][0]["updated_by"] == "operator_user"
        assert plan["checklist_items"][1]["name"] == "Test SQL"
        assert plan["checklist_items"][1]["completed"] is False

        # 4. Import to new database (works fine)
        result = run_command([
            "--db", db_import,
            "--format", "json",
            "plan", "import", plan_file,
            "--as-user", "importer",
        ])
        assert result.returncode == 0, f"Plan import failed: {result.stderr}"
        import_result = json.loads(result.stdout)
        assert import_result["success"] is True
        assert import_result["checklist_items_imported"] == 2

        # Verify imported checklist (use admin_user which has default permissions)
        result = run_command([
            "--db", db_import,
            "--format", "json",
            "task", "checklist", "view", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Checklist view failed: {result.stderr}"
        imported = json.loads(result.stdout)
        assert len(imported) == 2
        assert imported[0]["name"] == "Verify backup"
        assert imported[0]["completed"] is True
        assert imported[0]["notes"] == "Backup verified"

        # 5. Mark imported checklist item as incomplete locally to create conflict
        result = run_command([
            "--db", db_import,
            "task", "checklist", "update", "1", "1",
            "--completed", "false",
            "--as-user", "admin_user",
        ])
        assert result.returncode == 0, f"Checklist update failed: {result.stderr}"

        # Modify the plan to use different window name and time to avoid window conflicts on re-import
        with open(plan_file, "r", encoding="utf-8") as f:
            plan_for_reimport = json.load(f)
        plan_for_reimport["windows"][0]["name"] = "conflict-test-window"
        far_future = (datetime.utcnow() + timedelta(days=365)).isoformat()
        far_future_end = (datetime.utcnow() + timedelta(days=365, hours=2)).isoformat()
        plan_for_reimport["windows"][0]["start_time"] = far_future
        plan_for_reimport["windows"][0]["end_time"] = far_future_end
        plan_for_reimport["tasks"][0]["window_name"] = "conflict-test-window"
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan_for_reimport, f)

        # 6. Dry-run re-import shows conflict
        result = run_command([
            "--db", db_import,
            "--format", "json",
            "plan", "import", plan_file,
            "--dry-run",
            "--as-user", "importer",
        ])
        assert result.returncode == 0, f"Dry-run import failed: {result.stderr}"
        dry_run = json.loads(result.stdout)
        assert dry_run["success"] is True
        assert dry_run["dry_run"] is True
        assert len(dry_run["checklist_conflicts"]) >= 1
        assert "Verify backup" in dry_run["checklist_conflicts"][0]
        assert "local required=True, completed=False" in dry_run["checklist_conflicts"][0]
        assert "imported required=True, completed=True" in dry_run["checklist_conflicts"][0]

        # 7. Actual re-import fails due to conflict
        result = run_command([
            "--db", db_import,
            "--format", "json",
            "plan", "import", plan_file,
            "--as-user", "importer",
        ])
        assert result.returncode != 0, "Import should have failed due to conflict"
        error = json.loads(result.stderr)
        assert error["code"] == "checklist_conflict"
        assert "Use --ignore-checklist-conflict to proceed" in error["error"]

        # 8. Import with --ignore-checklist-conflict succeeds
        result = run_command([
            "--db", db_import,
            "--format", "json",
            "plan", "import", plan_file,
            "--ignore-checklist-conflict",
            "--as-user", "importer",
        ])
        assert result.returncode == 0, f"Import with ignore flag failed: {result.stderr}"
        import_result = json.loads(result.stdout)
        assert import_result["success"] is True
        assert len(import_result["checklist_conflicts_resolved"]) >= 1

        # 9. Verify dry-run never persisted changes
        result = run_command([
            "--db", db_dry,
            "plan", "import", plan_file,
            "--dry-run",
            "--as-user", "importer",
        ])
        assert result.returncode == 0, f"Dry-run failed: {result.stderr}"

        result = run_command([
            "--db", db_dry,
            "--format", "json",
            "task", "checklist", "view", "1",
            "--as-user", "admin_user",
        ])
        assert result.returncode != 0, "Dry-run should not have persisted data"
        error = json.loads(result.stderr)
        assert error["code"] == "task_not_found"
