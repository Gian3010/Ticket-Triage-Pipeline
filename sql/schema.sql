-- Esquema PostgreSQL equivalente ao definido em src/triage/db.py.
-- O pipeline também cria as tabelas sozinho (create_all); este arquivo serve para
-- provisionamento via docker-compose (initdb) e como documentação do modelo de dados.

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id      VARCHAR(64)  PRIMARY KEY,
    received_at    TIMESTAMP    NOT NULL,
    channel        VARCHAR(40),
    customer_ref   VARCHAR(32)  NOT NULL,          -- hash do e-mail (sem PII)
    subject        TEXT,
    body_masked    TEXT,                           -- corpo com CPF/CNPJ/e-mail/telefone mascarados
    category       VARCHAR(40)  NOT NULL,
    priority       VARCHAR(10)  NOT NULL CHECK (priority IN ('baixa','media','alta','critica')),
    sentiment      VARCHAR(10)  NOT NULL CHECK (sentiment IN ('negativo','neutro','positivo')),
    summary        TEXT,
    team           VARCHAR(40)  NOT NULL,
    confidence     DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    classified_by  VARCHAR(20)  NOT NULL CHECK (classified_by IN ('llm','rules','rules_fallback')),
    needs_review   INTEGER      NOT NULL DEFAULT 0,
    sla_due_at     TIMESTAMP    NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'open',
    processed_at   TIMESTAMP    NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_tickets_received          ON tickets (received_at);
CREATE INDEX IF NOT EXISTS ix_tickets_category_priority ON tickets (category, priority);
CREATE INDEX IF NOT EXISTS ix_tickets_open_sla          ON tickets (sla_due_at) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS ix_tickets_customer          ON tickets (customer_ref);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id        VARCHAR(36) PRIMARY KEY,
    started_at    TIMESTAMP        NOT NULL,
    finished_at   TIMESTAMP        NOT NULL,
    tickets_read  INTEGER          NOT NULL,
    tickets_new   INTEGER          NOT NULL,
    by_llm        INTEGER          NOT NULL,
    by_rules      INTEGER          NOT NULL,
    by_fallback   INTEGER          NOT NULL,
    needs_review  INTEGER          NOT NULL,
    duration_s    DOUBLE PRECISION NOT NULL
);
