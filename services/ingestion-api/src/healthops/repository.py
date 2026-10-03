import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from healthops.models import Observation
from healthops.schemas import BatchResult, ObservationIn


class SourceConflict(Exception):
    """The same provider identity was reused for a different payload."""


def fingerprint(record: ObservationIn) -> str:
    payload = record.model_dump(mode="json")
    # Treat 70, 70.0 and equivalent UTC timestamp encodings as the same record.
    payload["value"] = format(record.value.normalize(), "f")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ingest_batch(session: Session, user_id: UUID, records: list[ObservationIn]) -> BatchResult:
    inserted = 0
    try:
        # Stable lock order reduces deadlock risk for concurrent overlapping batches.
        for record in sorted(records, key=lambda r: (r.provider, r.source_record_id, r.metric)):
            digest = fingerprint(record)
            statement = (
                insert(Observation)
                .values(user_id=user_id, payload_hash=digest, **record.model_dump())
                .on_conflict_do_nothing(constraint="uq_source_record")
                .returning(Observation.id)
            )
            if session.scalar(statement) is not None:
                inserted += 1
            else:
                existing_hash = session.scalar(
                    select(Observation.payload_hash).where(
                        Observation.user_id == user_id,
                        Observation.provider == record.provider,
                        Observation.source_record_id == record.source_record_id,
                        Observation.metric == record.metric,
                    )
                )
                if existing_hash != digest:
                    raise SourceConflict
        session.commit()
    except Exception:
        session.rollback()
        raise
    return BatchResult(received=len(records), inserted=inserted, duplicates=len(records) - inserted)


class ConnectorIngestResult:
    """Per-record outcome for a connector sync: conflicts are reported, never overwritten."""

    def __init__(self):
        self.inserted = 0
        self.duplicates = 0
        self.conflicts: list[str] = []  # source_record_id values (no measurement values)


def ingest_tolerant(
    session: Session, user_id: UUID, records: list[ObservationIn]
) -> ConnectorIngestResult:
    """Ingest one record at a time so one upstream revision does not fail the whole page.

    A record whose provider identity already exists with a different payload is an upstream
    revision. The stored value is kept and the identity is reported in ``conflicts`` so the
    sync result shows it explicitly; nothing is silently replaced.
    """
    result = ConnectorIngestResult()
    for record in records:
        try:
            batch = ingest_batch(session, user_id, [record])
        except SourceConflict:
            result.conflicts.append(record.source_record_id)
            continue
        result.inserted += batch.inserted
        result.duplicates += batch.duplicates
    return result
