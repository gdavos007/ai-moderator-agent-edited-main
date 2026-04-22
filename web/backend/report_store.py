"""Filesystem-backed store for focus-group transcripts and generated reports.

Layout (all paths relative to REPORT_DATA_DIR, default ``/data``):

    transcripts/{session_id}.json          # raw transcript uploaded by the agent
    reports/{session_id}.meta.json         # status + versions + timestamps
    reports/{session_id}.v{N}.json         # generated report JSON, one file per version

We use JSON files rather than a database on purpose — the user explicitly
declined to introduce Postgres for the MVP.  Atomicity is ensured by writing
to a temporary file and ``os.replace()``-ing into place.

All public helpers are synchronous and cheap.  They are safe to call from
within FastAPI handlers without offloading to a threadpool.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_DEFAULT_DATA_DIR = Path(os.environ.get("REPORT_DATA_DIR", "/data"))

TRANSCRIPTS_DIR = _DEFAULT_DATA_DIR / "transcripts"
REPORTS_DIR = _DEFAULT_DATA_DIR / "reports"

# Accept only simple session ids — defends against path traversal even though
# the rest of the stack never takes session_id from untrusted input.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

# --------------------------------------------------------------------------- #
# Status constants
# --------------------------------------------------------------------------- #
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

VALID_STATUSES = {STATUS_PENDING, STATUS_PROCESSING, STATUS_COMPLETE, STATUS_FAILED}


# --------------------------------------------------------------------------- #
# Public dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class ReportMeta:
    session_id: str
    status: str = STATUS_PENDING
    latest_version: int = 0
    versions: List[int] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_error: Optional[str] = None
    title: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "latest_version": self.latest_version,
            "versions": self.versions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "title": self.title,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReportMeta":
        return cls(
            session_id=d["session_id"],
            status=d.get("status", STATUS_PENDING),
            latest_version=d.get("latest_version", 0),
            versions=list(d.get("versions", [])),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            last_error=d.get("last_error"),
            title=d.get("title"),
            started_at=d.get("started_at"),
            ended_at=d.get("ended_at"),
        )


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id!r}")


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as JSON to ``path`` atomically.

    Write goes to a sibling tempfile then ``os.replace`` swaps it in so
    concurrent readers never observe a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Malformed JSON at {path}: {e}")
        return None


def _meta_path(session_id: str) -> Path:
    return REPORTS_DIR / f"{session_id}.meta.json"


def _report_path(session_id: str, version: int) -> Path:
    return REPORTS_DIR / f"{session_id}.v{version}.json"


def _transcript_path(session_id: str) -> Path:
    return TRANSCRIPTS_DIR / f"{session_id}.json"


# --------------------------------------------------------------------------- #
# Bootstrapping
# --------------------------------------------------------------------------- #
def ensure_dirs() -> None:
    """Create transcript/report directories if missing.

    Called from the FastAPI startup hook so the first write doesn't race
    the first read.
    """
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Transcript ops
# --------------------------------------------------------------------------- #
def save_transcript(session_id: str, payload: Dict[str, Any]) -> Path:
    """Persist the full transcript upload payload from the agent.

    ``payload`` is the raw JSON the agent POSTs — includes the transcript
    object plus session-level metadata (title, started_at, participants,
    discussion-guide questions).  We store it verbatim so the report
    generator gets everything it needs in one read.
    """
    _validate_session_id(session_id)
    path = _transcript_path(session_id)
    _atomic_write_json(path, payload)
    logger.info(f"Transcript saved: {path}")
    return path


def load_transcript(session_id: str) -> Optional[Dict[str, Any]]:
    _validate_session_id(session_id)
    return _read_json(_transcript_path(session_id))


def transcript_exists(session_id: str) -> bool:
    _validate_session_id(session_id)
    return _transcript_path(session_id).exists()


# --------------------------------------------------------------------------- #
# Meta ops (status + version tracking)
# --------------------------------------------------------------------------- #
def get_meta(session_id: str) -> Optional[ReportMeta]:
    _validate_session_id(session_id)
    raw = _read_json(_meta_path(session_id))
    if raw is None:
        return None
    return ReportMeta.from_dict(raw)


def _save_meta(meta: ReportMeta) -> None:
    meta.updated_at = _now_iso()
    _atomic_write_json(_meta_path(meta.session_id), meta.to_dict())


def init_meta(
    session_id: str,
    *,
    title: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
) -> ReportMeta:
    """Create meta with status=pending.  Idempotent on same session_id.

    If meta already exists, it is returned as-is (caller should call
    ``bump_to_processing`` explicitly before regenerating).
    """
    _validate_session_id(session_id)
    existing = get_meta(session_id)
    if existing is not None:
        return existing

    meta = ReportMeta(
        session_id=session_id,
        status=STATUS_PENDING,
        created_at=_now_iso(),
        title=title,
        started_at=started_at,
        ended_at=ended_at,
    )
    _save_meta(meta)
    return meta


def set_status(session_id: str, status: str, *, error: Optional[str] = None) -> ReportMeta:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    meta = get_meta(session_id)
    if meta is None:
        raise FileNotFoundError(f"No meta for session_id={session_id}")
    meta.status = status
    meta.last_error = error if status == STATUS_FAILED else None
    _save_meta(meta)
    return meta


# --------------------------------------------------------------------------- #
# Report ops (versioned)
# --------------------------------------------------------------------------- #
def save_report(session_id: str, report: Dict[str, Any]) -> int:
    """Write ``report`` as the next version and update meta.

    Returns the new version number.  Bumps ``latest_version`` and appends
    to ``versions``.  Sets status to ``complete``.
    """
    _validate_session_id(session_id)
    meta = get_meta(session_id)
    if meta is None:
        # Tolerate missing meta — create one on the fly.  This shouldn't
        # happen in normal flow (init_meta runs before save_report), but
        # it keeps the function usable for ad-hoc repair.
        meta = init_meta(session_id)

    next_version = meta.latest_version + 1
    _atomic_write_json(_report_path(session_id, next_version), report)

    meta.latest_version = next_version
    meta.versions.append(next_version)
    meta.status = STATUS_COMPLETE
    meta.last_error = None
    _save_meta(meta)
    return next_version


def load_report(session_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Return the report JSON for the given version (or latest if unspecified)."""
    _validate_session_id(session_id)
    meta = get_meta(session_id)
    if meta is None or meta.latest_version == 0:
        return None
    v = version if version is not None else meta.latest_version
    if v not in meta.versions:
        return None
    return _read_json(_report_path(session_id, v))


# --------------------------------------------------------------------------- #
# Discovery (session list page)
# --------------------------------------------------------------------------- #
def list_sessions() -> List[ReportMeta]:
    """Return all known sessions, newest ``created_at`` first.

    A "known session" is one with either a transcript or a meta file on
    disk.  We prefer the meta record when both exist.
    """
    ensure_dirs()
    session_ids: set[str] = set()

    for p in TRANSCRIPTS_DIR.glob("*.json"):
        if _SESSION_ID_RE.match(p.stem):
            session_ids.add(p.stem)

    for p in REPORTS_DIR.glob("*.meta.json"):
        stem = p.name[: -len(".meta.json")]
        if _SESSION_ID_RE.match(stem):
            session_ids.add(stem)

    metas: List[ReportMeta] = []
    for sid in session_ids:
        meta = get_meta(sid)
        if meta is None:
            # Transcript exists but no meta yet — synthesize a pending one
            # so the session appears in the list.
            meta = ReportMeta(session_id=sid, status=STATUS_PENDING)
            # Try to hydrate a few fields from the transcript if available
            t = load_transcript(sid)
            if t:
                meta.title = t.get("title")
                meta.started_at = t.get("started_at") or t.get("transcript", {}).get("session_start")
                meta.ended_at = t.get("ended_at") or t.get("transcript", {}).get("session_end")
        metas.append(meta)

    metas.sort(key=lambda m: (m.created_at or m.started_at or ""), reverse=True)
    return metas
