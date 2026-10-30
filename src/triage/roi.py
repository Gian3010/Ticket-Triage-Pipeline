"""Cálculo de ROI **simulado**.

Separa explicitamente o que é MEDIDO (lido do banco: tempo de processamento, % de tickets que
exigem revisão humana) do que é PREMISSA do cenário fictício (volume semanal e tempo manual).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from . import db
from .config import Settings


@dataclass
class RoiReport:
    manual_hours_week: float
    review_hours_week: float
    compute_hours_week: float
    saved_hours_week: float
    saved_pct: float
    review_share: float
    seconds_per_ticket: float
    tickets_measured: int


def compute_roi(engine: Engine, settings: Settings) -> RoiReport | None:
    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(db.tickets)).scalar_one()
        if n == 0:
            return None
        review = conn.execute(select(func.sum(db.tickets.c.needs_review))).scalar_one() or 0
        row = conn.execute(select(func.sum(db.pipeline_runs.c.duration_s),
                                  func.sum(db.pipeline_runs.c.tickets_new))).one()
    total_s, runs_tickets = row[0] or 0.0, row[1] or 0
    sec_per_ticket = (total_s / runs_tickets) if runs_tickets else 0.0
    review_share = review / n

    weekly = settings.tickets_per_week
    manual_h = weekly * settings.manual_seconds / 3600
    review_h = weekly * review_share * settings.review_seconds / 3600
    compute_h = weekly * sec_per_ticket / 3600
    saved = manual_h - review_h - compute_h
    return RoiReport(manual_h, review_h, compute_h, saved, saved / manual_h if manual_h else 0.0,
                     review_share, sec_per_ticket, n)


def format_report(r: RoiReport, s: Settings) -> str:
    return (
        "ROI SIMULADO (premissas fictícias + medições reais do banco)\n"
        "-------------------------------------------------------------\n"
        f"PREMISSA  tickets/semana ........ {s.tickets_per_week}\n"
        f"PREMISSA  triagem manual ........ {s.manual_seconds:.0f} s/ticket -> {r.manual_hours_week:.1f} h/semana\n"
        f"PREMISSA  revisão humana ........ {s.review_seconds:.0f} s/ticket revisado\n"
        f"MEDIDO    tickets analisados .... {r.tickets_measured}\n"
        f"MEDIDO    % exige revisão ....... {r.review_share:.1%}\n"
        f"MEDIDO    processamento ......... {r.seconds_per_ticket * 1000:.1f} ms/ticket\n"
        "-------------------------------------------------------------\n"
        f"Horas com o pipeline: revisão {r.review_hours_week:.2f} h + máquina {r.compute_hours_week:.3f} h\n"
        f"Horas economizadas/semana: {r.saved_hours_week:.1f} h ({r.saved_pct:.0%})\n"
    )
