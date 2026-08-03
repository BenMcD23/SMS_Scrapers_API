"""Owner-only database backup management.

Lists the gzipped pg_dump backups stored in the configured Google Drive folder
and lets the owner trigger a fresh backup, preview a backup as a per-table
row-count diff against the live DB, or restore one over the live DB.

Every endpoint is gated by require_owner, so only OWNER_EMAIL can reach them.
Handlers are sync (`def`) so the slow pg_dump/psql subprocesses run in the
threadpool instead of blocking the event loop.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.audit import record_audit
from core.config import DB_BACKUP_DRIVE_FOLDER_ID, DB_BACKUP_RETENTION
from core.db import get_db
from core.security import require_owner
from scripts import db_backup

router = APIRouter(prefix="/backups", tags=["backups"])


@router.get("")
def get_backups(idinfo: dict = Depends(require_owner)):
    try:
        return {
            "retention": DB_BACKUP_RETENTION,
            "folder_id": DB_BACKUP_DRIVE_FOLDER_ID,
            "backups": db_backup.list_backups(),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not list backups: {e}")


@router.post("/run")
def run_backup(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_owner),
):
    try:
        result = db_backup.run_db_backup()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    # A backup is a full copy of every cadet and staff record leaving the box.
    record_audit(db, idinfo, action="run", resource="db_backup")
    return result


@router.get("/{file_id}/preview")
def preview(
    file_id: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_owner),
):
    try:
        result = db_backup.preview_backup(file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e}")
    record_audit(db, idinfo, action="preview", resource="db_backup", resource_id=file_id)
    return result


@router.post("/{file_id}/restore")
def restore(
    file_id: str,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_owner),
):
    # Recorded before the restore, not after: restore_backup drops and recreates
    # the database, so a row written afterwards would land in a session whose DB
    # was pulled out from under it. Note the trail cannot survive its own
    # restore either — after this the Audit_Events table is whatever the backup
    # held, so the stdout log is the only record that the restore happened.
    record_audit(db, idinfo, action="restore", resource="db_backup", resource_id=file_id)
    print(f"[backups] restore of {file_id} started by {idinfo.get('email')}")
    try:
        return db_backup.restore_backup(file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
