# 🎫 Ticket Triage Pipeline — triagem automática de suporte com LLM + PostgreSQL

Pipeline em Python que lê tickets de suporte, **mascara dados pessoais (LGPD)**, classifica categoria/prioridade/sentimento com um LLM (com fallback determinístico), calcula SLA, grava no **PostgreSQL** e alimenta um dashboard.

> **Aviso de transparência.** A empresa (*Rápido Express*), os tickets, clientes e o volume semanal são **fictícios**. O cálculo de horas economizadas é uma **simulação**: ele combina *premissas declaradas* (volume e tempo manual) com *medições reais* feitas pelo próprio código (tempo de processamento e % de tickets que exigem revisão). `python -m triage roi` imprime cada linha rotulada como PREMISSA ou MEDIDO.

---

## 1. Situação-problema (cenário fictício)

A *Rápido Express*, transportadora e-commerce fictícia, recebe ~**1.300 tickets por semana** por e-mail, WhatsApp, formulário do site e Reclame Aqui. Dois analistas de N1 liam cada ticket, escolhiam categoria, prioridade e time responsável e copiavam para uma planilha — **cerca de 45 s por ticket ≈ 16 h/semana** (premissa do cenário).

Consequências (cenário):
- Tickets críticos (medicamento danificado, ameaça de Procon) esperavam na fila com os demais.
- Sem SLA calculado, ninguém sabia o que estava vencendo.
- Dados pessoais (CPF, telefone) circulavam em planilhas.

**Solução:** pipeline diário que classifica, prioriza, roteia ao time certo, calcula o SLA e deixa **só os casos de baixa confiança** para revisão humana.

## 2. Arquitetura

```
 CSVs (inbox) ─► validação + dedupe ─► mascaramento PII ─► classificação ─► PostgreSQL ─► Metabase
                  (ids únicos,           (CPF/CNPJ/e-mail/   LLM validado     tickets          dashboard
                   datas válidas)         telefone)           │  falhou?       pipeline_runs     (SQL em
                                                              ▼                                 analytics.sql)
                                                         regras (fallback)
```

Decisões de engenharia:

| Decisão | Motivo |
|---|---|
| Mascarar PII **antes** do LLM e do banco | LGPD: o provedor de LLM e o BI nunca veem CPF/telefone/e-mail |
| `customer_ref` = hash do e-mail | Permite contar reincidência sem armazenar o e-mail |
| Saída do LLM validada com Pydantic | Categoria fora do domínio ou JSON quebrado nunca chega ao banco |
| Retries com backoff + fallback por regras | Queda da API não para a operação; `classified_by` registra a origem |
| `INSERT … ON CONFLICT DO NOTHING` + filtro prévio | Reprocessar o mesmo arquivo é seguro (idempotente) |
| Gravação em transação única por lote | Tudo ou nada |
| Arquivo com erro permanece na inbox | Um CSV ruim não derruba o lote |
| `needs_review` por confiança < 0,6 ou fallback | Humano só onde a máquina duvida |
| SLA por prioridade (2 h / 8 h / 24 h / 72 h) | Fila de trabalho ordenada pelo vencimento |

Estrutura:

```
ticket-triage-pipeline/
├── src/triage/  config · masking · classifier · pipeline · db · roi · synthetic · __main__
├── sql/         schema.sql · analytics.sql (8 consultas de BI)
├── tests/       test_triage.py (21 testes)
├── Dockerfile · docker-compose.yml (Postgres + pipeline + Metabase)
└── .github/workflows/daily.yml (testes + execução agendada)
```

## 3. Como rodar

### 3.1 Rápido (SQLite, sem Docker, sem chave de API)

**Linux / macOS**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .                         # torna o pacote `triage` importável (resolve "No module named triage")
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1               # se bloquear: Set-ExecutionPolicy -Scope Process RemoteSigned
pip install -r requirements.txt
pip install -e .
```

Depois, em qualquer sistema:
```bash
python -m pytest -q                      # 21 testes
python -m triage generate --n 1300       # cria data/inbox/tickets_7.csv (sintético)
python -m triage run                     # classifica e grava em data/triage.db
python -m triage roi                     # ROI simulado
python -m triage eval                    # acurácia do classificador de regras vs gabarito
```

> Sem `pip install -e .`, defina `PYTHONPATH=src` (Linux/macOS: `export PYTHONPATH=src`; PowerShell: `$env:PYTHONPATH="src"`; CMD: `set PYTHONPATH=src`). O comando `export` **não existe** no Windows.

### 3.2 Com PostgreSQL + Metabase (Docker)

```bash
cp .env.example .env                       # defina POSTGRES_PASSWORD
docker compose up -d postgres metabase     # Postgres (já com o schema) + Metabase em :3000
# com `pip install -e .` feito; no PowerShell use: $env:DATABASE_URL="postgresql+psycopg2://triage:SUA_SENHA@localhost:5432/triage"
DATABASE_URL=postgresql+psycopg2://triage:SUA_SENHA@localhost:5432/triage \
  python -m triage generate --n 1300
docker compose run --rm pipeline           # processa a inbox dentro do container
```

### 3.3 Com LLM

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TRIAGE_MODEL=<id-do-modelo-vigente-na-sua-conta>
export TRIAGE_MODE=llm                     # 'auto' usa LLM se houver chave+modelo
python -m triage run
```

## 4. Tutorial: dashboard no Metabase (passo a passo)

1. Suba o stack: `docker compose up -d postgres metabase` e abra **http://localhost:3000**.
2. Na tela inicial, escolha o idioma e crie o usuário administrador (e-mail, nome e senha).
3. Em **Adicione seus dados**, clique em **PostgreSQL** e preencha: *Display name* `Triage`; *Host* `postgres`; *Port* `5432`; *Database name* `triage`; *Username* `triage`; *Password* o valor de `POSTGRES_PASSWORD`. Clique em **Save**.
4. Clique em **+ New → SQL query**, selecione o banco **Triage** e cole a consulta **Q1** de `sql/analytics.sql`. Execute (▶) e clique em **Visualization → Bar** (eixo X: `dia`, série: `category`, empilhado). **Save** com o nome *Volume diário por categoria*.
5. Repita para **Q2** (visualização **Table**, *Backlog crítico*), **Q3** (**Bar**, *Carga por time*), **Q4** (**Table**, *Saúde do classificador*) e **Q8** (**Line**, *Horas economizadas por semana*).
6. Clique em **+ New → Dashboard**, nomeie *Operação de Suporte* e, com o ícone de **lápis**, use **+ → Saved question** para adicionar as 5 perguntas. Reorganize arrastando e clique em **Save**.
7. Para atualizar sozinho, clique no **relógio (Auto-refresh)** do dashboard e escolha *5 minutes*.

> Se o Metabase estiver rodando no Docker e o Postgres no seu host (fora do compose), use `host.docker.internal` no passo 3.

## 5. Resultados medidos neste repositório

Execuções reais do código (SQLite e PostgreSQL 16 local), dados sintéticos:

| Métrica | Valor | Observação |
|---|---|---|
| Testes automatizados | **21 passando** | inclui idempotência, PII, fallback do LLM, SLA |
| Reprocessar o mesmo lote | **0 novos / 1.300 existentes** | idempotência verificada no PostgreSQL |
| Consultas de `analytics.sql` | **8/8 executam** sem erro no PostgreSQL 16 | |
| Acurácia do classificador de regras | **98,2 %** (2.000 tickets) | ⚠️ ver ressalva abaixo |
| Tickets enviados para revisão humana | **5,1 %** (66 de 1.300) | modo regras, dados sintéticos |
| Tempo de processamento (modo regras) | **~0,1 ms/ticket** | com LLM será dominado pela latência da API |

**Ressalva importante:** os tickets sintéticos são gerados a partir de modelos de frase, então as regras os acertam quase sempre. **Em tickets reais (gírias, erros de digitação, mensagens longas) a acurácia das regras cai** — é exatamente o espaço em que o LLM agrega valor. Não use os 98,2 % como estimativa de produção. Com `TRIAGE_MODE=llm`, meça a sua acurácia contra uma amostra rotulada à mão antes de confiar nos números.

## 6. Impacto estimado (simulação)

Saída de `python -m triage roi` com as premissas do cenário (1.300 tickets/semana, 45 s manual, 20 s por revisão) e os valores medidos acima:

| | Horas/semana |
|---|---|
| Triagem manual (premissa) | 16,2 |
| Revisão humana (5,1 % dos tickets) | 0,4 |
| Máquina | < 0,01 |
| **Economia estimada** | **≈ 15,9 h (≈ 98 %)** |

Esta é uma **estimativa condicional às premissas**. Em produção com LLM, a taxa de revisão provavelmente será maior e deve ser recalculada com dados reais.

## 7. Limitações e próximos passos

- A inbox é uma pasta de CSVs; em produção seria S3/SFTP, fila (SQS/PubSub) ou webhook do helpdesk.
- Não há deduplicação semântica (tickets repetidos com ids diferentes).
- Sem *prompt caching*, batch API ou classificação em lote por chamada: custo por ticket pode ser reduzido.
- O LLM não foi executado neste ambiente (sem chave): o caminho LLM é coberto por testes com um *fake* (saída válida, inválida, exceções, erro transitório), mas **a acurácia real do LLM não foi medida**.
- Dockerfile e `docker-compose.yml` seguem práticas padrão, porém **não foram executados** neste ambiente (sem daemon Docker); valide com `docker compose up`.
- Próximos: avaliação com `llm-prompt-lab`, monitoramento de drift de categorias, e fila de revisão com feedback que realimenta few-shot.
