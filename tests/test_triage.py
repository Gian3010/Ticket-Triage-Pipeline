import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from triage import db  # noqa: E402
from triage.classifier import (CATEGORIES, TicketClassifier, classify_rules, parse_llm_output)  # noqa: E402
from triage.config import Settings  # noqa: E402
from triage.masking import customer_ref, mask_pii  # noqa: E402
from triage.pipeline import process_rows, read_csv_rows, run_inbox  # noqa: E402
from triage.roi import compute_roi  # noqa: E402
from triage.synthetic import generate_tickets, write_csv  # noqa: E402


@pytest.fixture()
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path}/t.db", inbox_dir=tmp_path / "in",
                    processed_dir=tmp_path / "done", mode="rules", workers=4)


@pytest.fixture()
def engine(settings):
    e = db.get_engine(settings.database_url)
    db.init_db(e)
    return e


# ---------- mascaramento ----------
def test_mask_pii_removes_cpf_cnpj_email_phone():
    t = ("Meu CPF 123.456.789-09, CNPJ 12.345.678/0001-95, email joao@exemplo.com.br, "
         "tel (11) 98765-4321 e pedido #1234567 código TL4829104756.")
    m = mask_pii(t)
    for s in ("123.456.789-09", "12.345.678/0001-95", "joao@exemplo", "98765-4321"):
        assert s not in m
    assert "[CPF]" in m and "[CNPJ]" in m and "[EMAIL]" in m and "[TELEFONE]" in m
    assert "#1234567" in m and "TL4829104756" in m  # não mascara identificadores de negócio


def test_customer_ref_stable_and_not_reversible():
    assert customer_ref("A@x.com ") == customer_ref("a@x.com")
    assert "@" not in customer_ref("a@x.com")


# ---------- regras ----------
def test_rules_critical_for_medicine_damage():
    r = classify_rules("URGENTE", "Os medicamentos chegaram com a caixa violada, vou acionar o Procon")
    assert r.category == "extravio_avaria" and r.priority == "critica" and r.team == "Sinistros"


def test_rules_praise_is_never_high_priority():
    r = classify_rules("Parabéns", "Atendimento excelente, recomendo, entrega urgente resolvida hoje")
    assert r.category == "elogio" and r.priority == "baixa" and r.sentiment == "positivo"


def test_rules_unknown_goes_to_outros_with_low_confidence():
    r = classify_rules("Oi", "bom dia")
    assert r.category == "outros" and r.confidence < 0.6


def test_rules_accuracy_on_synthetic_gold_is_reasonable():
    rows = generate_tickets(1500, seed=11)
    acc = sum(classify_rules(r["subject"], r["body"]).category == r["gold_category"] for r in rows) / len(rows)
    assert acc > 0.85


# ---------- LLM com validação/fallback ----------
GOOD = json.dumps({"category": "atraso_entrega", "priority": "alta", "sentiment": "negativo",
                   "summary": "Cliente reclama de atraso", "confidence": 0.93})


def test_llm_valid_output_used():
    c = TicketClassifier(llm_fn=lambda s, u: f"```json\n{GOOD}\n```", backoff_s=0)
    r = c.classify("x", "y")
    assert r.classified_by == "llm" and r.category == "atraso_entrega" and r.confidence == 0.93


@pytest.mark.parametrize("bad", ["não sei", "{}", '{"category": "inventada", "priority": "alta", '
                                 '"sentiment": "neutro", "summary": "x", "confidence": 0.5}',
                                 '{"category": "elogio", "priority": "alta", "sentiment": "neutro", '
                                 '"summary": "x", "confidence": 7}'])
def test_llm_invalid_output_falls_back_to_rules(bad):
    r = TicketClassifier(llm_fn=lambda s, u: bad, backoff_s=0).classify("Pedido atrasado", "ainda não recebi, atraso")
    assert r.classified_by == "rules_fallback" and r.category in CATEGORIES


def test_llm_exception_retries_then_falls_back():
    calls = {"n": 0}

    def boom(s, u):
        calls["n"] += 1
        raise TimeoutError("api fora")

    r = TicketClassifier(llm_fn=boom, retries=3, backoff_s=0).classify("a", "cancelar pedido")
    assert calls["n"] == 3 and r.classified_by == "rules_fallback"


def test_llm_transient_error_recovers():
    state = {"n": 0}

    def flaky(s, u):
        state["n"] += 1
        if state["n"] < 2:
            raise ConnectionError("blip")
        return GOOD

    assert TicketClassifier(llm_fn=flaky, backoff_s=0).classify("a", "b").classified_by == "llm"


def test_parse_llm_output_rejects_garbage():
    with pytest.raises(ValueError):
        parse_llm_output("sem json")


# ---------- pipeline ----------
def test_pipeline_end_to_end_and_idempotent(settings, engine):
    rows = generate_tickets(300, seed=3)
    s1 = process_rows(rows, engine, settings)
    assert s1.tickets_new == 300 and db.count_tickets(engine) == 300
    s2 = process_rows(rows, engine, settings)  # reprocessar o mesmo lote
    assert s2.tickets_new == 0 and db.count_tickets(engine) == 300


def test_pipeline_stores_no_raw_pii(settings, engine):
    rows = generate_tickets(400, seed=4)
    process_rows(rows, engine, settings)
    with engine.connect() as conn:
        bodies = [r[0] for r in conn.execute(db.tickets.select().with_only_columns(db.tickets.c.body_masked))]
        refs = [r[0] for r in conn.execute(db.tickets.select().with_only_columns(db.tickets.c.customer_ref))]
    joined = " ".join(bodies)
    assert "@exemplo.com.br" not in joined and "CPF é 1" not in joined
    assert all("@" not in x for x in refs)


def test_pipeline_skips_invalid_rows_and_duplicates(settings, engine):
    good = generate_tickets(5, seed=5)
    bad = [{**good[0], "ticket_id": "", }, {**good[1], "received_at": "ontem"}, {**good[2], "body": "  "}]
    s = process_rows(good + bad + [good[3]], engine, settings)  # + duplicata no lote
    assert s.invalid_rows == 3 and s.tickets_new == 5 and db.count_tickets(engine) == 5


def test_sla_depends_on_priority(settings, engine):
    process_rows(generate_tickets(200, seed=6), engine, settings)
    with engine.connect() as conn:
        rows = conn.execute(db.tickets.select()).mappings().all()
    for r in rows:
        hours = (r["sla_due_at"] - r["received_at"]).total_seconds() / 3600
        assert hours == {"critica": 2, "alta": 8, "media": 24, "baixa": 72}[r["priority"]]


def test_run_inbox_moves_files_and_keeps_bad_ones(settings):
    write_csv(generate_tickets(50, seed=8), settings.inbox_dir / "ok.csv")
    (settings.inbox_dir / "ruim.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    s = run_inbox(settings)
    assert s.tickets_new == 50
    assert (settings.processed_dir / "ok.csv").exists()
    assert (settings.inbox_dir / "ruim.csv").exists()  # mantido para análise


def test_read_csv_requires_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("ticket_id,body\n1,a\n", encoding="utf-8")
    with pytest.raises(ValueError):
        list(read_csv_rows(p))


def test_roi_uses_measured_data(settings, engine):
    assert compute_roi(engine, settings) is None
    process_rows(generate_tickets(500, seed=9), engine, settings)
    rep = compute_roi(engine, settings)
    assert rep.tickets_measured == 500 and 0 <= rep.review_share <= 1
    assert rep.saved_hours_week < rep.manual_hours_week and rep.saved_pct > 0.5
