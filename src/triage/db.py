"""Camada de persistência (SQLAlchemy Core) — PostgreSQL em produção, SQLite em dev/testes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import (Column, DateTime, Float, Index, Integer, MetaData, String, Table, Text,
                        create_engine, func, select)
from sqlalchemy.engine import Engine

metadata = MetaData()


def utcnow() -> datetime:
    """UTC 'naive' (sem tzinfo), compatível com colunas TIMESTAMP sem fuso."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

tickets = Table(
    "tickets", metadata,
    Column("ticket_id", String(64), primary_key=True),
    Column("received_at", DateTime, nullable=False),
    Column("channel", String(40)),
    Column("customer_ref", String(32), nullable=False),  # hash do e-mail (sem PII)
    Column("subject", Text),
    Column("body_masked", Text),
    Column("category", String(40), nullable=False),
    Column("priority", String(10), nullable=False),
    Column("sentiment", String(10), nullable=False),
    Column("summary", Text),
    Column("team", String(40), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("classified_by", String(20), nullable=False),
    Column("needs_review", Integer, nullable=False, default=0),
    Column("sla_due_at", DateTime, nullable=False),
    Column("status", String(20), nullable=False, default="open"),
    Column("processed_at", DateTime, nullable=False, default=utcnow),
)
Index("ix_tickets_received", tickets.c.received_at)
Index("ix_tickets_category_priority", tickets.c.category, tickets.c.priority)

pipeline_runs = Table(
    "pipeline_runs", metadata,
    Column("run_id", String(36), primary_key=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=False),
    Column("tickets_read", Integer, nullable=False),
    Column("tickets_new", Integer, nullable=False),
    Column("by_llm", Integer, nullable=False),
    Column("by_rules", Integer, nullable=False),
    Column("by_fallback", Integer, nullable=False),
    Column("needs_review", Integer, nullable=False),
    Column("duration_s", Float, nullable=False),
)


def get_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        from pathlib import Path

        Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)  # idempotente (checkfirst)


def existing_ids(engine: Engine, ids: Iterable[str]) -> set[str]:
    ids = list(ids)
    found: set[str] = set()
    with engine.connect() as conn:
        for i in range(0, len(ids), 500):  # evita limite de parâmetros do SQLite
            chunk = ids[i:i + 500]
            found.update(r[0] for r in conn.execute(select(tickets.c.ticket_id).where(tickets.c.ticket_id.in_(chunk))))
    return found


def _insert_stmt(engine: Engine):
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def insert_tickets(engine: Engine, rows: list[dict]) -> None:
    """Upsert idempotente: reprocessar o mesmo arquivo não duplica registros."""
    if not rows:
        return
    stmt = _insert_stmt(engine)(tickets).on_conflict_do_nothing(index_elements=["ticket_id"])
    with engine.begin() as conn:  # transação única: tudo ou nada
        for i in range(0, len(rows), 500):
            conn.execute(stmt, rows[i:i + 500])


def record_run(engine: Engine, **fields) -> str:
    run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(pipeline_runs.insert().values(run_id=run_id, **fields))
    return run_id


def count_tickets(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(tickets)).scalar_one()
