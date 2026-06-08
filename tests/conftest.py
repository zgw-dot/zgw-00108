"""Pytest configuration and fixtures."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from typing import Generator, Tuple

import pytest

from repair_cli.export_import import ExportImportService
from repair_cli.models import (
    MaintenanceWindow,
    RepairTask,
    Role,
    TaskStatus,
)
from repair_cli.persistence import (
    Database,
    RoleRepository,
)
from repair_cli.service import RepairService


@pytest.fixture
def temp_db() -> Generator[Tuple[Database, str], None, None]:
    """Create a temporary SQLite database."""
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"
    db = Database(db_url)
    db.init_db()
    db.init_default_roles()
    try:
        yield db, db_path
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.fixture
def db(temp_db: Tuple[Database, str]) -> Database:
    return temp_db[0]


@pytest.fixture
def db_path(temp_db: Tuple[Database, str]) -> str:
    return temp_db[1]


@pytest.fixture
def role_repo(db: Database) -> RoleRepository:
    return RoleRepository(db)


@pytest.fixture
def service(db: Database) -> RepairService:
    executor_calls = []

    def mock_executor(sql: str) -> Tuple[bool, str]:
        executor_calls.append(sql)
        if sql.startswith("FAIL"):
            return False, "Simulated failure"
        return True, f"Executed: {sql[:50]}"

    svc = RepairService(db, sql_executor=mock_executor)
    svc._executor_calls = executor_calls
    return svc


@pytest.fixture
def export_service(db: Database) -> ExportImportService:
    return ExportImportService(db)


@pytest.fixture
def role_repo(db: Database) -> "RoleRepository":
    from repair_cli.persistence import RoleRepository
    return RoleRepository(db)


@pytest.fixture
def future_window(service: RepairService) -> MaintenanceWindow:
    """Create a maintenance window in the future."""
    start = datetime.utcnow() + timedelta(hours=24)
    end = start + timedelta(hours=2)
    return service.create_window(MaintenanceWindow(
        name="test-window",
        description="Test maintenance window",
        start_time=start,
        end_time=end,
        created_by="admin_user",
    ))


@pytest.fixture
def active_window(service: RepairService) -> MaintenanceWindow:
    """Create a currently active maintenance window."""
    start = datetime.utcnow() - timedelta(hours=1)
    end = datetime.utcnow() + timedelta(hours=3)
    return service.create_window(MaintenanceWindow(
        name="active-window",
        description="Active maintenance window",
        start_time=start,
        end_time=end,
        created_by="admin_user",
    ))


@pytest.fixture
def draft_task(service: RepairService, future_window: MaintenanceWindow) -> RepairTask:
    """Create a draft task."""
    return service.create_task(RepairTask(
        name="test-task",
        description="Test repair task",
        created_by="operator_user",
        sql="UPDATE users SET email = 'new@example.com' WHERE id = 1",
        rollback_sql="UPDATE users SET email = 'old@example.com' WHERE id = 1",
        window_id=future_window.id,
    ))


@pytest.fixture
def pending_task(service: RepairService, draft_task: RepairTask) -> RepairTask:
    """Create a task pending approval."""
    return service.submit_for_approval(draft_task.id, "operator_user")


@pytest.fixture
def approved_task(service: RepairService, pending_task: RepairTask) -> RepairTask:
    """Create an approved task."""
    return service.approve_task(pending_task.id, "approver_user")


@pytest.fixture
def succeeded_task(service: RepairService, approved_task: RepairTask,
                   active_window: MaintenanceWindow) -> RepairTask:
    """Create a succeeded task by moving it to an active window and running."""
    service.task_repo.update_fields(
        approved_task.id,
        window_id=active_window.id,
    )
    return service.run_task(approved_task.id, "operator_user")
