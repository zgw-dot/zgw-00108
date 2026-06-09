"""Tests for export/import functionality."""

import json
import tempfile
import os
from datetime import datetime, timedelta

import pytest

from repair_cli.export_import import ExportImportService
from repair_cli.models import (
    ChecklistItem,
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.persistence import Database, PolicyRepository
from repair_cli.validation import ValidationError


def test_export_plan(export_service, service, future_window, pending_task):
    """Test exporting a plan."""
    plan = export_service.export_plan()
    assert plan["version"] == "1.0"
    assert "windows" in plan
    assert "tasks" in plan
    assert "audit_logs" in plan

    window_names = [w["name"] for w in plan["windows"]]
    assert future_window.name in window_names

    task_names = [t["name"] for t in plan["tasks"]]
    assert pending_task.name in task_names


def test_export_specific_tasks(export_service, service, future_window, draft_task, pending_task):
    """Test exporting specific tasks."""
    plan = export_service.export_plan(task_ids=[pending_task.id])
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["id"] == pending_task.id


def test_import_plan_to_new_database(service, export_service, db_path, active_window, succeeded_task):
    """Test exporting and importing to a fresh database."""
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        result = new_export.import_plan(plan, actor="importer")
        assert result["tasks_imported"] >= 1
        assert result["windows_imported"] >= 1

        new_tasks = new_export.task_repo.list()
        assert len(new_tasks) >= 1

        original_task = service.task_repo.get(succeeded_task.id)
        imported_task = next(t for t in new_tasks if t.name == original_task.name)
        assert imported_task.status == original_task.status
        assert imported_task.sql == original_task.sql
        assert imported_task.approved_by == original_task.approved_by

        original_window = service.window_repo.get(active_window.id)
        new_windows = new_export.window_repo.list()
        imported_window = next(w for w in new_windows if w.name == original_window.name)
        assert imported_window.start_time == original_window.start_time
        assert imported_window.end_time == original_window.end_time

        audits = new_export.audit_repo.list()
        assert len(audits) > 0
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_validation_nonexistent_window(export_service, service, future_window, draft_task):
    """Test that importing fails when referencing non-existent window."""
    plan = export_service.export_plan()

    for t in plan["tasks"]:
        t["window_id"] = 9999
        t["window_name"] = "nonexistent"

    errors = export_service.validate_import_plan(plan)
    assert len(errors) >= 1
    assert any("non-existent window" in e.message for e in errors)


def test_import_validation_window_overlap(export_service, service, future_window):
    """Test that importing fails when windows overlap."""
    service.create_task(RepairTask(
        name="overlap-test-task",
        description="test",
        created_by="operator_user",
        sql="UPDATE ...",
        window_id=future_window.id,
    ))
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        new_window = MaintenanceWindow(
            name="existing-in-new-db",
            start_time=future_window.start_time,
            end_time=future_window.end_time,
            created_by="admin",
        )
        new_export.window_repo.create(new_window)

        errors = new_export.validate_import_plan(plan)
        assert len(errors) >= 1
        assert any("overlaps" in e.message for e in errors)
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_rollback_on_failure(export_service, service, future_window, draft_task):
    """Test that import is rolled back on failure - no partial data."""
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        plan["tasks"].append({
            "id": 9999,
            "name": "invalid-task",
            "description": "",
            "created_by": "test",
            "sql": "",
            "window_id": 9999,
            "window_name": "nonexistent",
            "status": "draft",
        })

        with pytest.raises(ValidationError, match="Import validation failed"):
            new_export.import_plan(plan)

        tasks_after = new_export.task_repo.list()
        windows_after = new_export.window_repo.list()
        assert len(tasks_after) == 0
        assert len(windows_after) == 0
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_status_consistency(service, export_service, active_window, succeeded_task):
    """Test that task status is preserved after import."""
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        new_export.import_plan(plan)

        original = service.task_repo.get(succeeded_task.id)
        imported_tasks = new_export.task_repo.list()
        imported = next(t for t in imported_tasks if t.name == original.name)

        assert imported.status == original.status
        assert imported.status == TaskStatus.SUCCEEDED
        assert imported.approved_by == original.approved_by
        assert imported.executed_by == original.executed_by
        assert imported.rollback_by == original.rollback_by
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_export_to_file_and_import(export_service, service, future_window, draft_task):
    """Test exporting to file and importing back."""
    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        export_service.export_to_file(export_path)

        with open(export_path) as f:
            loaded = json.load(f)

        assert loaded["version"] == "1.0"
        assert len(loaded["tasks"]) >= 1

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            new_db = Database(f"sqlite:///{new_db_path}")
            new_db.init_db()
            new_db.init_default_roles()
            new_export = ExportImportService(new_db)

            result = new_export.import_from_file(export_path, actor="test")
            assert result["tasks_imported"] >= 1
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


def test_import_validation_detects_config_changes(service, export_service, future_window, draft_task):
    """Test that conflict detection changes after config changes."""
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        errors = new_export.validate_import_plan(plan)
        assert len(errors) == 0

        new_window = MaintenanceWindow(
            name="conflicting-window",
            start_time=future_window.start_time,
            end_time=future_window.end_time,
            created_by="admin",
        )
        new_export.window_repo.create(new_window)

        errors_after = new_export.validate_import_plan(plan)
        assert len(errors_after) >= 1
        assert any("overlaps" in e.message for e in errors_after)
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_preserves_audit_logs(service, export_service, active_window, succeeded_task):
    """Test that audit logs are preserved during import."""
    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)

        result = new_export.import_plan(plan)

        original_audits = service.audit_repo.list_by_task(succeeded_task.id)
        imported_tasks = new_export.task_repo.list()
        imported_task = next(t for t in imported_tasks if t.name == succeeded_task.name)
        imported_audits = new_export.audit_repo.list_by_task(imported_task.id)

        assert len(imported_audits) == len(original_audits)
        original_actions = [a.action for a in original_audits]
        imported_actions = [a.action for a in imported_audits]
        assert imported_actions == original_actions
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_export_plan_includes_policy(export_service, service):
    """Test that exported plan includes policy information."""
    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=False,
    )

    plan = export_service.export_plan()
    assert "policy" in plan
    assert plan["policy"]["allow_admin_self_approval"] is False
    assert plan["policy"]["require_different_approver"] is False
    assert plan["policy"]["updated_by"] == "admin_user"


def test_import_policy_to_new_database(export_service, service, future_window, pending_task):
    """Test that policy is imported to a new database."""
    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=False,
    )

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_db.init_default_policy()
        new_export = ExportImportService(new_db)
        new_policy_repo = PolicyRepository(new_db)

        policy_before = new_policy_repo.get()
        assert policy_before.allow_admin_self_approval is True

        result = new_export.import_plan(plan, actor="importer")
        assert result["policy_imported"] is True

        policy_after = new_policy_repo.get()
        assert policy_after.allow_admin_self_approval is False
        assert policy_after.require_different_approver is False
        assert policy_after.updated_by == "importer"
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_policy_conflict_detected(export_service, service):
    """Test that policy conflict is detected during import validation."""
    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=True,
    )

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_db.init_default_policy()
        new_export = ExportImportService(new_db)
        new_policy_repo = PolicyRepository(new_db)

        new_policy_repo.update(
            allow_admin_self_approval=True,
            require_different_approver=False,
            updated_by="existing_admin",
        )

        errors = new_export.validate_import_plan(plan)
        policy_conflicts = [e for e in errors if e.code == "policy_conflict"]
        assert len(policy_conflicts) == 1
        assert "allow_admin_self_approval: local=True, imported=False" in policy_conflicts[0].message
        assert "require_different_approver: local=False, imported=True" in policy_conflicts[0].message
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_policy_dry_run_does_not_modify(export_service, service):
    """Test that dry run import does not modify local policy."""
    from click.testing import CliRunner
    from repair_cli.cli import cli

    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=False,
    )

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        export_service.export_to_file(export_path)

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            runner = CliRunner()

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--dry-run",
                "--as-user", "importer",
            ])
            assert result.exit_code == 0, result.output

            new_db = Database(f"sqlite:///{new_db_path}")
            new_db.init_db()
            new_db.init_default_policy()
            new_policy_repo = PolicyRepository(new_db)

            policy = new_policy_repo.get()
            assert policy.allow_admin_self_approval is True
            assert policy.require_different_approver is True
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


def test_import_policy_with_ignore_conflict_flag(export_service, service):
    """Test that --ignore-policy-conflict flag allows proceeding with import."""
    from click.testing import CliRunner
    from repair_cli.cli import cli

    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=False,
    )

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        export_service.export_to_file(export_path)

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            new_db = Database(f"sqlite:///{new_db_path}")
            new_db.init_db()
            new_db.init_default_roles()
            new_db.init_default_policy()
            new_policy_repo = PolicyRepository(new_db)
            new_policy_repo.update(
                allow_admin_self_approval=True,
                require_different_approver=True,
                updated_by="existing_admin",
            )

            runner = CliRunner()

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--as-user", "importer",
            ])
            assert result.exit_code != 0
            error = json.loads(result.output)
            assert error["code"] == "policy_conflict"

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--ignore-policy-conflict",
                "--as-user", "importer",
            ])
            assert result.exit_code == 0, result.output

            policy = new_policy_repo.get()
            assert policy.allow_admin_self_approval is False
            assert policy.require_different_approver is False
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


def test_imported_policy_affects_approval(export_service, service, active_window, role_repo):
    """Test that imported policy actually affects approval behavior."""
    from repair_cli.service import RepairService

    role_repo.set_role("test_admin", Role.ADMIN)
    service.update_policy(
        actor="admin_user",
        allow_admin_self_approval=False,
        require_different_approver=True,
    )

    task = service.create_task(RepairTask(
        name="imported-policy-test",
        description="test",
        created_by="test_admin",
        sql="UPDATE ...",
        rollback_sql="UPDATE ...",
        window_id=active_window.id,
    ))
    service.submit_for_approval(task.id, "test_admin")

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_db.init_default_policy()
        new_export = ExportImportService(new_db)
        new_export.import_plan(plan, actor="importer")

        new_service = RepairService(new_db)
        new_service.role_repo.set_role("test_admin", Role.ADMIN)

        imported_tasks = new_service.task_repo.list()
        imported_task = next(t for t in imported_tasks if t.name == "imported-policy-test")

        with pytest.raises(ValidationError, match="cannot approve their own task"):
            new_service.approve_task(imported_task.id, "test_admin")
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_export_plan_includes_checklist(export_service, service, future_window, draft_task):
    """Test that exported plan includes checklist items."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Test on staging", required=False),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=saved[0].id,
        actor="operator_user",
        completed=True,
        notes="Backup verified",
    )

    plan = export_service.export_plan()
    assert "checklist_items" in plan
    assert len(plan["checklist_items"]) == 2

    checklist_by_name = {c["name"]: c for c in plan["checklist_items"]}
    assert "Verify backup" in checklist_by_name
    assert checklist_by_name["Verify backup"]["required"] is True
    assert checklist_by_name["Verify backup"]["completed"] is True
    assert checklist_by_name["Verify backup"]["notes"] == "Backup verified"
    assert checklist_by_name["Verify backup"]["updated_by"] == "operator_user"

    assert "Test on staging" in checklist_by_name
    assert checklist_by_name["Test on staging"]["required"] is False
    assert checklist_by_name["Test on staging"]["completed"] is False


def test_import_checklist_to_new_database(export_service, service, future_window, draft_task):
    """Test that checklist is imported to a new database."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
        ChecklistItem(task_id=draft_task.id, name="Test on staging", required=False),
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

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)
        from repair_cli.service import RepairService
        new_service = RepairService(new_db)

        result = new_export.import_plan(plan, actor="importer")
        assert result["checklist_items_imported"] == 2

        imported_tasks = new_service.task_repo.list()
        imported_task = next(t for t in imported_tasks if t.name == draft_task.name)

        imported_checklist = new_service.get_checklist(imported_task.id, "operator_user")
        assert len(imported_checklist) == 2

        by_name = {c.name: c for c in imported_checklist}
        assert by_name["Verify backup"].required is True
        assert by_name["Verify backup"].completed is True
        assert by_name["Verify backup"].notes == "Done"
        assert by_name["Test on staging"].required is False
        assert by_name["Test on staging"].completed is False
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_checklist_conflict_detected(export_service, service, future_window, draft_task):
    """Test that checklist conflict is detected during import validation."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True, completed=False),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=saved[0].id,
        actor="operator_user",
        completed=True,
    )

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)
        from repair_cli.service import RepairService
        new_service = RepairService(new_db)

        new_export.import_plan(plan, actor="importer")

        imported_tasks = new_service.task_repo.list()
        imported_task = next(t for t in imported_tasks if t.name == draft_task.name)
        imported_items = new_service.get_checklist(imported_task.id, "admin_user")
        new_service.checklist_repo.update_item(
            item_id=imported_items[0].id,
            actor="local_user",
            completed=False,
        )

        errors = new_export.validate_import_plan(plan)
        checklist_conflicts = [e for e in errors if e.code == "checklist_conflict"]
        assert len(checklist_conflicts) >= 1
        assert "Verify backup" in checklist_conflicts[0].message
        assert "completed=False" in checklist_conflicts[0].message
        assert "completed=True" in checklist_conflicts[0].message
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_checklist_dry_run_no_changes(export_service, service, future_window, draft_task):
    """Test that dry run import does not modify checklist in database."""
    from click.testing import CliRunner
    from repair_cli.cli import cli

    items = [
        ChecklistItem(task_id=draft_task.id, name="Verify backup", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        export_service.export_to_file(export_path)

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            runner = CliRunner()

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--dry-run",
                "--as-user", "importer",
            ])
            assert result.exit_code == 0, result.output
            output = json.loads(result.output)
            assert output["checklist_items_to_import"] == 1

            new_db = Database(f"sqlite:///{new_db_path}")
            new_db.init_db()
            from repair_cli.service import RepairService
            new_service = RepairService(new_db)

            tasks = new_service.task_repo.list()
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


def test_import_checklist_with_ignore_conflict_flag(export_service, service, future_window, draft_task):
    """Test that --ignore-checklist-conflict flag allows proceeding with import."""
    from click.testing import CliRunner
    from repair_cli.cli import cli

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
    )

    fd, export_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        export_service.export_to_file(export_path)

        fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            new_db = Database(f"sqlite:///{new_db_path}")
            new_db.init_db()
            new_db.init_default_roles()
            new_export = ExportImportService(new_db)
            from repair_cli.service import RepairService
            new_service = RepairService(new_db)

            original_plan = export_service.export_plan()
            new_export.import_plan(original_plan, actor="importer")

            imported_tasks = new_service.task_repo.list()
            imported_task = next(t for t in imported_tasks if t.name == draft_task.name)
            imported_items = new_service.get_checklist(imported_task.id, "admin_user")
            new_service.checklist_repo.update_item(
                item_id=imported_items[0].id,
                actor="local_user",
                completed=False,
            )

            with open(export_path, "r", encoding="utf-8") as f:
                plan_for_reimport = json.load(f)

            plan_for_reimport["windows"][0]["name"] = "conflict-test-window"
            from datetime import datetime, timedelta
            far_future = (datetime.utcnow() + timedelta(days=365)).isoformat()
            far_future_end = (datetime.utcnow() + timedelta(days=365, hours=2)).isoformat()
            plan_for_reimport["windows"][0]["start_time"] = far_future
            plan_for_reimport["windows"][0]["end_time"] = far_future_end
            plan_for_reimport["tasks"][0]["window_name"] = "conflict-test-window"

            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(plan_for_reimport, f, ensure_ascii=False)

            runner = CliRunner()

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--as-user", "importer",
            ])
            assert result.exit_code != 0, result.output
            error = json.loads(result.output)
            assert error["code"] == "checklist_conflict"

            result = runner.invoke(cli, [
                "--db", new_db_path,
                "--format", "json",
                "plan", "import", export_path,
                "--ignore-checklist-conflict",
                "--as-user", "importer",
            ])
            assert result.exit_code == 0, result.output

            refreshed = new_service.get_checklist(imported_task.id, "admin_user")
            assert refreshed[0].completed is True
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


def test_checklist_persistence_imported(export_service, service, future_window, draft_task):
    """Test that imported checklist persists across database reconnections."""
    from repair_cli.persistence import Database
    from repair_cli.service import RepairService

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
        notes="Verified OK",
    )

    plan = export_service.export_plan()

    fd, new_db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        new_db = Database(f"sqlite:///{new_db_path}")
        new_db.init_db()
        new_db.init_default_roles()
        new_export = ExportImportService(new_db)
        new_export.import_plan(plan, actor="importer")

        reconnected_db = Database(f"sqlite:///{new_db_path}")
        reconnected_db.init_db()
        new_service = RepairService(reconnected_db)

        imported_tasks = new_service.task_repo.list()
        imported_task = next(t for t in imported_tasks if t.name == draft_task.name)

        checklist = new_service.get_checklist(imported_task.id, "operator_user")
        assert len(checklist) == 1
        assert checklist[0].name == "Verify backup"
        assert checklist[0].completed is True
        assert checklist[0].notes == "Verified OK"
        assert checklist[0].updated_by == "operator_user"
    finally:
        try:
            os.unlink(new_db_path)
        except OSError:
            pass


def test_import_invalid_checklist_item(export_service, service, future_window, draft_task):
    """Test that invalid checklist items are rejected during import validation."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Valid item", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    plan = export_service.export_plan()

    for c in plan["checklist_items"]:
        c["task_id"] = 99999

    errors = export_service.validate_import_plan(plan)
    checklist_errors = [e for e in errors if e.code == "checklist_task_not_found"]
    assert len(checklist_errors) >= 1


def test_export_checklist_includes_all_fields(export_service, service, future_window, draft_task):
    """Test that all checklist fields are included in export."""
    items = [
        ChecklistItem(task_id=draft_task.id, name="Full test", required=True),
    ]
    service.set_checklist(draft_task.id, items, "operator_user")

    saved = service.get_checklist(draft_task.id, "operator_user")
    service.update_checklist_item(
        task_id=draft_task.id,
        item_id=saved[0].id,
        actor="operator_user",
        completed=True,
        notes="All fields test",
    )

    plan = export_service.export_plan()
    checklist = plan["checklist_items"][0]

    assert "id" in checklist
    assert "task_id" in checklist
    assert "name" in checklist
    assert "required" in checklist
    assert "completed" in checklist
    assert "notes" in checklist
    assert "updated_by" in checklist
    assert "created_at" in checklist
    assert "updated_at" in checklist

    assert checklist["name"] == "Full test"
    assert checklist["required"] is True
    assert checklist["completed"] is True
    assert checklist["notes"] == "All fields test"
    assert checklist["updated_by"] == "operator_user"

