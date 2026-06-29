"""Owner-only database backup management.

Lists the gzipped pg_dump backups stored in the configured Google Drive folder
and lets the owner trigger a fresh backup, preview a backup as a per-table
row-count diff against the live DB, or restore one over the live DB.

Every endpoint is gated by require_owner, so only OWNER_EMAIL can reach them.
Handlers are sync (`def`) so the slow pg_dump/psql subprocesses run in the
threadpool instead of blocking the event loop.
"""

from fastapi import APIRouter, Depends, HTTPException

from core.config import DB_BACKUP_DRIVE_FOLDER_ID, DB_BACKUP_RETENTION
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
def run_backup(idinfo: dict = Depends(require_owner)):
    try:
        return db_backup.run_db_backup()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@router.get("/{file_id}/preview")
def preview(file_id: str, idinfo: dict = Depends(require_owner)):
    try:
        return db_backup.preview_backup(file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e}")


@router.post("/{file_id}/restore")
def restore(file_id: str, idinfo: dict = Depends(require_owner)):
    try:
        return db_backup.restore_backup(file_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
