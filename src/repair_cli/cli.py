"""CLI entry point for repair scheduling system."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import click
from dateutil import parser as date_parser

from .export_import import ExportImportService
from .formatters import (
    error_response,
    format_audit_table,
    format_output,
    format_roles_table,
    format_task_detail,
    format_tasks_table,
    format_window_detail,
    format_windows_table,
    success_response,
    to_json,
)
from .models import (
    MaintenanceWindow,
    RepairTask,
    Role,
)
from .persistence import (
    Database,
    RoleRepository,
)
from .service import RepairService
from .validation import ValidationError


def get_db(db_path: Optional[str] = None) -> Database:
    db_url = os.environ.get("REPAIR_DB_URL")
    if db_path:
        db_url = f"sqlite:///{db_path}"
    if not db_url:
        db_url = "sqlite:///repair.db"
    db = Database(db_url)
    db.init_db()
    db.init_default_roles()
    return db


def get_service(db_path: Optional[str] = None) -> RepairService:
    db = get_db(db_path)
    return RepairService(db)


def get_export_service(db_path: Optional[str] = None) -> ExportImportService:
    db = get_db(db_path)
    return ExportImportService(db)


def handle_error(e: Exception, output_format: str) -> None:
    if isinstance(e, ValidationError):
        click.echo(error_response(e.message, e.code, output_format), err=True)
    else:
        click.echo(error_response(str(e), output_format=output_format), err=True)
    sys.exit(1)


@click.group()
@click.option("--db", "db_path", help="Path to SQLite database file")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["table", "json"]),
              help="Output format")
@click.pass_context
def cli(ctx: click.Context, db_path: Optional[str], output_format: str) -> None:
    """Data repair scheduling and approval CLI."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["output_format"] = output_format


@cli.group()
def window() -> None:
    """Manage maintenance windows."""
    pass


@window.command("create")
@click.option("--name", required=True, help="Window name")
@click.option("--description", default="", help="Window description")
@click.option("--start", "start_str", required=True,
              help="Start time (e.g., '2025-01-01 02:00:00' or '+2h')")
@click.option("--end", "end_str", required=True,
              help="End time (e.g., '2025-01-01 04:00:00' or '+4h')")
@click.option("--as-user", default="admin_user", help="Acting user")
@click.pass_context
def window_create(ctx: click.Context, name: str, description: str,
                  start_str: str, end_str: str, as_user: str) -> None:
    """Create a new maintenance window."""
    output_format = ctx.obj["output_format"]
    try:
        start = _parse_time(start_str)
        end = _parse_time(end_str)
        service = get_service(ctx.obj["db_path"])
        w = MaintenanceWindow(
            name=name,
            description=description,
            start_time=start,
            end_time=end,
            created_by=as_user,
        )
        created = service.create_window(w)
        click.echo(success_response(
            f"Window '{name}' created",
            output_format,
            id=created.id,
            start=start.isoformat(),
            end=end.isoformat(),
        ))
    except Exception as e:
        handle_error(e, output_format)


@window.command("list")
@click.pass_context
def window_list(ctx: click.Context) -> None:
    """List all maintenance windows."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        windows = service.window_repo.list()
        click.echo(format_output(
            windows,
            output_format,
            format_windows_table,
        ))
    except Exception as e:
        handle_error(e, output_format)


@window.command("show")
@click.argument("window_id", type=int)
@click.pass_context
def window_show(ctx: click.Context, window_id: int) -> None:
    """Show details of a maintenance window."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        w = service.window_repo.get(window_id)
        if not w:
            handle_error(ValidationError(f"Window {window_id} not found",
                                        code="window_not_found"),
                        output_format)
            return
        click.echo(format_output(w, output_format, format_window_detail))
    except Exception as e:
        handle_error(e, output_format)


@window.command("update")
@click.argument("window_id", type=int)
@click.option("--name", help="New window name")
@click.option("--description", help="New description")
@click.option("--start", "start_str", help="New start time")
@click.option("--end", "end_str", help="New end time")
@click.option("--as-user", default="admin_user", help="Acting user")
@click.pass_context
def window_update(ctx: click.Context, window_id: int, name: Optional[str],
                  description: Optional[str], start_str: Optional[str],
                  end_str: Optional[str], as_user: str) -> None:
    """Update a maintenance window."""
    output_format = ctx.obj["output_format"]
    try:
        kwargs = {}
        if name:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if start_str:
            kwargs["start_time"] = _parse_time(start_str)
        if end_str:
            kwargs["end_time"] = _parse_time(end_str)
        service = get_service(ctx.obj["db_path"])
        updated = service.update_window(window_id, as_user, **kwargs)
        click.echo(success_response(
            f"Window {window_id} updated",
            output_format,
            id=updated.id,
        ))
    except Exception as e:
        handle_error(e, output_format)


@cli.group()
def task() -> None:
    """Manage repair tasks."""
    pass


@task.command("create")
@click.option("--name", required=True, help="Task name")
@click.option("--description", required=True, help="Task description")
@click.option("--sql", required=True, help="SQL to execute")
@click.option("--rollback-sql", help="SQL to rollback")
@click.option("--window-id", "window_id", type=int, required=True,
              help="Maintenance window ID")
@click.option("--as-user", default="operator_user", help="Acting user")
@click.pass_context
def task_create(ctx: click.Context, name: str, description: str, sql: str,
                rollback_sql: Optional[str], window_id: int, as_user: str) -> None:
    """Create a new repair task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        t = RepairTask(
            name=name,
            description=description,
            created_by=as_user,
            sql=sql,
            rollback_sql=rollback_sql,
            window_id=window_id,
        )
        created = service.create_task(t)
        click.echo(success_response(
            f"Task '{name}' created",
            output_format,
            id=created.id,
            status=created.status.value,
            window_id=created.window_id,
        ))
    except Exception as e:
        handle_error(e, output_format)


@task.command("list")
@click.pass_context
def task_list(ctx: click.Context) -> None:
    """List all repair tasks."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        tasks = service.task_repo.list()
        click.echo(format_output(tasks, output_format, format_tasks_table))
    except Exception as e:
        handle_error(e, output_format)


@task.command("show")
@click.argument("task_id", type=int)
@click.pass_context
def task_show(ctx: click.Context, task_id: int) -> None:
    """Show details of a repair task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        t = service.task_repo.get(task_id)
        if not t:
            handle_error(ValidationError(f"Task {task_id} not found",
                                        code="task_not_found"),
                        output_format)
            return
        click.echo(format_output(t, output_format, format_task_detail))
    except Exception as e:
        handle_error(e, output_format)


@task.command("submit")
@click.argument("task_id", type=int)
@click.option("--as-user", default="operator_user", help="Acting user")
@click.pass_context
def task_submit(ctx: click.Context, task_id: int, as_user: str) -> None:
    """Submit a task for approval."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        updated = service.submit_for_approval(task_id, as_user)
        click.echo(success_response(
            f"Task {task_id} submitted for approval",
            output_format,
            id=updated.id,
            status=updated.status.value,
        ))
    except Exception as e:
        handle_error(e, output_format)


@task.command("approve")
@click.argument("task_id", type=int)
@click.option("--as-user", default="approver_user", help="Acting user")
@click.pass_context
def task_approve(ctx: click.Context, task_id: int, as_user: str) -> None:
    """Approve a task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        updated = service.approve_task(task_id, as_user)
        click.echo(success_response(
            f"Task {task_id} approved",
            output_format,
            id=updated.id,
            status=updated.status.value,
            approved_by=updated.approved_by,
        ))
    except Exception as e:
        handle_error(e, output_format)


@task.command("reject")
@click.argument("task_id", type=int)
@click.option("--reason", default="", help="Rejection reason")
@click.option("--as-user", default="approver_user", help="Acting user")
@click.pass_context
def task_reject(ctx: click.Context, task_id: int, reason: str, as_user: str) -> None:
    """Reject a task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        updated = service.reject_task(task_id, as_user, reason)
        click.echo(success_response(
            f"Task {task_id} rejected",
            output_format,
            id=updated.id,
            status=updated.status.value,
        ))
    except Exception as e:
        handle_error(e, output_format)


@task.command("run")
@click.argument("task_id", type=int)
@click.option("--as-user", default="operator_user", help="Acting user")
@click.pass_context
def task_run(ctx: click.Context, task_id: int, as_user: str) -> None:
    """Run a task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        updated = service.run_task(task_id, as_user)
        click.echo(success_response(
            f"Task {task_id} execution completed: {updated.status.value}",
            output_format,
            id=updated.id,
            status=updated.status.value,
            executed_at=updated.executed_at.isoformat() if updated.executed_at else None,
        ))
    except Exception as e:
        handle_error(e, output_format)


@task.command("rollback")
@click.argument("task_id", type=int)
@click.option("--as-user", default="operator_user", help="Acting user")
@click.pass_context
def task_rollback(ctx: click.Context, task_id: int, as_user: str) -> None:
    """Rollback a task."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        updated = service.rollback_task(task_id, as_user)
        click.echo(success_response(
            f"Task {task_id} rollback completed: {updated.status.value}",
            output_format,
            id=updated.id,
            status=updated.status.value,
            rollback_at=updated.rollback_at.isoformat() if updated.rollback_at else None,
        ))
    except Exception as e:
        handle_error(e, output_format)


@cli.group()
def audit() -> None:
    """View audit logs."""
    pass


@audit.command("list")
@click.option("--task-id", "task_id", type=int, help="Filter by task ID")
@click.pass_context
def audit_list(ctx: click.Context, task_id: Optional[int]) -> None:
    """List audit logs."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        if task_id:
            logs = service.audit_repo.list_by_task(task_id)
        else:
            logs = service.audit_repo.list()
        click.echo(format_output(logs, output_format, format_audit_table))
    except Exception as e:
        handle_error(e, output_format)


@cli.group()
def role() -> None:
    """Manage user roles."""
    pass


@role.command("list")
@click.pass_context
def role_list(ctx: click.Context) -> None:
    """List all user roles."""
    output_format = ctx.obj["output_format"]
    try:
        db = get_db(ctx.obj["db_path"])
        repo = RoleRepository(db)
        roles = repo.list()
        click.echo(format_output(roles, output_format, format_roles_table))
    except Exception as e:
        handle_error(e, output_format)


@role.command("set")
@click.argument("username")
@click.argument("role_name", type=click.Choice(["operator", "approver", "admin"]))
@click.option("--as-user", default="admin_user", help="Acting user")
@click.pass_context
def role_set(ctx: click.Context, username: str, role_name: str, as_user: str) -> None:
    """Set a user's role."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_service(ctx.obj["db_path"])
        role = Role(role_name)
        service.set_user_role(username, role, as_user)
        click.echo(success_response(
            f"Role '{role_name}' set for user '{username}'",
            output_format,
        ))
    except Exception as e:
        handle_error(e, output_format)


@cli.group()
def plan() -> None:
    """Export and import repair plans."""
    pass


@plan.command("export")
@click.argument("filepath")
@click.option("--task-id", "task_ids", type=int, multiple=True,
              help="Task IDs to export (export all if not specified)")
@click.pass_context
def plan_export(ctx: click.Context, filepath: str, task_ids: tuple) -> None:
    """Export repair plan to a JSON file."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_export_service(ctx.obj["db_path"])
        id_list = list(task_ids) if task_ids else None
        service.export_to_file(filepath, id_list)
        click.echo(success_response(
            f"Plan exported to {filepath}",
            output_format,
            tasks_exported=len(id_list) if id_list else "all",
        ))
    except Exception as e:
        handle_error(e, output_format)


@plan.command("import")
@click.argument("filepath")
@click.option("--as-user", default="import", help="Acting user for import")
@click.option("--dry-run", is_flag=True, help="Validate only, don't import")
@click.pass_context
def plan_import(ctx: click.Context, filepath: str, as_user: str, dry_run: bool) -> None:
    """Import repair plan from a JSON file."""
    output_format = ctx.obj["output_format"]
    try:
        service = get_export_service(ctx.obj["db_path"])

        import json
        with open(filepath, "r", encoding="utf-8") as f:
            plan = json.load(f)

        errors = service.validate_import_plan(plan)
        if errors:
            error_msgs = "; ".join(e.message for e in errors)
            handle_error(ValidationError(
                f"Import validation failed with {len(errors)} error(s): {error_msgs}",
                code="import_validation_failed",
            ), output_format)
            return

        if dry_run:
            click.echo(success_response(
                "Import validation passed (dry run)",
                output_format,
                windows_to_import=len(plan.get("windows", [])),
                tasks_to_import=len(plan.get("tasks", [])),
                audits_to_import=len(plan.get("audit_logs", [])),
            ))
            return

        result = service.import_plan(plan, actor=as_user)
        click.echo(success_response(
            "Plan imported successfully",
            output_format,
            **result,
        ))
    except Exception as e:
        handle_error(e, output_format)


def _parse_time(time_str: str) -> datetime:
    time_str = time_str.strip()
    if time_str.startswith("+"):
        try:
            amount = int(time_str[1:-1])
            unit = time_str[-1]
            now = datetime.utcnow()
            if unit == "m":
                return now + timedelta(minutes=amount)
            elif unit == "h":
                return now + timedelta(hours=amount)
            elif unit == "d":
                return now + timedelta(days=amount)
        except (ValueError, IndexError):
            pass
    try:
        return date_parser.parse(time_str)
    except (ValueError, TypeError):
        raise ValidationError(
            f"Invalid time format: '{time_str}'. "
            f"Use absolute time (e.g., '2025-01-01 02:00:00') "
            f"or relative (e.g., '+2h', '+1d')",
            code="invalid_time_format",
        )


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
