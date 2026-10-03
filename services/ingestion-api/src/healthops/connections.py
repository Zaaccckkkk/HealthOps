"""Routes for server-side provider connections (Google Health first).

Flow: the app, authenticated with its bearer token, creates a connection session; the server
binds a one-time ``state`` to the user and returns Google's authorization URL; the phone opens
it in a browser session; Google redirects to the callback, which validates the state, exchanges
the code with the server-side client secret, checks the Google Health identity, stores the
encrypted refresh token and sends the browser back to the app's URL scheme with a status word
only. No HealthOps token ever travels in a URL and no Google token ever reaches the phone.
"""

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from healthops.auth import SessionDep, UserDep
from healthops.connectors.crypto import TokenCipher
from healthops.connectors.google_health import (
    PROVIDER,
    SCOPES,
    AccountNotLinked,
    GoogleApiError,
    GoogleHealthClient,
    SyncReport,
    sync_window,
)
from healthops.models import ConnectionSession, Observation, ProviderConnection
from healthops.repository import ingest_tolerant

router = APIRouter(prefix="/v1/connections/google-health", tags=["connections"])

SESSION_TTL = timedelta(minutes=10)


class SessionRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)


class SessionResponse(BaseModel):
    authorization_url: str
    expires_in_seconds: int


class ConnectionStatus(BaseModel):
    configured: bool
    connected: bool
    provider: str = PROVIDER
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    linked_account: bool | None = None
    last_sync_report: dict | None = None
    # Outcome of the most recent authorization attempt, so the app can explain a failure.
    last_session_outcome: str | None = None


class SyncRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)
    days: int | None = Field(default=None, ge=1)


class DisconnectResult(BaseModel):
    disconnected: bool
    revoked_at_provider: bool


class DeleteDataResult(BaseModel):
    deleted: int


def _client(request: Request) -> GoogleHealthClient:
    client = getattr(request.app.state, "google_health", None)
    if client is None:
        raise HTTPException(503, "Google Health connector is not configured on this server.")
    return client


def _cipher(request: Request) -> TokenCipher:
    return request.app.state.token_cipher


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "Expected an IANA timezone such as America/New_York.") from exc


def _connection(session, user_id) -> ProviderConnection | None:
    return session.scalar(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id, ProviderConnection.provider == PROVIDER
        )
    )


@router.post("/session", response_model=SessionResponse)
def create_session(body: SessionRequest, request: Request, user: UserDep, session: SessionDep):
    client = _client(request)
    _zone(body.timezone)
    state = secrets.token_urlsafe(32)
    session.add(
        ConnectionSession(
            state=state,
            user_id=user.id,
            provider=PROVIDER,
            timezone=body.timezone,
            expires_at=datetime.now(UTC) + SESSION_TTL,
        )
    )
    session.commit()
    return SessionResponse(
        authorization_url=client.authorization_url(state),
        expires_in_seconds=int(SESSION_TTL.total_seconds()),
    )


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    session: SessionDep,
    state: Annotated[str, Query(min_length=1, max_length=64)],
    code: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
):
    client = _client(request)
    target = request.app.state.settings.app_callback_url
    record = session.get(ConnectionSession, state)
    now = datetime.now(UTC)
    if record is None or record.used_at is not None or record.expires_at < now:
        # Unknown, replayed or expired state: nothing is stored; the browser only learns that.
        return RedirectResponse(f"{target}?status=invalid_state", status_code=303)
    record.used_at = now
    outcome = "connected"
    try:
        if error or not code:
            outcome = "denied"
        else:
            tokens = client.exchange_code(code)
            refresh_token = tokens.get("refresh_token")
            access_token = tokens.get("access_token", "")
            if not refresh_token:
                outcome = "no_refresh_token"
            else:
                try:
                    identity = client.identity(access_token)
                except AccountNotLinked:
                    client.revoke(refresh_token)
                    outcome = "account_not_linked"
                else:
                    cipher = _cipher(request)
                    connection = _connection(session, record.user_id)
                    if connection is None:
                        connection = ProviderConnection(user_id=record.user_id, provider=PROVIDER)
                        session.add(connection)
                    connection.encrypted_refresh_token = cipher.encrypt(refresh_token)
                    connection.scopes = tokens.get("scope", " ".join(SCOPES))
                    connection.external_user_id = (
                        str(identity.get("healthUserId") or "")[:100] or None
                    )
                    connection.legacy_user_id = (
                        str(identity.get("legacyUserId") or "")[:100] or None
                    )
                    connection.connected_at = now
    except GoogleApiError as exc:
        outcome = "token_exchange_failed" if exc.status < 500 else "provider_unavailable"
    record.outcome = outcome
    session.commit()
    return RedirectResponse(f"{target}?status={outcome}", status_code=303)


@router.get("", response_model=ConnectionStatus)
def status(request: Request, user: UserDep, session: SessionDep):
    configured = getattr(request.app.state, "google_health", None) is not None
    connection = _connection(session, user.id)
    last_session = session.scalar(
        select(ConnectionSession.outcome)
        .where(ConnectionSession.user_id == user.id, ConnectionSession.outcome.is_not(None))
        .order_by(ConnectionSession.created_at.desc())
        .limit(1)
    )
    if connection is None:
        return ConnectionStatus(
            configured=configured, connected=False, last_session_outcome=last_session
        )
    return ConnectionStatus(
        configured=configured,
        connected=True,
        connected_at=connection.connected_at,
        last_sync_at=connection.last_sync_at,
        scopes=connection.scopes.split(),
        linked_account=bool(connection.external_user_id),
        last_sync_report=json.loads(connection.last_sync_report)
        if connection.last_sync_report
        else None,
        last_session_outcome=last_session,
    )


@router.post("/sync", response_model=SyncReport)
def sync(body: SyncRequest, request: Request, user: UserDep, session: SessionDep):
    client = _client(request)
    settings = request.app.state.settings
    tz = _zone(body.timezone)
    days = body.days or settings.connection_sync_default_days
    if days > settings.connection_sync_max_days:
        raise HTTPException(422, f"days must be at most {settings.connection_sync_max_days}.")
    connection = _connection(session, user.id)
    if connection is None:
        raise HTTPException(404, "Google Health is not connected for this account.")
    try:
        refresh_token = _cipher(request).decrypt(connection.encrypted_refresh_token)
    except ValueError as exc:
        raise HTTPException(
            409, "Stored credential cannot be used; reconnect Google Health."
        ) from exc
    try:
        access_token = client.refresh_access_token(refresh_token)
    except GoogleApiError as exc:
        if exc.status in (400, 401):
            raise HTTPException(
                409, "Google authorization expired; reconnect Google Health."
            ) from exc
        raise HTTPException(502, "Google is not reachable right now.") from exc
    today = datetime.now(tz).date()
    window_start = today - timedelta(days=days - 1)
    cursors = json.loads(connection.cursors or "{}")
    user_id = user.id

    def ingest(records):
        return ingest_tolerant(session, user_id, records)

    report = sync_window(client, access_token, ingest, window_start, today, tz, cursors)
    connection.cursors = json.dumps(cursors)
    connection.last_sync_report = report.to_json()
    connection.last_sync_at = datetime.now(UTC)
    session.commit()
    return report


@router.delete("", response_model=DisconnectResult)
def disconnect(request: Request, user: UserDep, session: SessionDep):
    client = _client(request)
    connection = _connection(session, user.id)
    if connection is None:
        raise HTTPException(404, "Google Health is not connected for this account.")
    revoked = False
    try:
        revoked = client.revoke(_cipher(request).decrypt(connection.encrypted_refresh_token))
    except (ValueError, GoogleApiError, OSError):
        revoked = False  # the local credential is removed regardless; the status says so
    session.delete(connection)
    session.commit()
    return DisconnectResult(disconnected=True, revoked_at_provider=revoked)


@router.delete("/data", response_model=DeleteDataResult)
def delete_synced_data(user: UserDep, session: SessionDep):
    """Separate from disconnecting: removes every observation this connector stored."""
    count = session.scalar(
        select(func.count())
        .select_from(Observation)
        .where(Observation.user_id == user.id, Observation.provider == PROVIDER)
    )
    session.execute(
        delete(Observation).where(Observation.user_id == user.id, Observation.provider == PROVIDER)
    )
    session.commit()
    return DeleteDataResult(deleted=int(count or 0))
