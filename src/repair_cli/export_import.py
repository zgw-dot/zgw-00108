"""Export and import functionality for repair plans."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from dateutil import parser as date_parser

from .models import (
    ApprovalPolicy,
    AuditLog,
    MaintenanceWindow,
    RepairTask,
    TaskStatus,
)
from .persistence import (
    AuditRepository,
    Database,
    PolicyRepository,
    TaskRepository,
    WindowRepository,
)
from .validation import ValidationError, Validator


class ExportImportService:
    def __init__(self, db: Database):
        self.db = db
        self.task_repo = TaskRepository(db)
        self.window_repo = WindowRepository(db)
        self.audit_repo = AuditRepository(db)
        self.policy_repo = PolicyRepository(db)

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        return date_parser.parse(value)

    def export_plan(self, task_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        if task_ids:
            tasks = [t for t in (self.task_repo.get(tid) for tid in task_ids) if t]
        else:
            tasks = self.task_repo.list()

        window_ids = {t.window_id for t in tasks}
        windows = [w for w in (self.window_repo.get(wid) for wid in window_ids) if w]

        all_task_ids = [t.id for t in tasks if t.id]
        audits: List[AuditLog] = []
        for tid in all_task_ids:
            audits.extend(self.audit_repo.list_by_task(tid))

        policy = self.policy_repo.get()
        plan = {
            "version": "1.0",
            "exported_at": datetime.utcnow().isoformat(),
            "policy": {
                "allow_admin_self_approval": policy.allow_admin_self_approval,
                "require_different_approver": policy.require_different_approver,
                "updated_by": policy.updated_by,
                "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
            },
            "windows": [],
            "tasks": [],
            "audit_logs": [],
        }

        for w in windows:
            plan["windows"].append({
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "start_time": w.start_time.isoformat(),
                "end_time": w.end_time.isoformat(),
                "created_by": w.created_by,
            })

        for t in tasks:
            plan["tasks"].append({
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "created_by": t.created_by,
                "sql": t.sql,
                "rollback_sql": t.rollback_sql,
                "window_id": t.window_id,
                "window_name": next((w.name for w in windows if w.id == t.window_id), None),
                "status": t.status.value,
                "approved_by": t.approved_by,
                "approved_at": t.approved_at.isoformat() if t.approved_at else None,
                "executed_at": t.executed_at.isoformat() if t.executed_at else None,
                "executed_by": t.executed_by,
                "rollback_at": t.rollback_at.isoformat() if t.rollback_at else None,
                "rollback_by": t.rollback_by,
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            })

        for a in audits:
            plan["audit_logs"].append({
                "id": a.id,
                "task_id": a.task_id,
                "action": a.action,
                "actor": a.actor,
                "old_status": a.old_status,
                "new_status": a.new_status,
                "details": a.details,
                "created_at": a.created_at.isoformat(),
            })

        return plan

    def export_to_file(self, filepath: str, task_ids: Optional[List[int]] = None) -> None:
        plan = self.export_plan(task_ids)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

    def validate_import_plan(self, plan: Dict[str, Any]) -> List[ValidationError]:
        errors: List[ValidationError] = []

        if plan.get("version") != "1.0":
            errors.append(ValidationError(
                f"Unsupported plan version: {plan.get('version')}",
                code="unsupported_version",
            ))
            return errors

        import_policy = plan.get("policy")
        if import_policy:
            current_policy = self.policy_repo.get()
            policy_conflicts = []

            import_allow_admin = import_policy.get("allow_admin_self_approval")
            if import_allow_admin is not None and import_allow_admin != current_policy.allow_admin_self_approval:
                policy_conflicts.append(
                    f"allow_admin_self_approval: local={current_policy.allow_admin_self_approval}, "
                    f"imported={import_allow_admin}"
                )

            import_require_diff = import_policy.get("require_different_approver")
            if import_require_diff is not None and import_require_diff != current_policy.require_different_approver:
                policy_conflicts.append(
                    f"require_different_approver: local={current_policy.require_different_approver}, "
                    f"imported={import_require_diff}"
                )

            if policy_conflicts:
                errors.append(ValidationError(
                    f"Policy conflict detected: {'; '.join(policy_conflicts)}. "
                    f"Import will overwrite local policy.",
                    code="policy_conflict",
                ))

        existing_windows = self.window_repo.list()
        existing_window_names = {w.name for w in existing_windows}

        for window_data in plan.get("windows", []):
            name = window_data.get("name")
            if name in existing_window_names:
                errors.append(ValidationError(
                    f"Window name '{name}' already exists in target database",
                    code="duplicate_window_name",
                ))

            try:
                window = MaintenanceWindow(
                    name=name,
                    description=window_data.get("description", ""),
                    start_time=self._parse_datetime(window_data.get("start_time")),
                    end_time=self._parse_datetime(window_data.get("end_time")),
                    created_by=window_data.get("created_by", "import"),
                )
                overlapping = self.window_repo.get_all_overlapping(
                    window.start_time, window.end_time
                )
                if overlapping:
                    names = [f"'{w.name}'(id={w.id})" for w in overlapping]
                    errors.append(ValidationError(
                        f"Window '{name}' overlaps with existing windows: {', '.join(names)}",
                        code="window_overlap",
                    ))
            except (ValueError, TypeError) as e:
                errors.append(ValidationError(
                    f"Invalid window '{name}': {e}",
                    code="invalid_window",
                ))

        import_windows = []
        for window_data in plan.get("windows", []):
            try:
                import_windows.append(MaintenanceWindow(
                    name=window_data.get("name"),
                    description=window_data.get("description", ""),
                    start_time=self._parse_datetime(window_data.get("start_time")),
                    end_time=self._parse_datetime(window_data.get("end_time")),
                    created_by=window_data.get("created_by", "import"),
                ))
            except (ValueError, TypeError):
                pass

        for task_data in plan.get("tasks", []):
            window_id = task_data.get("window_id")
            window_name = task_data.get("window_name")

            found = False
            if window_id is not None:
                if any(w.id == window_id for w in existing_windows):
                    found = True
                if any(w.name == next((wd.get("name") for wd in plan.get("windows", [])
                                       if wd.get("id") == window_id), None)
                       for w in import_windows):
                    found = True

            if window_name is not None:
                if window_name in existing_window_names:
                    found = True
                if any(w.name == window_name for w in import_windows):
                    found = True

            if not found:
                errors.append(ValidationError(
                    f"Task '{task_data.get('name')}' references non-existent window "
                    f"(id={window_id}, name='{window_name}')",
                    code="import_window_not_found",
                ))

            try:
                RepairTask(
                    name=task_data.get("name"),
                    description=task_data.get("description"),
                    created_by=task_data.get("created_by", "import"),
                    sql=task_data.get("sql"),
                    rollback_sql=task_data.get("rollback_sql"),
                    window_id=window_id or 0,
                    status=TaskStatus(task_data.get("status", TaskStatus.DRAFT.value)),
                )
            except (ValueError, TypeError) as e:
                errors.append(ValidationError(
                    f"Invalid task '{task_data.get('name')}': {e}",
                    code="invalid_task",
                ))

        return errors

    def import_plan(self, plan: Dict[str, Any], actor: str = "import") -> Dict[str, Any]:
        errors = self.validate_import_plan(plan)
        non_policy_errors = [e for e in errors if e.code != "policy_conflict"]
        if non_policy_errors:
            raise ValidationError(
                f"Import validation failed with {len(non_policy_errors)} error(s): "
                + "; ".join(e.message for e in non_policy_errors),
                code="import_validation_failed",
            )

        window_id_map: Dict[int, int] = {}
        task_id_map: Dict[int, int] = {}

        with self.db.session() as session:
            try:
                for window_data in plan.get("windows", []):
                    old_id = window_data.get("id")
                    name = window_data.get("name")

                    existing = self.window_repo.get_by_name(name)
                    if existing:
                        new_window_id = existing.id
                    else:
                        window = MaintenanceWindow(
                            name=name,
                            description=window_data.get("description", ""),
                            start_time=self._parse_datetime(window_data.get("start_time")),
                            end_time=self._parse_datetime(window_data.get("end_time")),
                            created_by=window_data.get("created_by", actor),
                        )
                        created = self.window_repo.create(window)
                        new_window_id = created.id

                    if old_id is not None:
                        window_id_map[old_id] = new_window_id

                for task_data in plan.get("tasks", []):
                    old_id = task_data.get("id")
                    window_id = task_data.get("window_id")
                    window_name = task_data.get("window_name")

                    resolved_window_id: Optional[int] = None
                    if window_id is not None and window_id in window_id_map:
                        resolved_window_id = window_id_map[window_id]
                    elif window_name:
                        existing = self.window_repo.get_by_name(window_name)
                        if existing:
                            resolved_window_id = existing.id

                    if resolved_window_id is None:
                        raise ValidationError(
                            f"Cannot resolve window for task '{task_data.get('name')}'",
                            code="window_resolution_failed",
                        )

                    status_str = task_data.get("status", TaskStatus.DRAFT.value)
                    try:
                        status = TaskStatus(status_str)
                    except ValueError:
                        status = TaskStatus.DRAFT

                    task = RepairTask(
                        name=task_data.get("name"),
                        description=task_data.get("description"),
                        created_by=task_data.get("created_by", actor),
                        sql=task_data.get("sql"),
                        rollback_sql=task_data.get("rollback_sql"),
                        window_id=resolved_window_id,
                        status=status,
                        approved_by=task_data.get("approved_by"),
                        approved_at=self._parse_datetime(task_data.get("approved_at")),
                        executed_at=self._parse_datetime(task_data.get("executed_at")),
                        executed_by=task_data.get("executed_by"),
                        rollback_at=self._parse_datetime(task_data.get("rollback_at")),
                        rollback_by=task_data.get("rollback_by"),
                    )

                    created = self.task_repo.create(task)
                    if task.status != TaskStatus.DRAFT:
                        self.task_repo.update_fields(
                            created.id,
                            status=task.status.value,
                            approved_by=task.approved_by,
                            approved_at=task.approved_at,
                            executed_at=task.executed_at,
                            executed_by=task.executed_by,
                            rollback_at=task.rollback_at,
                            rollback_by=task.rollback_by,
                        )

                    if old_id is not None:
                        task_id_map[old_id] = created.id

                for audit_data in plan.get("audit_logs", []):
                    old_task_id = audit_data.get("task_id")
                    new_task_id = task_id_map.get(old_task_id) if old_task_id else None

                    audit = AuditLog(
                        task_id=new_task_id,
                        action=audit_data.get("action"),
                        actor=audit_data.get("actor", actor),
                        old_status=audit_data.get("old_status"),
                        new_status=audit_data.get("new_status"),
                        details=audit_data.get("details"),
                        created_at=self._parse_datetime(audit_data.get("created_at")),
                    )
                    self.audit_repo.log(audit)

                policy_imported = False
                policy_data = plan.get("policy")
                if policy_data:
                    self.policy_repo.update(
                        allow_admin_self_approval=policy_data.get("allow_admin_self_approval"),
                        require_different_approver=policy_data.get("require_different_approver"),
                        updated_by=actor,
                    )
                    policy_imported = True

                session.commit()

            except Exception:
                session.rollback()
                raise

        return {
            "windows_imported": len(plan.get("windows", [])),
            "tasks_imported": len(plan.get("tasks", [])),
            "audits_imported": len(plan.get("audit_logs", [])),
            "policy_imported": policy_imported,
            "window_id_map": window_id_map,
            "task_id_map": task_id_map,
        }

    def import_from_file(self, filepath: str, actor: str = "import") -> Dict[str, Any]:
        with open(filepath, "r", encoding="utf-8") as f:
            plan = json.load(f)
        return self.import_plan(plan, actor=actor)
