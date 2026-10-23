"""The AI orchestrator.

An agent is a typed function: code fetches the data, the agent asks the model for JSON, and the
answer is validated and checked against the input before anything uses it. Agents never touch the
database. The orchestrator runs pipelines of steps and tracks status, cost and cancellation.
"""
import logging
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.modules.ai.llm import LLMRequest, ProviderError, ProviderTimeout, cost_usd, get_provider
from app.modules.ai.models import AIRun, AIRunStep
from app.modules.auth.models import User

log = logging.getLogger(__name__)


class GroundingError(ValueError):
    """The model's answer is well-formed but not supported by the data it was given."""


class Cancelled(Exception):
    pass


class RunFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AgentFailed(Exception):
    def __init__(self, reason: str, attempts: int, tokens_in: int, tokens_out: int, cost: float):
        super().__init__(reason)
        self.reason, self.attempts = reason, attempts
        self.tokens_in, self.tokens_out, self.cost_usd = tokens_in, tokens_out, cost


@dataclass
class Agent:
    name: str
    task: str
    prompt_version: str
    system: str
    output_model: type[BaseModel]
    build_prompt: Callable[[dict], str]
    ground: Callable[[BaseModel, dict], dict]  # checks the answer against the input; may raise GroundingError
    fallback: Callable[[dict], dict] | None = None  # a non-AI answer, if one is meaningful
    max_output_tokens: int = 1200


@dataclass
class AgentResult:
    output: dict
    source: str  # "ai" or "fallback"
    attempts: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    error: str | None = None  # why the fallback was used


@dataclass
class Step:
    name: str
    kind: str  # "service" (runs on the main thread, may use the database) or "agent"
    depends_on: tuple[str, ...] = ()
    run: Callable[[dict, Session], dict] | None = None
    agent: Agent | None = None
    payload: Callable[[dict], dict] | None = None


@dataclass
class Pipeline:
    task: str
    steps: list[Step]
    result: Callable[[dict], dict]
    requires_approval: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _call_with_timeout(provider, request: LLMRequest, timeout: float):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(provider.complete, request)
    try:
        return future.result(timeout=timeout)
    except ProviderError:
        raise
    except FutureTimeout:
        raise ProviderTimeout()  # the abandoned call finishes in the background; its answer is discarded
    except Exception as exc:
        raise ProviderError("The AI provider failed unexpectedly") from exc
    finally:
        pool.shutdown(wait=False)


def run_agent(
    agent: Agent,
    payload: dict,
    *,
    provider,
    cancelled: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> AgentResult:
    """One agent call with timeout, retries, validation, one repair attempt, and a fallback."""
    settings = get_settings()
    schema = agent.output_model.model_json_schema()
    base_prompt = agent.build_prompt(payload)
    attempts = transient = repairs = 0
    tokens_in = tokens_out = 0
    cost = 0.0
    repair_note: str | None = None
    reason = "invalid_output"

    while True:
        if cancelled():
            raise Cancelled()
        attempts += 1
        prompt = base_prompt
        if repair_note:
            prompt += f"\n\nYour previous answer was rejected: {repair_note}\nReturn corrected JSON only."
        request = LLMRequest(
            task=agent.task, system=agent.system, user=prompt, json_schema=schema, payload=payload,
            max_output_tokens=agent.max_output_tokens, timeout_seconds=settings.ai_timeout_seconds,
        )
        try:
            response = _call_with_timeout(provider, request, settings.ai_timeout_seconds)
        except ProviderError as exc:
            reason = "provider_error"
            if exc.retryable and transient < settings.ai_max_retries:
                transient += 1
                sleep(settings.ai_retry_backoff_seconds * 2 ** (transient - 1))
                continue
            break

        tokens_in += response.tokens_in
        tokens_out += response.tokens_out
        cost += cost_usd(response.model, response.tokens_in, response.tokens_out)
        try:
            parsed = agent.output_model.model_validate_json(_strip_fences(response.text))
            output = agent.ground(parsed, payload)
            return AgentResult(output, "ai", attempts, tokens_in, tokens_out, round(cost, 6))
        except ValueError as exc:  # includes pydantic validation errors and GroundingError
            reason = "invalid_output"
            if repairs < 1:
                repairs += 1
                repair_note = str(exc)[:300]
                continue
            break

    if agent.fallback is not None:
        return AgentResult(agent.fallback(payload), "fallback", attempts, tokens_in, tokens_out,
                           round(cost, 6), reason)
    raise AgentFailed(reason, attempts, tokens_in, tokens_out, round(cost, 6))


# ---------- running a pipeline ----------

def _is_cancelled(run_id: uuid.UUID) -> bool:
    with SessionLocal() as session:  # its own session, so it is safe to call from worker threads
        return bool(session.scalar(select(AIRun.cancel_requested).where(AIRun.id == run_id)))


def _record(db: Session, run_id: uuid.UUID, ctx: dict, **fields) -> None:
    ctx["_position"] = ctx.get("_position", 0) + 1
    db.add(AIRunStep(run_id=run_id, position=ctx["_position"], **fields))


def _run_service(db: Session, run_id: uuid.UUID, ctx: dict, step: Step) -> None:
    started = time.monotonic()
    try:
        ctx[step.name] = step.run(ctx, db)
    except Exception as exc:
        db.rollback()
        code = exc.code if isinstance(exc, AppError) else "internal_error"
        _record(db, run_id, ctx, name=step.name, kind="service", status="failed",
                duration_ms=_ms(started), error_code=code)
        db.commit()
        raise
    _record(db, run_id, ctx, name=step.name, kind="service", status="succeeded", attempts=1,
            duration_ms=_ms(started))
    db.commit()


def _failure(error: Exception) -> tuple[str, str]:
    """(error code, step status) for a failed agent step."""
    if isinstance(error, Cancelled):
        return "cancelled", "cancelled"
    if isinstance(error, AgentFailed):
        return ("ai_unavailable" if error.reason == "provider_error" else "ai_invalid_output"), "failed"
    log.error("Unexpected failure in an agent step", exc_info=error)
    return "internal_error", "failed"


def _run_agents(db: Session, run_id: uuid.UUID, ctx: dict, steps: list[Step]) -> None:
    settings = get_settings()
    try:
        provider = get_provider()
    except ProviderError:
        raise RunFailed("ai_unavailable", "The AI service is not available right now.")
    jobs = [(step, step.payload(ctx)) for step in steps]

    def work(job):
        step, payload = job
        started = time.monotonic()
        try:
            result = run_agent(step.agent, payload, provider=provider, cancelled=lambda: _is_cancelled(run_id))
            return step, result, None, _ms(started)
        except Exception as exc:  # Cancelled, AgentFailed, or anything unexpected
            return step, None, exc, _ms(started)

    if len(jobs) == 1:
        outcomes = [work(jobs[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(len(jobs), settings.ai_max_parallel_steps)) as pool:
            outcomes = list(pool.map(work, jobs))

    first_error: Exception | None = None
    for step, result, error, duration in outcomes:
        if result is not None:
            ctx[step.name] = result.output
            ctx[f"{step.name}_source"] = result.source
            meta = {"source": result.source, "prompt_version": step.agent.prompt_version}
            if result.error:
                meta["fallback_reason"] = result.error
            _record(db, run_id, ctx, name=step.name, kind="agent", agent_name=step.agent.name,
                    status="succeeded", attempts=result.attempts, duration_ms=duration,
                    tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                    cost_usd=result.cost_usd, meta=meta)
        else:
            code, status = _failure(error)
            extra = {}
            if isinstance(error, AgentFailed):
                extra = {"attempts": error.attempts, "tokens_in": error.tokens_in,
                         "tokens_out": error.tokens_out, "cost_usd": error.cost_usd}
            _record(db, run_id, ctx, name=step.name, kind="agent", agent_name=step.agent.name,
                    status=status, duration_ms=duration, error_code=code, **extra)
            first_error = first_error or error
    db.commit()

    if first_error is not None:
        if isinstance(first_error, Cancelled):
            raise first_error
        code, _ = _failure(first_error)
        messages = {
            "ai_unavailable": "The AI service is unavailable right now. Please try again later.",
            "ai_invalid_output": "The AI service returned an answer that did not pass our checks. Please try again.",
        }
        raise RunFailed(code, messages.get(code, "Something went wrong. Please try again."))


def _execute(db: Session, run_id: uuid.UUID, pipeline: Pipeline, ctx: dict) -> None:
    settings = get_settings()
    started = time.monotonic()
    pending, done = list(pipeline.steps), set()
    while pending:
        if time.monotonic() - started > settings.ai_run_timeout_seconds:
            raise RunFailed("run_timeout", "The request took too long and was stopped.")
        if _is_cancelled(run_id):
            raise Cancelled()
        ready = [s for s in pending if all(d in done for d in s.depends_on)]
        if not ready:
            raise RuntimeError("Pipeline has a missing dependency or a cycle")
        for step in (s for s in ready if s.kind == "service"):
            _run_service(db, run_id, ctx, step)
        agent_steps = [s for s in ready if s.kind == "agent"]
        if agent_steps:
            _run_agents(db, run_id, ctx, agent_steps)  # independent agent steps run in parallel
        done |= {s.name for s in ready}
        pending = [s for s in pending if s not in ready]


def _finish(db: Session, run_id: uuid.UUID, *, status, output, error_code, error_message, approval) -> None:
    run = db.get(AIRun, run_id)
    steps = db.scalars(select(AIRunStep).where(AIRunStep.run_id == run_id)).all()
    run.tokens_in = sum(s.tokens_in for s in steps)
    run.tokens_out = sum(s.tokens_out for s in steps)
    run.cost_usd = round(sum(s.cost_usd for s in steps), 6)
    run.status = status
    run.output = output
    run.error_code = error_code
    run.error = error_message
    run.requires_approval = approval and status == "succeeded"
    run.finished_at = _now()
    db.commit()


def execute_run(run_id: uuid.UUID, payload: dict) -> None:
    """Runs one queued run to the end. Called as a background task; never raises."""
    from app.modules.ai.pipelines import PIPELINES  # imported here to avoid a circular import

    with SessionLocal() as db:
        run = db.get(AIRun, run_id)
        if run is None or run.status != "queued":
            return  # cancelled before it started
        pipeline = PIPELINES.get(run.task_type)
        run.status = "running"
        run.started_at = _now()
        db.commit()

        status, output, code, message = "failed", None, None, None
        try:
            if pipeline is None:
                raise RunFailed("unknown_task", "This kind of AI task is not available.")
            ctx = {"user": db.get(User, run.user_id), "input": payload}
            _execute(db, run_id, pipeline, ctx)
            output, status = pipeline.result(ctx), "succeeded"
        except Cancelled:
            status = "cancelled"
        except RunFailed as exc:
            code, message = exc.code, exc.message
        except AppError as exc:
            code, message = exc.code, exc.message
        except Exception:
            log.exception("AI run %s failed unexpectedly", run_id)
            code, message = "internal_error", "Something went wrong. Please try again."

        db.rollback()  # drop anything half-done; completed steps were already committed
        _finish(db, run_id, status=status, output=output, error_code=code, error_message=message,
                approval=bool(pipeline and pipeline.requires_approval))