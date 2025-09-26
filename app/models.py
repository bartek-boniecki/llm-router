# app/models.py
"""
SQLAlchemy 2.x (async) models for:
- jobs
- job_artifacts
- job_costs
- events

BEGINNER NOTES:
- "Model" = Python class that maps to a database table.
- We use SQLAlchemy's async engine so the API can handle many requests concurrently.
- We auto-create tables at startup for simplicity (demo-friendly).
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, DateTime, Enum, ForeignKey, Text, select

from app.config import settings
from enum import Enum as PyEnum


class Base(DeclarativeBase):
    """Base class required by SQLAlchemy to define models."""
    pass


class JobStatus(PyEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    cost_ceiling_usd: Mapped[float] = mapped_column(Float, default=0.0)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.job_id"))
    artifact_uri: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))


class JobCost(Base):
    __tablename__ = "job_costs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.job_id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.job_id"))
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Async engine and session maker
_engine = create_async_engine(settings.DB_URL, echo=False)
_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db():
    """Create tables if they don't exist yet."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the shared sessionmaker (factory for DB sessions)."""
    return _session_maker


# --- Helper functions for common DB tasks ---

async def upsert_job_by_dedupe_key(session: AsyncSession, dedupe_key: str | None, defaults: Job) -> Job:
    """
    If 'dedupe_key' is provided:
      - Return the existing Job with that key (and update its updated_at), OR
      - Insert a new Job using 'defaults' if none exists.

    If no 'dedupe_key', always insert a new Job.
    """
    if not dedupe_key:
        session.add(defaults)
        await session.flush()
        return defaults

    # ORM select so we get Job objects (not raw rows/tuples).
    result = await session.execute(select(Job).where(Job.dedupe_key == dedupe_key))
    job: Job | None = result.scalar_one_or_none()

    if job:
        job.updated_at = datetime.utcnow()
        session.add(job)
        await session.flush()
        return job

    # Insert new if not found
    session.add(defaults)
    await session.flush()
    return defaults


async def get_job_by_id(session: AsyncSession, job_id: str) -> Job | None:
    """Fetch a Job by its primary key (job_id)."""
    return await session.get(Job, job_id)


async def record_event(session: AsyncSession, job_id: str, level: str, message: str):
    """Insert a debug/info event for a job."""
    session.add(Event(job_id=job_id, level=level, message=message))
    await session.flush()


async def record_cost(
    session: AsyncSession,
    job_id: str,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: int,
):
    """Insert a cost/usage record for a job."""
    session.add(
        JobCost(
            job_id=job_id,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
    )
    await session.flush()
