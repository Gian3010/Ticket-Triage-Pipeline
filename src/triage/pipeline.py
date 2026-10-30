"""Orquestração: ler CSVs da caixa de entrada -> mascarar PII -> classificar -> gravar -> arquivar."""
from __future__ import annotations

import csv
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy.engine import Engine

from . import db
from .classifier import SLA_HOURS, TicketClassifier, anthropic_llm_fn
from .config import Settings
from .masking import customer_ref, mask_pii

log = logging.getLogger("triage")
REQUIRED = {"ticket_id", "received_at", "customer_email", "subject", "body"}


@dataclass
class RunStats:
    tickets_read: int = 0
    tickets_new: int = 0
    by_llm: int = 0
    by_rules: int = 0
    by_fallback: int = 0
    needs_review: int = 0
    invalid_rows: int = 0
    duration_s: float = 0.0


def read_csv_rows(path: Path) -> Iterator[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: colunas ausentes {sorted(missing)}")
        yield from reader


def _build_classifier(settings: Settings) -> TicketClassifier:
    if settings.use_llm():
        log.info("modo LLM (modelo=%s)", settings.anthropic_model)
        return TicketClassifier(llm_fn=anthropic_llm_fn(settings.anthropic_model))
    log.info("modo regras (sem credenciais de LLM ou TRIAGE_MODE=rules)")
    return TicketClassifier(llm_fn=None)


def process_rows(raw_rows: list[dict], engine: Engine, settings: Settings,
                 classifier: Optional[TicketClassifier] = None) -> RunStats:
    """Processa uma lista de linhas de ticket e grava as novas. Idempotente."""
    t0 = time.perf_counter()
    started = db.utcnow()
    classifier = classifier or _build_classifier(settings)
    stats = RunStats(tickets_read=len(raw_rows))

    # 1) validação + deduplicação (dentro do lote e contra o banco)
    valid: dict[str, dict] = {}
    for r in raw_rows:
        try:
            if not (r.get("ticket_id") and r.get("body", "").strip()):
                raise ValueError("ticket sem id ou corpo")
            datetime.fromisoformat(r["received_at"])
        except (ValueError, TypeError, KeyError):
            stats.invalid_rows += 1
            continue
        valid.setdefault(r["ticket_id"], r)
    already = db.existing_ids(engine, valid)
    todo = [r for tid, r in valid.items() if tid not in already]
    stats.tickets_new = len(todo)

    # 2) mascaramento ANTES de qualquer LLM ou escrita
    masked = [(r, mask_pii(r.get("subject", "")), mask_pii(r["body"])) for r in todo]

    # 3) classificação concorrente
    def work(item: tuple[dict, str, str]):
        return classifier.classify(item[1], item[2])

    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        results = list(pool.map(work, masked))

    # 4) montagem das linhas e gravação transacional
    rows = []
    for (r, subj, body), res in zip(masked, results):
        received = datetime.fromisoformat(r["received_at"])
        review = int(res.confidence < settings.review_threshold or res.classified_by == "rules_fallback")
        stats.needs_review += review
        stats.by_llm += res.classified_by == "llm"
        stats.by_rules += res.classified_by == "rules"
        stats.by_fallback += res.classified_by == "rules_fallback"
        rows.append({
            "ticket_id": r["ticket_id"], "received_at": received, "channel": r.get("channel"),
            "customer_ref": customer_ref(r["customer_email"]), "subject": subj, "body_masked": body,
            "category": res.category, "priority": res.priority, "sentiment": res.sentiment,
            "summary": res.summary, "team": res.team, "confidence": res.confidence,
            "classified_by": res.classified_by, "needs_review": review,
            "sla_due_at": received + timedelta(hours=SLA_HOURS[res.priority]), "status": "open",
            "processed_at": db.utcnow(),
        })
    db.insert_tickets(engine, rows)

    stats.duration_s = time.perf_counter() - t0
    db.record_run(
        engine, started_at=started, finished_at=db.utcnow(), tickets_read=stats.tickets_read,
        tickets_new=stats.tickets_new, by_llm=stats.by_llm, by_rules=stats.by_rules,
        by_fallback=stats.by_fallback, needs_review=stats.needs_review, duration_s=stats.duration_s,
    )
    log.info("lidos=%d novos=%d llm=%d regras=%d fallback=%d revisão=%d inválidos=%d em %.2fs",
             stats.tickets_read, stats.tickets_new, stats.by_llm, stats.by_rules, stats.by_fallback,
             stats.needs_review, stats.invalid_rows, stats.duration_s)
    return stats


def run_inbox(settings: Optional[Settings] = None) -> RunStats:
    """Processa todos os CSVs da caixa de entrada e move os bem-sucedidos para ``processed``."""
    settings = settings or Settings()
    engine = db.get_engine(settings.database_url)
    db.init_db(engine)
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)

    total = RunStats()
    classifier = _build_classifier(settings)
    for path in sorted(settings.inbox_dir.glob("*.csv")):
        try:
            rows = list(read_csv_rows(path))
            s = process_rows(rows, engine, settings, classifier)
        except Exception:  # noqa: BLE001 - um arquivo ruim não derruba o lote
            log.exception("falha ao processar %s; arquivo mantido na caixa de entrada", path.name)
            continue
        shutil.move(str(path), settings.processed_dir / path.name)
        for f in ("tickets_read", "tickets_new", "by_llm", "by_rules", "by_fallback", "needs_review",
                  "invalid_rows"):
            setattr(total, f, getattr(total, f) + getattr(s, f))
        total.duration_s += s.duration_s
    return total
