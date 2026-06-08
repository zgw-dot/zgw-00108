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
- `approval_policies` - Configurable approval policies (persisted across restarts)

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
repair --format json window list

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

### Approval Policy Management

```bash
# View current approval policy (JSON format)
repair --format json policy view

# View current approval policy (table format)
repair policy view

# Modify policy (admin only) - allow admin self-approval
repair policy set --allow-admin-self-approval true --as-user admin_user

# Modify policy (admin only) - require different approver
repair policy set --require-different-approver true --as-user admin_user

# Modify both policies at once
repair policy set --allow-admin-self-approval false --require-different-approver false --as-user admin_user
```

### Export & Import

```bash
# Export all tasks to a plan file (includes policy)
repair plan export plan.json

# Export specific tasks
repair plan export plan.json --task-id 1 --task-id 2

# Validate import (dry run) - shows policy conflicts without modifying
repair plan import plan.json --dry-run

# Import into a new database
repair --db new.db plan import plan.json --as-user alice

# Import despite policy conflicts
repair plan import plan.json --ignore-policy-conflict --as-user alice
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
- **Self-Approval**: Configurable via approval policy. Default: Regular users cannot approve or reject their own tasks; Admin users are exempt. See `Approval Policy Configuration` below.
- **State Enforcement**: Actions only allowed from valid states
- **Window Expiry**: Cannot submit/approve tasks for windows that have ended
- **Rollback Timing**: Can only rollback after execution (SUCCEEDED or FAILED). Failed rollback attempts are logged to audit.
- **Window Lock**: Cannot modify window times if tasks are in active states
- **Import References**: Tasks must reference existing windows during import
- **Audit Logging**: All actions (including failed attempts and policy changes) are logged to the audit table with actor, action, status transitions, and reason.

### Approval Policy Configuration

Approval rules are no longer hardcoded - they can be configured by admin users and persist across restarts. Two policies are supported:

| Policy | Default | Description |
|--------|---------|-------------|
| `allow_admin_self_approval` | `true` | Whether admin users can approve/reject their own tasks |
| `require_different_approver` | `true` | Whether approver must be different from task creator |

Policy changes are:
- Restricted to admin users only
- Audited with before/after values
- Persisted to SQLite
- Included in plan exports
- Validated during plan imports with conflict detection

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
| `policy` | `view` | View current approval policy |
| `policy` | `set` | Update approval policy (admin only) |
| `plan` | `export` | Export plan to JSON (includes policy) |
| `plan` | `import` | Import plan from JSON (with policy conflict detection) |

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
repair --db verify_admin.db --format json task show 1

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
repair --db verify_rollback.db --format json audit list --task-id 1 | python -c "import sys,json; print('Initial audit count:', len(json.load(sys.stdin)))"

# Try to rollback before execution (should fail)
repair --db verify_rollback.db --format json task rollback 1 --as-user operator_user

# Verify task status is unchanged
repair --db verify_rollback.db --format json task show 1 | python -c "import sys,json; print('Status:', json.load(sys.stdin)['status'])"

# Verify audit log has the failure record
repair --db verify_rollback.db --format json audit list --task-id 1

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
repair --db verify_regular.db --format json task approve 1 --as-user operator_user

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

---

## Approval Policy Verification Commands

### Fix 3: View and Modify Approval Policy

Admin users can view and modify approval policies that are persisted across restarts:

```bash
# 1. View default policy (JSON format)
repair --db verify_policy.db --format json policy view

# 2. View default policy (table format)
repair --db verify_policy.db policy view

# 3. Regular user tries to modify policy (should fail)
repair --db verify_policy.db --format json policy set --allow-admin-self-approval false --as-user operator_user

# 4. Admin modifies policy - disable admin self-approval
repair --db verify_policy.db --format json policy set --allow-admin-self-approval false --as-user admin_user

# 5. Admin modifies policy - disable different approver requirement
repair --db verify_policy.db --format json policy set --require-different-approver false --as-user admin_user

# 6. Modify both policies at once
repair --db verify_policy.db --format json policy set --allow-admin-self-approval true --require-different-approver true --as-user admin_user

# 7. Partial update - only change one policy
repair --db verify_policy.db --format json policy set --allow-admin-self-approval false --as-user admin_user

# 8. Verify policy persistence by reconnecting
repair --db verify_policy.db --format json policy view

# 9. View audit logs for policy changes
repair --db verify_policy.db --format json audit list

# Cleanup
rm verify_policy.db
```

**Expected JSON output - Step 1 (default policy):**
```json
{
  "id": 1,
  "allow_admin_self_approval": true,
  "require_different_approver": true,
  "updated_by": null,
  "updated_at": "2026-06-08 07:17:12 UTC"
}
```

**Expected JSON output - Step 3 (regular user denied):**
```json
{
  "success": false,
  "error": "User 'operator_user' does not have admin role to update policy",
  "code": "policy_update_denied"
}
```

**Expected JSON output - Step 4 (successful policy update):**
```json
{
  "success": true,
  "message": "Policy updated",
  "changed_fields": ["allow_admin_self_approval"],
  "old_policy": {
    "allow_admin_self_approval": true,
    "require_different_approver": true
  },
  "new_policy": {
    "allow_admin_self_approval": false,
    "require_different_approver": true
  }
}
```

**Expected JSON output - Step 9 (policy audit logs):**
```json
[
  {
    "id": 5,
    "task_id": null,
    "action": "policy_updated",
    "actor": "admin_user",
    "old_status": null,
    "new_status": null,
    "details": "Policy updated. Changed fields: allow_admin_self_approval. Old: allow_admin_self_approval=True, require_different_approver=True. New: allow_admin_self_approval=False, require_different_approver=True",
    "created_at": "2026-06-08 07:18:32 UTC"
  },
  {
    "id": 1,
    "task_id": null,
    "action": "policy_update_denied",
    "actor": "operator_user",
    "old_status": null,
    "new_status": null,
    "details": "User 'operator_user' attempted to update policy without admin permission",
    "created_at": "2026-06-08 07:17:39 UTC"
  }
]
```

---

### Fix 4: Policy Affects Approval Behavior

Policy changes immediately affect approval/reject decisions:

```bash
# 1. Setup: Create window and disable admin self-approval
repair --db verify_policy_approval.db window create --name "policy-test" --start "-1h" --end "+3h" --as-user admin_user

repair --db verify_policy_approval.db policy set --allow-admin-self-approval false --require-different-approver true --as-user admin_user

# 2. Admin creates their own task
repair --db verify_policy_approval.db task create --name "admin-task" --description "Test" --sql "SELECT 1" --rollback-sql "SELECT 1" --window-id 1 --as-user admin_user

repair --db verify_policy_approval.db task submit 1 --as-user admin_user

# 3. Admin tries to approve their own task (should fail due to policy)
repair --db verify_policy_approval.db --format json task approve 1 --as-user admin_user

# 4. Change policy to allow admin self-approval
repair --db verify_policy_approval.db policy set --allow-admin-self-approval true --as-user admin_user

# 5. Admin can now approve their own task
repair --db verify_policy_approval.db --format json task approve 1 --as-user admin_user

# Cleanup
rm verify_policy_approval.db
```

**Expected error JSON - Step 3 (policy blocks admin self-approval):**
```json
{
  "success": false,
  "error": "User 'admin_user' cannot approve their own task (created_by=admin_user). Policy requires different approver.",
  "code": "self_approval_not_allowed"
}
```

**Expected success JSON - Step 5 (policy allows admin self-approval):**
```json
{
  "success": true,
  "message": "Task 1 approved",
  "id": 1,
  "status": "approved",
  "approved_by": "admin_user"
}
```

---

### Fix 5: Export/Import with Policy

Plans include policy information and imports detect conflicts:

```bash
# 1. Setup: Create window, task, and custom policy
repair --db verify_policy_export.db window create --name "export-test" --start "-1h" --end "+3h" --as-user admin_user

repair --db verify_policy_export.db task create --name "export-task" --description "test" --sql "SELECT 1" --rollback-sql "SELECT 1" --window-id 1 --as-user operator_user

repair --db verify_policy_export.db policy set --allow-admin-self-approval false --require-different-approver false --as-user admin_user

# 2. Export plan (includes policy)
repair --db verify_policy_export.db plan export policy_plan.json

# 3. View exported policy in file
python -c "import json; plan=json.load(open('policy_plan.json')); print(json.dumps(plan['policy'], indent=2))"

# 4. Dry-run import to new DB with default policy (shows conflict)
repair --db verify_policy_import.db --format json plan import policy_plan.json --dry-run --as-user importer

# 5. Actual import without --ignore-policy-conflict (fails due to conflict)
repair --db verify_policy_import2.db --format json plan import policy_plan.json --as-user importer

# 6. Import with --ignore-policy-conflict (succeeds)
repair --db verify_policy_import3.db --format json plan import policy_plan.json --ignore-policy-conflict --as-user importer

# 7. Verify imported policy affects behavior
repair --db verify_policy_import3.db --format json policy view

# Cleanup
rm verify_policy_export.db verify_policy_import.db verify_policy_import2.db verify_policy_import3.db policy_plan.json
```

**Expected JSON output - Step 4 (dry-run shows policy conflict):**
```json
{
  "success": true,
  "message": "Import validation passed (dry run)",
  "windows_to_import": 1,
  "tasks_to_import": 1,
  "audits_to_import": 1,
  "policy_to_import": {
    "allow_admin_self_approval": false,
    "require_different_approver": false,
    "updated_by": "admin_user",
    "updated_at": "2026-06-08T07:40:46.955205"
  },
  "policy_conflicts": [
    "Policy conflict detected: allow_admin_self_approval: local=True, imported=False; require_different_approver: local=True, imported=False. Import will overwrite local policy."
  ],
  "note": "Use --ignore-policy-conflict to import despite conflicts"
}
```

**Expected error JSON - Step 5 (policy conflict blocks import):**
```json
{
  "success": false,
  "error": "Policy conflict detected. Use --ignore-policy-conflict to proceed. Policy conflict detected: allow_admin_self_approval: local=True, imported=False; require_different_approver: local=True, imported=False. Import will overwrite local policy.",
  "code": "policy_conflict"
}
```

**Expected JSON output - Step 6 (successful import with conflict override):**
```json
{
  "success": true,
  "message": "Plan imported successfully",
  "windows_imported": 1,
  "tasks_imported": 1,
  "audits_imported": 1,
  "policy_imported": true,
  "window_id_map": {
    "1": 1
  },
  "task_id_map": {
    "1": 1
  },
  "policy_conflicts_resolved": [
    "Policy conflict detected: allow_admin_self_approval: local=True, imported=False; require_different_approver: local=True, imported=False. Import will overwrite local policy."
  ]
}
```

### Policy Error Code Reference

| Error Code | Description |
|------------|-------------|
| `policy_update_denied` | Non-admin user attempted to modify policy |
| `policy_conflict` | Imported policy differs from local policy |
| `missing_policy_option` | `policy set` called without any policy options |
| `self_approval_not_allowed` | User cannot approve own task per policy |
| `self_rejection_not_allowed` | User cannot reject own task per policy |
