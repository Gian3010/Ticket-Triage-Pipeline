-- Consultas analíticas (PostgreSQL) para dashboards (Metabase/Grafana/Superset).
-- Cada bloco é independente: cole no editor SQL da ferramenta de BI.

-- Q1 · Volume diário por categoria
SELECT date_trunc('day', received_at)::date AS dia,
       category,
       count(*)                              AS tickets
FROM tickets
GROUP BY 1, 2
ORDER BY 1, 2;

-- Q2 · Backlog crítico: tickets abertos de prioridade alta/crítica ordenados pelo SLA mais próximo
--      (usa "agora" fixo na consulta; em produção troque por now())
SELECT ticket_id, team, priority, category, summary, sla_due_at,
       round(extract(epoch FROM (sla_due_at - now() AT TIME ZONE 'utc')) / 3600.0, 1) AS horas_ate_sla
FROM tickets
WHERE status = 'open' AND priority IN ('critica', 'alta')
ORDER BY sla_due_at
LIMIT 50;

-- Q3 · Carga por time e prioridade (para dimensionar escala de atendimento)
SELECT team,
       count(*)                                         AS tickets,
       count(*) FILTER (WHERE priority = 'critica')     AS criticos,
       count(*) FILTER (WHERE priority = 'alta')        AS altos,
       round(100.0 * count(*) FILTER (WHERE sentiment = 'negativo') / count(*), 1) AS pct_negativo
FROM tickets
GROUP BY team
ORDER BY tickets DESC;

-- Q4 · Saúde do classificador: origem da classificação e fila de revisão humana
SELECT classified_by,
       count(*)                                   AS tickets,
       round(avg(confidence)::numeric, 3)         AS confianca_media,
       sum(needs_review)                          AS para_revisar,
       round(100.0 * sum(needs_review) / count(*), 1) AS pct_revisar
FROM tickets
GROUP BY classified_by;

-- Q5 · Clientes reincidentes (3+ tickets) — sinal de churn, sem expor PII (usa customer_ref)
SELECT customer_ref,
       count(*)                          AS tickets,
       count(DISTINCT category)          AS categorias,
       min(received_at)                  AS primeiro,
       max(received_at)                  AS ultimo
FROM tickets
GROUP BY customer_ref
HAVING count(*) >= 3
ORDER BY tickets DESC
LIMIT 50;

-- Q6 · Horário de pico de entrada de tickets (hora do dia x dia da semana)
SELECT extract(isodow FROM received_at)::int AS dia_semana,
       extract(hour FROM received_at)::int   AS hora,
       count(*)                              AS tickets
FROM tickets
GROUP BY 1, 2
ORDER BY 1, 2;

-- Q7 · Execuções do pipeline: throughput e custo operacional
SELECT date_trunc('day', started_at)::date AS dia,
       sum(tickets_new)                    AS tickets_processados,
       round(sum(duration_s)::numeric, 2)  AS segundos_totais,
       round((sum(duration_s) / NULLIF(sum(tickets_new), 0) * 1000)::numeric, 2) AS ms_por_ticket
FROM pipeline_runs
GROUP BY 1
ORDER BY 1 DESC;

-- Q8 · Horas economizadas por semana (premissas do cenário: 45 s manual, 20 s por revisão)
WITH semana AS (
    SELECT date_trunc('week', received_at)::date AS semana,
           count(*)                              AS tickets,
           sum(needs_review)                     AS revisoes
    FROM tickets
    GROUP BY 1
)
SELECT semana, tickets,
       round(tickets * 45 / 3600.0, 1)                                   AS horas_manual,
       round(revisoes * 20 / 3600.0, 1)                                  AS horas_com_pipeline,
       round((tickets * 45 - revisoes * 20) / 3600.0, 1)                 AS horas_economizadas
FROM semana
ORDER BY semana;
