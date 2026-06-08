"""Tests for export/import functionality."""

import json
import tempfile
import os
from datetime import datetime, timedelta

import pytest

from repair_cli.export_import import ExportImportService
from repair_cli.models import (
    MaintenanceWindow,
    RepairTask,
    TaskStatus,
)
from repair_cli.persistence import Database
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
