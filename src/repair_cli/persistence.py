"""SQLite persistence layer using SQLAlchemy."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, List, Optional, Type, TypeVar

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from .models import (
    ApprovalPolicy as ApprovalPolicyModel,
    AuditLog as AuditLogModel,
    ChecklistItem as ChecklistItemModel,
    MaintenanceWindow as MaintenanceWindowModel,
    RepairTask as RepairTaskModel,
    Role,
    RoleRule as RoleRuleModel,
    TaskStatus,
)

Base = declarative_base()
T = TypeVar("T")


class RepairTask(Base):
    __tablename__ = "repair_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False)
    created_by = Column(String(255), nullable=False, index=True)
    sql = Column(Text, nullable=False)
    rollback_sql = Column(Text)
    window_id = Column(Integer, ForeignKey("maintenance_windows.id"), nullable=False)
    status = Column(String(50), nullable=False, default=TaskStatus.DRAFT.value, index=True)
    approved_by = Column(String(255))
    approved_at = Column(DateTime)
    executed_at = Column(DateTime)
    executed_by = Column(String(255))
    rollback_at = Column(DateTime)
    rollback_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    window = relationship("MaintenanceWindow", back_populates="tasks")
    audit_logs = relationship("AuditLog", back_populates="task")
    checklist_items = relationship("ChecklistItem", back_populates="task", cascade="all, delete-orphan")


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, default="")
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False, index=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = relationship("RepairTask", back_populates="window")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("repair_tasks.id"))
    action = Column(String(255), nullable=False, index=True)
    actor = Column(String(255), nullable=False, index=True)
    old_status = Column(String(50))
    new_status = Column(String(50))
    details = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    task = relationship("RepairTask", back_populates="audit_logs")


class RoleRule(Base):
    __tablename__ = "role_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False, unique=True, index=True)
    role = Column(String(50), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    allow_admin_self_approval = Column(Integer, nullable=False, default=1)
    require_different_approver = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(255))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("repair_tasks.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    required = Column(Integer, nullable=False, default=1)
    completed = Column(Integer, nullable=False, default=0)
    notes = Column(Text)
    updated_by = Column(String(255))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = relationship("RepairTask", back_populates="checklist_items")


class Database:
    def __init__(self, db_url: str = "sqlite:///repair.db"):
        self.engine = create_engine(db_url, echo=False, future=True)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine, future=True)

    def init_db(self) -> None:
        Base.metadata.create_all(bind=self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def init_default_roles(self) -> None:
        with self.session() as session:
            default_roles = [
                ("admin_user", Role.ADMIN),
                ("approver_user", Role.APPROVER),
                ("operator_user", Role.OPERATOR),
                ("operator_user_2", Role.OPERATOR),
            ]
            for username, role in default_roles:
                existing = session.query(RoleRule).filter(RoleRule.username == username).first()
                if not existing:
                    rr = RoleRule(username=username, role=role.value)
                    session.add(rr)
            session.commit()

    def init_default_policy(self) -> None:
        with self.session() as session:
            existing = session.query(ApprovalPolicy).first()
            if not existing:
                policy = ApprovalPolicy(
                    allow_admin_self_approval=1,
                    require_different_approver=1,
                    updated_by=None,
                )
                session.add(policy)
            session.commit()


def _to_task_model(db_task: RepairTask) -> RepairTaskModel:
    return RepairTaskModel(
        id=db_task.id,
        name=db_task.name,
        description=db_task.description,
        created_by=db_task.created_by,
        sql=db_task.sql,
        rollback_sql=db_task.rollback_sql,
        window_id=db_task.window_id,
        status=TaskStatus(db_task.status),
        approved_by=db_task.approved_by,
        approved_at=db_task.approved_at,
        executed_at=db_task.executed_at,
        executed_by=db_task.executed_by,
        rollback_at=db_task.rollback_at,
        rollback_by=db_task.rollback_by,
        created_at=db_task.created_at,
        updated_at=db_task.updated_at,
    )


def _to_window_model(db_window: MaintenanceWindow) -> MaintenanceWindowModel:
    return MaintenanceWindowModel(
        id=db_window.id,
        name=db_window.name,
        description=db_window.description,
        start_time=db_window.start_time,
        end_time=db_window.end_time,
        created_by=db_window.created_by,
        created_at=db_window.created_at,
        updated_at=db_window.updated_at,
    )


def _to_audit_model(db_audit: AuditLog) -> AuditLogModel:
    return AuditLogModel(
        id=db_audit.id,
        task_id=db_audit.task_id,
        action=db_audit.action,
        actor=db_audit.actor,
        old_status=db_audit.old_status,
        new_status=db_audit.new_status,
        details=db_audit.details,
        created_at=db_audit.created_at,
    )


def _to_role_model(db_role: RoleRule) -> RoleRuleModel:
    return RoleRuleModel(
        id=db_role.id,
        username=db_role.username,
        role=Role(db_role.role),
        created_at=db_role.created_at,
    )


def _to_policy_model(db_policy: ApprovalPolicy) -> ApprovalPolicyModel:
    return ApprovalPolicyModel(
        id=db_policy.id,
        allow_admin_self_approval=bool(db_policy.allow_admin_self_approval),
        require_different_approver=bool(db_policy.require_different_approver),
        updated_by=db_policy.updated_by,
        updated_at=db_policy.updated_at,
    )


def _to_checklist_model(db_item: ChecklistItem) -> ChecklistItemModel:
    return ChecklistItemModel(
        id=db_item.id,
        task_id=db_item.task_id,
        name=db_item.name,
        required=bool(db_item.required),
        completed=bool(db_item.completed),
        notes=db_item.notes,
        updated_by=db_item.updated_by,
        created_at=db_item.created_at,
        updated_at=db_item.updated_at,
    )


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, task: RepairTaskModel) -> RepairTaskModel:
        with self.db.session() as session:
            db_task = RepairTask(
                name=task.name,
                description=task.description,
                created_by=task.created_by,
                sql=task.sql,
                rollback_sql=task.rollback_sql,
                window_id=task.window_id,
                status=task.status.value,
            )
            session.add(db_task)
            session.commit()
            session.refresh(db_task)
            return _to_task_model(db_task)

    def get(self, task_id: int) -> Optional[RepairTaskModel]:
        with self.db.session() as session:
            db_task = session.query(RepairTask).filter(RepairTask.id == task_id).first()
            return _to_task_model(db_task) if db_task else None

    def get_for_update(self, session: Session, task_id: int) -> Optional[RepairTask]:
        return session.query(RepairTask).filter(RepairTask.id == task_id).with_for_update().first()

    def list(self) -> List[RepairTaskModel]:
        with self.db.session() as session:
            db_tasks = session.query(RepairTask).order_by(RepairTask.id.desc()).all()
            return [_to_task_model(t) for t in db_tasks]

    def update_status(
        self,
        task_id: int,
        old_status: TaskStatus,
        new_status: TaskStatus,
        **kwargs,
    ) -> Optional[RepairTaskModel]:
        with self.db.session() as session:
            db_task = self.get_for_update(session, task_id)
            if not db_task or db_task.status != old_status.value:
                return None
            db_task.status = new_status.value
            for key, value in kwargs.items():
                setattr(db_task, key, value)
            db_task.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(db_task)
            return _to_task_model(db_task)

    def update_fields(self, task_id: int, **kwargs) -> Optional[RepairTaskModel]:
        with self.db.session() as session:
            db_task = self.get_for_update(session, task_id)
            if not db_task:
                return None
            for key, value in kwargs.items():
                setattr(db_task, key, value)
            db_task.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(db_task)
            return _to_task_model(db_task)

    def list_by_window(self, window_id: int) -> List[RepairTaskModel]:
        with self.db.session() as session:
            db_tasks = (
                session.query(RepairTask)
                .filter(RepairTask.window_id == window_id)
                .order_by(RepairTask.id.desc())
                .all()
            )
            return [_to_task_model(t) for t in db_tasks]


class WindowRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, window: MaintenanceWindowModel) -> MaintenanceWindowModel:
        with self.db.session() as session:
            db_window = MaintenanceWindow(
                name=window.name,
                description=window.description,
                start_time=window.start_time,
                end_time=window.end_time,
                created_by=window.created_by,
            )
            session.add(db_window)
            session.commit()
            session.refresh(db_window)
            return _to_window_model(db_window)

    def get(self, window_id: int) -> Optional[MaintenanceWindowModel]:
        with self.db.session() as session:
            db_window = (
                session.query(MaintenanceWindow).filter(MaintenanceWindow.id == window_id).first()
            )
            return _to_window_model(db_window) if db_window else None

    def get_by_name(self, name: str) -> Optional[MaintenanceWindowModel]:
        with self.db.session() as session:
            db_window = (
                session.query(MaintenanceWindow).filter(MaintenanceWindow.name == name).first()
            )
            return _to_window_model(db_window) if db_window else None

    def list(self) -> List[MaintenanceWindowModel]:
        with self.db.session() as session:
            db_windows = (
                session.query(MaintenanceWindow).order_by(MaintenanceWindow.start_time.desc()).all()
            )
            return [_to_window_model(w) for w in db_windows]

    def get_all_overlapping(
        self, start_time: datetime, end_time: datetime, exclude_id: Optional[int] = None
    ) -> List[MaintenanceWindowModel]:
        with self.db.session() as session:
            query = session.query(MaintenanceWindow).filter(
                MaintenanceWindow.start_time < end_time,
                MaintenanceWindow.end_time > start_time,
            )
            if exclude_id is not None:
                query = query.filter(MaintenanceWindow.id != exclude_id)
            db_windows = query.all()
            return [_to_window_model(w) for w in db_windows]

    def update(self, window_id: int, **kwargs) -> Optional[MaintenanceWindowModel]:
        with self.db.session() as session:
            db_window = (
                session.query(MaintenanceWindow)
                .filter(MaintenanceWindow.id == window_id)
                .with_for_update()
                .first()
            )
            if not db_window:
                return None
            for key, value in kwargs.items():
                setattr(db_window, key, value)
            db_window.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(db_window)
            return _to_window_model(db_window)


class AuditRepository:
    def __init__(self, db: Database):
        self.db = db

    def log(self, audit: AuditLogModel) -> AuditLogModel:
        with self.db.session() as session:
            db_audit = AuditLog(
                task_id=audit.task_id,
                action=audit.action,
                actor=audit.actor,
                old_status=audit.old_status,
                new_status=audit.new_status,
                details=audit.details,
            )
            session.add(db_audit)
            session.commit()
            session.refresh(db_audit)
            return _to_audit_model(db_audit)

    def list_by_task(self, task_id: int) -> List[AuditLogModel]:
        with self.db.session() as session:
            db_audits = (
                session.query(AuditLog)
                .filter(AuditLog.task_id == task_id)
                .order_by(AuditLog.created_at.asc())
                .all()
            )
            return [_to_audit_model(a) for a in db_audits]

    def list(self) -> List[AuditLogModel]:
        with self.db.session() as session:
            db_audits = session.query(AuditLog).order_by(AuditLog.created_at.desc()).all()
            return [_to_audit_model(a) for a in db_audits]


class RoleRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_role(self, username: str) -> Optional[Role]:
        with self.db.session() as session:
            db_role = session.query(RoleRule).filter(RoleRule.username == username).first()
            return Role(db_role.role) if db_role else None

    def set_role(self, username: str, role: Role) -> RoleRuleModel:
        with self.db.session() as session:
            db_role = session.query(RoleRule).filter(RoleRule.username == username).first()
            if db_role:
                db_role.role = role.value
            else:
                db_role = RoleRule(username=username, role=role.value)
                session.add(db_role)
            session.commit()
            session.refresh(db_role)
            return _to_role_model(db_role)

    def list(self) -> List[RoleRuleModel]:
        with self.db.session() as session:
            db_roles = session.query(RoleRule).order_by(RoleRule.username).all()
            return [_to_role_model(r) for r in db_roles]

    def has_role(self, username: str, required_role: Role) -> bool:
        user_role = self.get_role(username)
        if user_role == Role.ADMIN:
            return True
        if required_role == Role.OPERATOR:
            return user_role in (Role.OPERATOR, Role.APPROVER, Role.ADMIN)
        if required_role == Role.APPROVER:
            return user_role in (Role.APPROVER, Role.ADMIN)
        return user_role == required_role


class PolicyRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> ApprovalPolicyModel:
        with self.db.session() as session:
            db_policy = session.query(ApprovalPolicy).order_by(ApprovalPolicy.id.desc()).first()
            if not db_policy:
                default = ApprovalPolicy(
                    allow_admin_self_approval=1,
                    require_different_approver=1,
                    updated_by="system",
                )
                session.add(default)
                session.commit()
                session.refresh(default)
                db_policy = default
            return _to_policy_model(db_policy)

    def update(
        self,
        allow_admin_self_approval: Optional[bool] = None,
        require_different_approver: Optional[bool] = None,
        updated_by: Optional[str] = None,
    ) -> ApprovalPolicyModel:
        with self.db.session() as session:
            db_policy = session.query(ApprovalPolicy).order_by(ApprovalPolicy.id.desc()).with_for_update().first()
            if not db_policy:
                db_policy = ApprovalPolicy(
                    allow_admin_self_approval=1,
                    require_different_approver=1,
                    updated_by=updated_by,
                )
                session.add(db_policy)
            else:
                if allow_admin_self_approval is not None:
                    db_policy.allow_admin_self_approval = 1 if allow_admin_self_approval else 0
                if require_different_approver is not None:
                    db_policy.require_different_approver = 1 if require_different_approver else 0
                if updated_by is not None:
                    db_policy.updated_by = updated_by
                db_policy.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(db_policy)
            return _to_policy_model(db_policy)


class ChecklistRepository:
    def __init__(self, db: Database):
        self.db = db

    def set_items(self, task_id: int, items: List[ChecklistItemModel]) -> Tuple[int, int]:
        created = 0
        updated = 0
        with self.db.session() as session:
            existing = session.query(ChecklistItem).filter(
                ChecklistItem.task_id == task_id
            ).all()
            existing_by_name = {item.name: item for item in existing}

            for item_data in items:
                if item_data.name in existing_by_name:
                    db_item = existing_by_name[item_data.name]
                    changed = False
                    if db_item.required != (1 if item_data.required else 0):
                        db_item.required = 1 if item_data.required else 0
                        changed = True
                    if db_item.completed != (1 if item_data.completed else 0):
                        db_item.completed = 1 if item_data.completed else 0
                        changed = True
                    if db_item.notes != item_data.notes:
                        db_item.notes = item_data.notes
                        changed = True
                    if db_item.updated_by != item_data.updated_by:
                        db_item.updated_by = item_data.updated_by
                        changed = True
                    if changed:
                        db_item.updated_at = datetime.utcnow()
                        updated += 1
                    del existing_by_name[item_data.name]
                else:
                    db_item = ChecklistItem(
                        task_id=task_id,
                        name=item_data.name,
                        required=1 if item_data.required else 0,
                        completed=1 if item_data.completed else 0,
                        notes=item_data.notes,
                        updated_by=item_data.updated_by,
                    )
                    session.add(db_item)
                    created += 1

            for to_remove in existing_by_name.values():
                session.delete(to_remove)

            session.commit()
        return created, updated

    def get_by_task(self, task_id: int) -> List[ChecklistItemModel]:
        with self.db.session() as session:
            db_items = (
                session.query(ChecklistItem)
                .filter(ChecklistItem.task_id == task_id)
                .order_by(ChecklistItem.id.asc())
                .all()
            )
            return [_to_checklist_model(item) for item in db_items]

    def get(self, item_id: int) -> Optional[ChecklistItemModel]:
        with self.db.session() as session:
            db_item = session.query(ChecklistItem).filter(ChecklistItem.id == item_id).first()
            return _to_checklist_model(db_item) if db_item else None

    def get_for_update(self, session: Session, item_id: int) -> Optional[ChecklistItem]:
        return session.query(ChecklistItem).filter(ChecklistItem.id == item_id).with_for_update().first()

    def update_item(
        self,
        item_id: int,
        actor: str,
        completed: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> Optional[ChecklistItemModel]:
        with self.db.session() as session:
            db_item = self.get_for_update(session, item_id)
            if not db_item:
                return None
            old_completed = bool(db_item.completed)
            if completed is not None:
                db_item.completed = 1 if completed else 0
            if notes is not None:
                db_item.notes = notes
            db_item.updated_by = actor
            db_item.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(db_item)
            result = _to_checklist_model(db_item)
            result._old_completed = old_completed
            return result

    def get_incomplete_required(self, task_id: int) -> List[ChecklistItemModel]:
        with self.db.session() as session:
            db_items = (
                session.query(ChecklistItem)
                .filter(
                    ChecklistItem.task_id == task_id,
                    ChecklistItem.required == 1,
                    ChecklistItem.completed == 0,
                )
                .order_by(ChecklistItem.id.asc())
                .all()
            )
            return [_to_checklist_model(item) for item in db_items]

    def delete_by_task(self, task_id: int) -> int:
        with self.db.session() as session:
            count = session.query(ChecklistItem).filter(
                ChecklistItem.task_id == task_id
            ).delete()
            session.commit()
            return count
