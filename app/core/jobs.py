"""A tiny job registry.

Jobs are plain functions with no arguments that return a small report (a dict).
Today `enqueue` runs them inline. To move to Celery later, only `enqueue` changes.
"""
from collections.abc import Callable

from app.core.config import get_settings
from app.core.errors import AppError

_REGISTRY: dict[str, Callable[[], dict]] = {}


def job(name: str):
    def register(fn: Callable[[], dict]) -> Callable[[], dict]:
        _REGISTRY[name] = fn
        return fn

    return register


def job_names() -> list[str]:
    return sorted(_REGISTRY)


def run_job(name: str) -> dict:
    fn = _REGISTRY.get(name)
    if fn is None:
        raise AppError(404, "unknown_job", f"Unknown job '{name}'", job_names())
    return fn()


def enqueue(name: str) -> dict:
    backend = get_settings().job_backend
    if backend == "inline":
        return run_job(name)
    raise RuntimeError(f"Unsupported JOB_BACKEND '{backend}'")