"""Output formatters for table and JSON output."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tabulate import tabulate

from .models import (
    ApprovalPolicy,
    AuditLog,
    ChecklistItem,
    MaintenanceWindow,
    RepairTask,
    RoleRule,
)


def _format_datetime(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_tasks_table(tasks: List[RepairTask]) -> str:
    rows = []
    for t in tasks:
        rows.append([
            t.id,
            t.name,
            t.status.value,
            t.window_id,
            t.created_by,
            t.approved_by or "",
            _format_datetime(t.created_at),
            _format_datetime(t.executed_at),
        ])
    headers = ["ID", "Name", "Status", "Window ID", "Created By", "Approved By", "Created At", "Executed At"]
    return tabulate(rows, headers=headers, tablefmt="grid")


def format_task_detail(task: RepairTask) -> str:
    rows = [
        ["ID", task.id],
        ["Name", task.name],
        ["Status", task.status.value],
        ["Description", task.description],
        ["Window ID", task.window_id],
        ["Created By", task.created_by],
        ["Created At", _format_datetime(task.created_at)],
        ["SQL", task.sql],
        ["Rollback SQL", task.rollback_sql or "(none)"],
        ["Approved By", task.approved_by or "(pending)"],
        ["Approved At", _format_datetime(task.approved_at)],
        ["Executed By", task.executed_by or ""],
        ["Executed At", _format_datetime(task.executed_at)],
        ["Rollback By", task.rollback_by or ""],
        ["Rollback At", _format_datetime(task.rollback_at)],
        ["Updated At", _format_datetime(task.updated_at)],
    ]
    return tabulate(rows, tablefmt="grid")


def format_windows_table(windows: List[MaintenanceWindow]) -> str:
    rows = []
    for w in windows:
        rows.append([
            w.id,
            w.name,
            w.description or "",
            _format_datetime(w.start_time),
            _format_datetime(w.end_time),
            w.created_by,
        ])
    headers = ["ID", "Name", "Description", "Start Time", "End Time", "Created By"]
    return tabulate(rows, headers=headers, tablefmt="grid")


def format_window_detail(window: MaintenanceWindow) -> str:
    rows = [
        ["ID", window.id],
        ["Name", window.name],
        ["Description", window.description or ""],
        ["Start Time", _format_datetime(window.start_time)],
        ["End Time", _format_datetime(window.end_time)],
        ["Created By", window.created_by],
        ["Created At", _format_datetime(window.created_at)],
        ["Updated At", _format_datetime(window.updated_at)],
    ]
    return tabulate(rows, tablefmt="grid")


def format_audit_table(audits: List[AuditLog]) -> str:
    rows = []
    for a in audits:
        rows.append([
            a.id,
            a.task_id or "",
            a.action,
            a.actor,
            a.old_status or "",
            a.new_status or "",
            _format_datetime(a.created_at),
            (a.details or "")[:80],
        ])
    headers = ["ID", "Task ID", "Action", "Actor", "Old Status", "New Status", "Created At", "Details"]
    return tabulate(rows, headers=headers, tablefmt="grid")


def format_roles_table(roles: List[RoleRule]) -> str:
    rows = []
    for r in roles:
        rows.append([
            r.id,
            r.username,
            r.role.value,
            _format_datetime(r.created_at),
        ])
    headers = ["ID", "Username", "Role", "Created At"]
    return tabulate(rows, headers=headers, tablefmt="grid")


def format_policy_table(policy: ApprovalPolicy) -> str:
    rows = [
        ["Allow Admin Self-Approval", "Yes" if policy.allow_admin_self_approval else "No"],
        ["Require Different Approver", "Yes" if policy.require_different_approver else "No"],
        ["Updated By", policy.updated_by or "(default)"],
        ["Updated At", _format_datetime(policy.updated_at)],
    ]
    return tabulate(rows, headers=["Policy Setting", "Value"], tablefmt="grid")


def format_checklist_table(items: List[ChecklistItem]) -> str:
    rows = []
    for item in items:
        rows.append([
            item.id,
            "✓" if item.completed else " ",
            "*" if item.required else " ",
            item.name,
            item.updated_by or "",
            _format_datetime(item.updated_at),
            (item.notes or "")[:60],
        ])
    headers = ["ID", "Done", "Req", "Name", "Updated By", "Updated At", "Notes"]
    return tabulate(rows, headers=headers, tablefmt="grid")


def _task_to_dict(task: RepairTask) -> Dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "created_by": task.created_by,
        "sql": task.sql,
        "rollback_sql": task.rollback_sql,
        "window_id": task.window_id,
        "status": task.status.value,
        "approved_by": task.approved_by,
        "approved_at": _format_datetime(task.approved_at) if task.approved_at else None,
        "executed_at": _format_datetime(task.executed_at) if task.executed_at else None,
        "executed_by": task.executed_by,
        "rollback_at": _format_datetime(task.rollback_at) if task.rollback_at else None,
        "rollback_by": task.rollback_by,
        "created_at": _format_datetime(task.created_at),
        "updated_at": _format_datetime(task.updated_at),
    }


def _window_to_dict(window: MaintenanceWindow) -> Dict[str, Any]:
    return {
        "id": window.id,
        "name": window.name,
        "description": window.description,
        "start_time": _format_datetime(window.start_time),
        "end_time": _format_datetime(window.end_time),
        "created_by": window.created_by,
        "created_at": _format_datetime(window.created_at),
        "updated_at": _format_datetime(window.updated_at),
    }


def _audit_to_dict(audit: AuditLog) -> Dict[str, Any]:
    return {
        "id": audit.id,
        "task_id": audit.task_id,
        "action": audit.action,
        "actor": audit.actor,
        "old_status": audit.old_status,
        "new_status": audit.new_status,
        "details": audit.details,
        "created_at": _format_datetime(audit.created_at),
    }


def _role_to_dict(role: RoleRule) -> Dict[str, Any]:
    return {
        "id": role.id,
        "username": role.username,
        "role": role.role.value,
        "created_at": _format_datetime(role.created_at),
    }


def _policy_to_dict(policy: ApprovalPolicy) -> Dict[str, Any]:
    return {
        "id": policy.id,
        "allow_admin_self_approval": policy.allow_admin_self_approval,
        "require_different_approver": policy.require_different_approver,
        "updated_by": policy.updated_by,
        "updated_at": _format_datetime(policy.updated_at),
    }


def _checklist_to_dict(item: ChecklistItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "task_id": item.task_id,
        "name": item.name,
        "required": item.required,
        "completed": item.completed,
        "notes": item.notes,
        "updated_by": item.updated_by,
        "created_at": _format_datetime(item.created_at),
        "updated_at": _format_datetime(item.updated_at),
    }


def to_json(data: Any, indent: int = 2) -> str:
    if isinstance(data, list):
        items = []
        for item in data:
            if isinstance(item, RepairTask):
                items.append(_task_to_dict(item))
            elif isinstance(item, MaintenanceWindow):
                items.append(_window_to_dict(item))
            elif isinstance(item, AuditLog):
                items.append(_audit_to_dict(item))
            elif isinstance(item, RoleRule):
                items.append(_role_to_dict(item))
            elif isinstance(item, ChecklistItem):
                items.append(_checklist_to_dict(item))
            else:
                items.append(item)
        return json.dumps(items, indent=indent, ensure_ascii=False)
    elif isinstance(data, RepairTask):
        return json.dumps(_task_to_dict(data), indent=indent, ensure_ascii=False)
    elif isinstance(data, MaintenanceWindow):
        return json.dumps(_window_to_dict(data), indent=indent, ensure_ascii=False)
    elif isinstance(data, AuditLog):
        return json.dumps(_audit_to_dict(data), indent=indent, ensure_ascii=False)
    elif isinstance(data, RoleRule):
        return json.dumps(_role_to_dict(data), indent=indent, ensure_ascii=False)
    elif isinstance(data, ApprovalPolicy):
        return json.dumps(_policy_to_dict(data), indent=indent, ensure_ascii=False)
    elif isinstance(data, ChecklistItem):
        return json.dumps(_checklist_to_dict(data), indent=indent, ensure_ascii=False)
    else:
        return json.dumps(data, indent=indent, ensure_ascii=False)


def format_output(
    data: Any,
    output_format: str = "table",
    table_formatter=None,
) -> str:
    if output_format == "json":
        return to_json(data)
    elif output_format == "table":
        if table_formatter:
            return table_formatter(data)
        return str(data)
    else:
        raise ValueError(f"Unknown output format: {output_format}")


def success_response(message: str, output_format: str = "table", **kwargs) -> str:
    if output_format == "json":
        result = {"success": True, "message": message}
        result.update(kwargs)
        return json.dumps(result, indent=2, ensure_ascii=False)
    parts = [f"✓ {message}"]
    for k, v in kwargs.items():
        parts.append(f"  {k}: {v}")
    return "\n".join(parts)


def error_response(message: str, code: Optional[str] = None, output_format: str = "table") -> str:
    if output_format == "json":
        result = {"success": False, "error": message}
        if code:
            result["code"] = code
        return json.dumps(result, indent=2, ensure_ascii=False)
    if code:
        return f"✗ [{code}] {message}"
    return f"✗ {message}"
