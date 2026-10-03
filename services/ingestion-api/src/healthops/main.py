import asyncio
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import AwareDatetime
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from healthops.assistant.contracts import AskRequest, AskResponse
from healthops.assistant.debug_page import DEBUG_PAGE_HTML
from healthops.assistant.limits import RateLimiter
from healthops.assistant.model import ChatModel, OllamaChatModel, OpenAICompatibleChatModel
from healthops.assistant.orchestrator import Assistant, AssistantSettings, TodayOverrideNotAllowed
from healthops.assistant.prompts import PROMPT_VERSION
from healthops.assistant.toolbox import Toolbox
from healthops.auth import SessionDep, UserDep, bearer, current_user
from healthops.config import Settings
from healthops.db import build_engine
from healthops.models import Observation, User
from healthops.repository import SourceConflict, ingest_batch
from healthops.schemas import (
    BatchResult,
    Metric,
    ObservationBatch,
    ObservationOut,
    ObservationPage,
    Provider,
    UserOut,
)
from healthops.tools.availability import availability_tool
from healthops.tools.contracts import (
    AvailabilityParams,
    AvailabilityResult,
    DailySummaryParams,
    DailySummaryResult,
    PeriodComparisonParams,
    PeriodComparisonResult,
)
from healthops.tools.daily_summary import daily_summary_tool
from healthops.tools.local_days import ToolInputError
from healthops.tools.period_comparison import period_comparison_tool

TimezoneQuery = Annotated[str, Query(min_length=1, max_length=80)]


def build_assistant(
    settings: Settings, session_factory: sessionmaker, chat_model: ChatModel | None
):
    """The assistant exists only when a model endpoint is configured or a model is injected."""
    if chat_model is None:
        if not settings.assistant_model_base_url:
            return None
        adapters = {"ollama": OllamaChatModel, "openai_compatible": OpenAICompatibleChatModel}
        adapter = adapters.get(settings.assistant_model_backend)
        if adapter is None:
            raise ValueError(
                f"unknown assistant_model_backend {settings.assistant_model_backend!r}"
            )
        chat_model = adapter(
            settings.assistant_model_base_url,
            settings.assistant_model_name,
            prompt_version=PROMPT_VERSION,
            timeout_seconds=settings.assistant_model_timeout_seconds,
            max_output_tokens=settings.assistant_max_output_tokens,
            temperature=settings.assistant_temperature,
            thinking=settings.assistant_thinking,
        )
    return Assistant(
        chat_model,
        Toolbox(session_factory),
        AssistantSettings(
            max_tool_calls=settings.assistant_max_tool_calls,
            max_model_turns=settings.assistant_max_model_turns,
            deadline_seconds=settings.assistant_deadline_seconds,
            model_context_budget_chars=settings.assistant_model_context_budget_chars,
            evidence_budget_bytes=settings.assistant_evidence_budget_bytes,
            max_concurrency=settings.assistant_max_concurrency,
            allow_today_override=settings.assistant_allow_today_override,
        ),
    )


def create_app(settings: Settings | None = None, chat_model: ChatModel | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.require_local()
    engine = build_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="HealthOps local ingestion API", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.assistant = build_assistant(settings, app.state.session_factory, chat_model)
    app.state.assistant_limiter = RateLimiter(settings.assistant_rate_limit_per_minute)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI's default validation body can echo private input values.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "loc": list(e["loc"]),
                        "type": e["type"],
                        "msg": "Invalid field or inconsistent measurement.",
                    }
                    for e in exc.errors()
                ]
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def storage_failure(request: Request, exc: SQLAlchemyError):
        # Do not send SQL parameters, connection strings, or health payloads to the client.
        return JSONResponse(status_code=503, content={"detail": "Storage temporarily unavailable."})

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready(session: SessionDep):
        # Checks connectivity AND that the initial schema exists.
        session.execute(select(User.id).limit(1))
        return {"status": "ready"}

    @app.get("/v1/me", response_model=UserOut)
    def me(user: UserDep):
        return user

    @app.post("/v1/observations/batch", response_model=BatchResult)
    def upload(batch: ObservationBatch, user: UserDep, session: SessionDep):
        try:
            return ingest_batch(session, user.id, batch.observations)
        except SourceConflict as exc:
            raise HTTPException(
                409, "Source record already exists with different content; batch was not saved."
            ) from exc

    @app.get("/v1/observations", response_model=ObservationPage)
    def history(
        user: UserDep,
        session: SessionDep,
        start: AwareDatetime | None = None,
        end: AwareDatetime | None = None,
        metric: Metric | None = None,
        provider: Provider | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ):
        if start is not None and end is not None and end <= start:
            raise HTTPException(422, "end must be later than start.")
        statement = select(Observation).where(Observation.user_id == user.id)
        # Start-inclusive/end-exclusive window on the record's START time.
        if start is not None:
            statement = statement.where(Observation.started_at >= start)
        if end is not None:
            statement = statement.where(Observation.started_at < end)
        if metric is not None:
            statement = statement.where(Observation.metric == metric)
        if provider is not None:
            statement = statement.where(Observation.provider == provider)
        rows = session.scalars(
            statement.order_by(Observation.started_at, Observation.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return ObservationPage(
            items=[ObservationOut.model_validate(row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    @app.delete("/v1/observations/{observation_id}", status_code=204)
    def delete_observation(observation_id: UUID, user: UserDep, session: SessionDep):
        deleted = session.scalar(
            delete(Observation)
            .where(
                Observation.id == observation_id,
                Observation.user_id == user.id,
            )
            .returning(Observation.id)
        )
        if deleted is None:
            raise HTTPException(404, "Observation not found.")
        session.commit()
        return Response(status_code=204)

    # Tool routes: authentication and parameter parsing only. The authenticated user_id is
    # injected into the tool; neither clients nor a future LLM can choose whose data to read.
    @app.get("/v1/tools/availability", response_model=AvailabilityResult)
    def tool_availability(
        user: UserDep,
        session: SessionDep,
        timezone: TimezoneQuery,
        start_date: date,
        end_date: date,
    ):
        params = AvailabilityParams(timezone=timezone, start_date=start_date, end_date=end_date)
        try:
            return availability_tool(session, user.id, params)
        except ToolInputError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/tools/daily-summary", response_model=DailySummaryResult)
    def tool_daily_summary(
        user: UserDep,
        session: SessionDep,
        metric: Metric,
        timezone: TimezoneQuery,
        start_date: date,
        end_date: date,
        provider: Provider | None = None,
    ):
        params = DailySummaryParams(
            metric=metric,
            timezone=timezone,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
        )
        try:
            return daily_summary_tool(session, user.id, params)
        except ToolInputError as exc:
            # Explicit, value-free messages: limits are refused, never silently truncated.
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/tools/period-comparison", response_model=PeriodComparisonResult)
    def tool_period_comparison(
        user: UserDep,
        session: SessionDep,
        metric: Metric,
        timezone: TimezoneQuery,
        baseline_start: date,
        baseline_end: date,
        current_start: date,
        current_end: date,
        provider: Provider | None = None,
    ):
        params = PeriodComparisonParams(
            metric=metric,
            timezone=timezone,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            current_start=current_start,
            current_end=current_end,
            provider=provider,
        )
        try:
            return period_comparison_tool(session, user.id, params)
        except ToolInputError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/debug/assistant", response_class=HTMLResponse, include_in_schema=False)
    def assistant_debug_page():
        # Local verification page, not the iOS chat feature. Same origin, no build step, no
        # token in the source; the page renders API text only as DOM text nodes.
        return HTMLResponse(
            DEBUG_PAGE_HTML,
            headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/v1/assistant/ask", response_model=AskResponse)
    async def assistant_ask(
        body: AskRequest,
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        # Authentication uses its own short-lived session that is closed before the request
        # waits for the concurrency gate or the model, so no pooled connection is held while
        # the model thinks. Tools open their own short-lived sessions in a worker thread.
        assistant: Assistant | None = request.app.state.assistant
        if assistant is None:
            raise HTTPException(503, "Assistant not configured: no model endpoint is set.")

        def authenticate():
            with request.app.state.session_factory() as session:
                return current_user(session, credentials).id

        user_id = await asyncio.to_thread(authenticate)
        limiter: RateLimiter = request.app.state.assistant_limiter
        if not limiter.allow(user_id):
            raise HTTPException(
                429,
                "Too many questions; wait and retry.",
                headers={"Retry-After": str(limiter.retry_after_seconds(user_id))},
            )
        try:
            return await assistant.ask(body, user_id)
        except TodayOverrideNotAllowed as exc:
            raise HTTPException(
                422, "The today override is only allowed in evaluation mode."
            ) from exc
        except ToolInputError as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


app = create_app()
