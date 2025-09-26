"""
app/queue_client.py
Small helper for publishing job messages to RabbitMQ with retries.
"""

import json
import asyncio
from typing import Dict, Any

import aio_pika
from aio_pika import DeliveryMode, ExchangeType

from app.config import settings

EXCHANGE_NAME = "router_exchange"
QUEUE_NAME = "router_jobs"
ROUTING_KEY = "router.jobs"

async def _sleep_backoff(attempt: int, base: float = 1.0, cap: float = 10.0):
    delay = min(cap, base * (2 ** (attempt - 1)))
    await asyncio.sleep(delay)

async def _connect_with_retry(url: str, max_attempts: int = 10) -> aio_pika.RobustConnection:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await aio_pika.connect_robust(url)
        except Exception:
            if attempt >= max_attempts:
                raise
            await _sleep_backoff(attempt)

async def enqueue_job_message(job_id: str, body: Dict[str, Any]) -> None:
    """
    Publish message: {"job_id": ..., "body": {...}} to the router queue.
    """
    url = settings.RABBITMQ_URL or "amqp://guest:guest@rabbitmq:5672/"
    connection = await _connect_with_retry(url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        # Idempotent: declaring again is safe
        exchange = await channel.declare_exchange(EXCHANGE_NAME, ExchangeType.DIRECT, durable=True)
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        await queue.bind(exchange, ROUTING_KEY)

        message = {"job_id": job_id, "body": body}
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode("utf-8"),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=ROUTING_KEY,
        )
