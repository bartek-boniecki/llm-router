"""
Queue Worker

- Pulls jobs from RabbitMQ (local/dev) or SQS (placeholder)
- Routes to cheapest viable LLM
- Records costs + status in Postgres
"""

import asyncio
import json
import os
import signal
import time
from typing import Any, Dict

import aio_pika
from aio_pika import ExchangeType
from aio_pika.abc import AbstractIncomingMessage

from app.config import settings
from app.logging_setup import configure_logging, get_logger
from app.models import (
    init_db,
    create_session_maker,
    JobStatus,
    record_event,
    record_cost,
    get_job_by_id,
)
from app.routing_policy import RoutingPolicy
from app.adapters import LLMAdapterRegistry
from app.utils import maybe_redact_pii

configure_logging()
log = get_logger()

EXCHANGE_NAME = "router_exchange"
QUEUE_NAME = "router_jobs"
ROUTING_KEY = "router.jobs"

_shutdown = asyncio.Event()

async def _sleep_backoff(attempt: int, base: float = 1.0, cap: float = 15.0):
    delay = min(cap, base * (2 ** (attempt - 1)))
    await asyncio.sleep(delay)

async def _connect_rabbitmq_with_retry(url: str, max_attempts: int = 0) -> aio_pika.RobustConnection:
    attempt = 0
    while True:
        attempt += 1
        try:
            log.info("rabbitmq_connect_attempt", url=url, attempt=attempt)
            conn = await aio_pika.connect_robust(url)
            log.info("rabbitmq_connected")
            return conn
        except Exception as e:
            log.warning("rabbitmq_connect_failed", attempt=attempt, err=str(e))
            if max_attempts and attempt >= max_attempts:
                raise
            await _sleep_backoff(attempt)

async def _declare_topology(channel: aio_pika.abc.AbstractChannel):
    exchange = await channel.declare_exchange(EXCHANGE_NAME, ExchangeType.DIRECT, durable=True)
    queue = await channel.declare_queue(QUEUE_NAME, durable=True)
    await queue.bind(exchange, ROUTING_KEY)
    return exchange, queue

async def _handle_message(message: AbstractIncomingMessage):
    async with message.process(requeue=True):
        payload = json.loads(message.body.decode("utf-8"))
        job_id = payload.get("job_id")
        body: Dict[str, Any] = payload.get("body", {})

        log.info("worker_received", job_id=job_id)

        # Mark job as running
        SessionLocal = await create_session_maker()
        async with SessionLocal() as session:
            job = await get_job_by_id(session, job_id)
            if not job:
                log.warning("job_missing_in_db", job_id=job_id)
                return
            job.status = JobStatus.running
            await session.commit()

        # Prepare prompt (with optional PII redaction)
        prompt = body.get("prompt", "")
        redact = bool(body.get("redact_pii")) and settings.PII_REDACTION_ENABLED
        if redact:
            prompt = maybe_redact_pii(prompt)

        # Choose a model
        policy = RoutingPolicy()
        start = time.perf_counter()
        decision = await policy.choose_model(
            input_text=prompt,
            expected_output_tokens=int(body.get("expected_output_tokens", 512)),
            quality_floor=int(body.get("quality_floor", 1)),
            cost_ceiling_usd=float(body.get("cost_ceiling_usd", 0.1)),
            provider_hints=body.get("provider_hints") or {},
        )

        # Call the provider
        adapter = LLMAdapterRegistry.get(decision.provider)
        output_text, token_stats = await adapter.complete(
            model=decision.model,
            prompt=prompt,
            max_tokens=int(body.get("expected_output_tokens", 512)),
            temperature=decision.temperature,
            system_prompt=decision.system_prompt,
            timeout_s=settings.LLM_TIMEOUT_S,
        )

        # Record cost + status
        latency_ms = int((time.perf_counter() - start) * 1000)
        SessionLocal = await create_session_maker()
        async with SessionLocal() as session:
            db_job = await get_job_by_id(session, job_id)
            if db_job:
                db_job.status = JobStatus.succeeded
                await record_cost(
                    session=session,
                    job_id=db_job.job_id,
                    provider=decision.provider,
                    model=decision.model,
                    tokens_in=token_stats.tokens_in,
                    tokens_out=token_stats.tokens_out,
                    cost_usd=decision.estimated_cost_usd,
                    latency_ms=latency_ms,
                )
                await record_event(
                    session=session,
                    job_id=db_job.job_id,
                    level="info",
                    message=f"Routed to {decision.provider}:{decision.model}",
                )
                await session.commit()

        # (Optional) you could write output_text to S3/MinIO here

async def _run_worker_rabbit():
    # Use settings.RABBITMQ_URL consistently (available via .env or k8s Secret)
    url = settings.RABBITMQ_URL or os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    conn = await _connect_rabbitmq_with_retry(url)
    async with conn:
        channel = await conn.channel()
        await channel.set_qos(prefetch_count=16)
        _, queue = await _declare_topology(channel)
        await queue.consume(_handle_message, no_ack=False)
        log.info("worker_started_rabbit", queue=QUEUE_NAME)
        await _shutdown.wait()

async def _run_worker_sqs():
    log.info("worker_sqs_not_configured")
    await _shutdown.wait()

async def run_worker():
    await init_db()
    mode = (settings.QUEUE_MODE or "rabbitmq").lower()
    if mode == "sqs":
        await _run_worker_sqs()
    else:
        await _run_worker_rabbit()

def _handle_signals():
    loop = asyncio.get_event_loop()
    def _signal_handler():
        log.info("worker_shutdown_signal")
        _shutdown.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _signal_handler)
        except NotImplementedError:
            pass

if __name__ == "__main__":
    _handle_signals()
    asyncio.run(run_worker())
