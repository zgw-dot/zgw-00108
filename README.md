# Data Repair Scheduling & Approval CLI

A production-grade CLI tool for managing data repair tasks with role-based approval, maintenance windows, execution, rollback, and audit logging.

## Architecture

The project is modularized into distinct layers:

| Layer | File | Responsibility |
|-------|------|----------------|
| Models | [models.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/models.py) | Pydantic data models, enums, basic validation |
| Persistence | [persistence.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/persistence.py) | SQLite/SQLAlchemy ORM, repositories, CRUD operations |
| Validation | [validation.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/validation.py) | Business rule validation, state transition checks |
| Service | [service.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/service.py) | Core business logic, orchestration, audit logging |
| Export/Import | [export_import.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/export_import.py) | Plan export, validation, and import with rollback |
| Formatters | [formatters.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/formatters.py) | Table and JSON output formatting |
| CLI Entry | [cli.py](file:///d:/workSpace/AI__SPACE/02-label/zgw-00108/src/repair_cli/cli.py) | Click-based command-line interface |

## Database Schema

All data is stored in SQLite with the following tables:
- `maintenance_windows` - Time windows for safe execution
- `repair_tasks` - Repair tasks with full lifecycle tracking
- `audit_logs` - Immutable audit trail of all actions
- `role_rules` - User-to-role mappings

## Default Roles

Three default users are created on first run:
- `admin_user` - Full admin access
- `approver_user` - Can approve/reject tasks
- `operator_user` - Can create, submit, run, and rollback tasks

## Installation

```bash
pip install -e .
```

Or with development dependencies:

```bash
pip install -e ".[dev]"
```

## Quick Start: Full Workflow

```bash
# 1. Create a maintenance window (admin)
repair window create \
  --name "batch-window-001" \
  --description "Nightly batch maintenance window" \
  --start "2025-01-15 02:00:00" \
  --end "2025-01-15 04:00:00" \
  --as-user admin_user

# 2. List windows to get the ID
repair window list

# 3. Create a repair task (operator)
repair task create \
  --name "fix-user-email-123" \
  --description "Fix corrupted email for user #123" \
  --sql "UPDATE users SET email = 'correct@example.com' WHERE id = 123" \
  --rollback-sql "UPDATE users SET email = 'old@example.com' WHERE id = 123" \
  --window-id 1 \
  --as-user operator_user

# 4. Submit for approval (operator)
repair task submit 1 --as-user operator_user

# 5. Approve (different approver, NOT the creator)
repair task approve 1 --as-user approver_user

# 6. Run (operator, must be within window)
repair task run 1 --as-user operator_user

# 7. Rollback if needed (operator)
repair task rollback 1 --as-user operator_user

# 8. View audit trail
repair audit list --task-id 1
```

## Common Commands

### Window Management

```bash
# Create with relative times
repair window create --name "emergency" --start "+1h" --end "+3h"

# List all windows
repair window list --format json

# Show window details
repair window show 1

# Update window (only if no active tasks)
repair window update 1 --description "Updated description"
```

### Task Management

```bash
# List all tasks
repair task list

# Show task details
repair task show 1

# Reject a task
repair task reject 1 --reason "Insufficient testing" --as-user approver_user
```

### Role Management

```bash
# List all roles
repair role list

# Set user role (admin only)
repair role set alice approver --as-user admin_user
```

### Export & Import

```bash
# Export all tasks to a plan file
repair plan export plan.json

# Export specific tasks
repair plan export plan.json --task-id 1 --task-id 2

# Validate import (dry run)
repair plan import plan.json --dry-run

# Import into a new database
repair --db new.db plan import plan.json --as-user alice
```

### Output Formats

All commands support `--format table` (default) and `--format json`:

```bash
repair --format json task list
repair --format json audit list
```

## Business Rules & Constraints

### State Transitions
```
DRAFT → PENDING_APPROVAL → APPROVED → RUNNING → SUCCEEDED → ROLLBACK_RUNNING → ROLLBACK_SUCCEEDED
               ↓              ↓           ↓
           REJECTED        FAILED    ROLLBACK_FAILED
```

### Validation Rules
- **Window Overlap**: New windows cannot overlap with existing windows
- **Self-Approval**: Regular users cannot approve or reject their own tasks. **Admin users are exempt** and can approve/reject their own tasks to complete the full lifecycle.
- **State Enforcement**: Actions only allowed from valid states
- **Window Expiry**: Cannot submit/approve tasks for windows that have ended
- **Rollback Timing**: Can only rollback after execution (SUCCEEDED or FAILED). Failed rollback attempts are logged to audit.
- **Window Lock**: Cannot modify window times if tasks are in active states
- **Import References**: Tasks must reference existing windows during import
- **Audit Logging**: All actions (including failed attempts) are logged to the audit table with actor, action, status transitions, and reason.

## Testing

Run the full test suite:

```bash
pytest -v
```

With coverage:

```bash
pytest --cov=src/repair_cli --cov-report=term-missing
```

### Test Coverage Areas
- ✅ Full workflow: create → submit → approve → run → rollback
- ✅ Window overlap detection and rejection
- ✅ Self-approval prevention
- ✅ Premature rollback rejection
- ✅ Import referencing non-existent window
- ✅ Database persistence across reconnections
- ✅ Import failure rollback (no partial data)
- ✅ Audit records for all actions
- ✅ Conflict detection after config changes
- ✅ JSON output stability
- ✅ CLI command integration

## Error Handling

All errors produce consistent output:

**Table format:**
```
✗ [self_approval_not_allowed] User 'operator_user' cannot approve their own task
```

**JSON format:**
```json
{
  "success": false,
  "error": "User 'operator_user' cannot approve their own task",
  "code": "self_approval_not_allowed"
}
```

## Environment Variables

- `REPAIR_DB_URL` - Override the database URL (default: `sqlite:///repair.db`)

## Command Reference

| Command Group | Subcommand | Purpose |
|--------------|------------|---------|
| `window` | `create` | Create maintenance window |
| `window` | `list` | List all windows |
| `window` | `show` | Show window details |
| `window` | `update` | Update window |
| `task` | `create` | Create repair task |
| `task` | `list` | List all tasks |
| `task` | `show` | Show task details |
| `task` | `submit` | Submit for approval |
| `task` | `approve` | Approve task |
| `task` | `reject` | Reject task |
| `task` | `run` | Execute task |
| `task` | `rollback` | Rollback task |
| `audit` | `list` | View audit logs |
| `role` | `list` | List user roles |
| `role` | `set` | Set user role |
| `plan` | `export` | Export plan to JSON |
| `plan` | `import` | Import plan from JSON |

## Bug Fix Verification Commands

### Fix 1: Admin Self-Approval Exception

Admin users can now complete the full lifecycle on their own tasks:

```bash
# Setup: Create a window (admin)
repair --db verify_admin.db window create \
  --name "admin-test-window" \
  --description "Admin self-workflow test" \
  --start "-1h" --end "+3h" \
  --as-user admin_user

# Admin creates their own task
repair --db verify_admin.db task create \
  --name "admin-self-task" \
  --description "Test admin self-approval" \
  --sql "UPDATE users SET email = LOWER(email)" \
  --rollback-sql "UPDATE users SET email = UPPER(email)" \
  --window-id 1 \
  --as-user admin_user

# Admin submits their own task
repair --db verify_admin.db task submit 1 --as-user admin_user

# Admin approves their own task (was blocked before, now works)
repair --db verify_admin.db task approve 1 --as-user admin_user

# Admin executes their own task (was blocked before, now works)
repair --db verify_admin.db task run 1 --as-user admin_user

# Verify status
repair --db verify_admin.db task show 1 --format json

# Cleanup
rm verify_admin.db
```

**Expected JSON output for status check:**
```json
{
  "id": 1,
  "name": "admin-self-task",
  "status": "succeeded",
  "created_by": "admin_user",
  "approved_by": "admin_user",
  "executed_by": "admin_user"
}
```

---

### Fix 2: Failed Rollback Audit Logging

Failed rollback attempts (before task execution) now write an audit record:

```bash
# Setup: Create a window and task
repair --db verify_rollback.db window create \
  --name "rollback-test-window" \
  --description "Rollback audit test" \
  --start "-1h" --end "+3h" \
  --as-user admin_user

repair --db verify_rollback.db task create \
  --name "rollback-test-task" \
  --description "Test failed rollback audit" \
  --sql "UPDATE ..." \
  --rollback-sql "UPDATE ..." \
  --window-id 1 \
  --as-user operator_user

# Get initial audit count
repair --db verify_rollback.db audit list --task-id 1 --format json | python -c "import sys,json; print('Initial audit count:', len(json.load(sys.stdin)))"

# Try to rollback before execution (should fail)
repair --db verify_rollback.db task rollback 1 --as-user operator_user --format json

# Verify task status is unchanged
repair --db verify_rollback.db task show 1 --format json | python -c "import sys,json; print('Status:', json.load(sys.stdin)['status'])"

# Verify audit log has the failure record
repair --db verify_rollback.db audit list --task-id 1 --format json

# Cleanup
rm verify_rollback.db
```

**Expected audit log entry for failed rollback:**
```json
{
  "id": 2,
  "task_id": 1,
  "action": "task_rollback_rejected",
  "actor": "operator_user",
  "old_status": "draft",
  "new_status": "draft",
  "details": "Task 1 is in state draft, must be 'succeeded' or 'failed' to rollback",
  "created_at": "2025-01-15 02:30:00 UTC"
}
```

---

### Regression: Regular User Self-Approval Still Blocked

Verify regular users still cannot approve their own tasks:

```bash
# Setup
repair --db verify_regular.db window create \
  --name "regular-test-window" \
  --start "-1h" --end "+3h" \
  --as-user admin_user

repair --db verify_regular.db task create \
  --name "regular-self-task" \
  --description "Test regular user self-approval blocked" \
  --sql "UPDATE ..." \
  --rollback-sql "UPDATE ..." \
  --window-id 1 \
  --as-user operator_user

repair --db verify_regular.db task submit 1 --as-user operator_user

# Regular user tries to approve their own task (should fail)
repair --db verify_regular.db task approve 1 --as-user operator_user --format json

# Cleanup
rm verify_regular.db
```

**Expected error JSON:**
```json
{
  "success": false,
  "error": "User 'operator_user' cannot approve their own task (created_by=operator_user)",
  "code": "self_approval_not_allowed"
}
```
