"""Classificação de tickets: LLM com validação estrita + fallback determinístico por regras.

Princípios de produção:
- A saída do LLM é validada (Pydantic); JSON inválido ou categoria fora do domínio => fallback.
- Falha de rede/API => retries com backoff exponencial e, por fim, fallback.
- Todo registro guarda `classified_by` ('llm', 'rules', 'rules_fallback') para auditoria e métricas.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

Category = Literal[
    "atraso_entrega", "extravio_avaria", "cobranca_fatura", "rastreamento",
    "alteracao_endereco", "cancelamento", "elogio", "outros",
]
Priority = Literal["baixa", "media", "alta", "critica"]
Sentiment = Literal["negativo", "neutro", "positivo"]

CATEGORIES: list[str] = list(Category.__args__)  # type: ignore[attr-defined]
PRIORITIES = ["baixa", "media", "alta", "critica"]
SLA_HOURS = {"critica": 2, "alta": 8, "media": 24, "baixa": 72}
TEAM_BY_CATEGORY = {
    "extravio_avaria": "Sinistros", "atraso_entrega": "Operações", "cobranca_fatura": "Financeiro",
    "rastreamento": "Atendimento N1", "alteracao_endereco": "Operações", "cancelamento": "Retenção",
    "elogio": "Qualidade", "outros": "Atendimento N1",
}


class Classification(BaseModel):
    category: Category
    priority: Priority
    sentiment: Sentiment
    summary: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)


@dataclass
class Result:
    category: str
    priority: str
    sentiment: str
    summary: str
    confidence: float
    team: str
    classified_by: str


def _norm(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.lower())


_KEYWORDS: dict[str, list[str]] = {
    "extravio_avaria": ["extravi", "avaria", "danific", "quebrad", "amassad", "roubo", "roubad",
                        "perdid", "violad", "faltando", "veio rasgad", "caixa aberta"],
    "atraso_entrega": ["atras", "nao chegou", "ainda nao recebi", "demor", "nao foi entregue",
                       "previsao de entrega", "prazo vencido", "passou do prazo"],
    "cobranca_fatura": ["cobranc", "fatura", "boleto", "nota fiscal", "cobrado a mais", "2a via",
                        "segunda via", "estorno", "valor errado", "cobrado duas"],
    "rastreamento": ["rastre", "onde esta", "localizar", "sem atualizacao", "status do pedido",
                     "codigo de rastr"],
    "alteracao_endereco": ["alterar endereco", "mudar endereco", "endereco errado", "trocar o endereco",
                           "novo endereco", "redirecionar", "mudei de endereco"],
    "cancelamento": ["cancel", "desistir", "desistencia", "devolver", "devolucao", "nao quero mais"],
    "elogio": ["parabens", "elogio", "excelente", "agradec", "muito bom", "otimo atendimento",
               "recomendo", "nota 10"],
}
_NEG = ["pessim", "horrivel", "absurdo", "inaceitavel", "revoltad", "vergonha", "descaso", "pior",
        "nunca mais", "decepcion", "insatisfeit", "ridicul", "lixo"]
_POS = ["parabens", "excelente", "agradec", "otimo", "muito bom", "recomendo", "satisfeit", "perfeito"]
_URGENT = ["urgente", "imediat", "hoje", "perecivel", "medicamento", "hospital", "procon", "advogado",
           "reclame aqui", "processo", "justica", "remedio"]
_BASE_PRIORITY = {
    "extravio_avaria": "alta", "atraso_entrega": "media", "cobranca_fatura": "media",
    "rastreamento": "baixa", "alteracao_endereco": "media", "cancelamento": "media",
    "elogio": "baixa", "outros": "baixa",
}


def classify_rules(subject: str, body: str) -> Result:
    """Classificador determinístico por palavras-chave (baseline e fallback)."""
    text = _norm(f"{subject}. {body}")
    scores = {c: sum(text.count(k) for k in kws) for c, kws in _KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top_cat, top), (_, second) = ranked[0], ranked[1]
    if top == 0:
        category, confidence = "outros", 0.3
    else:
        category, confidence = top_cat, min(0.95, 0.5 + 0.15 * (top - second))

    neg = sum(text.count(k) for k in _NEG)
    pos = sum(text.count(k) for k in _POS)
    sentiment = "negativo" if neg > pos else "positivo" if pos > neg else "neutro"
    if category in {"extravio_avaria", "atraso_entrega"} and sentiment == "neutro":
        sentiment = "negativo"

    level = PRIORITIES.index(_BASE_PRIORITY[category])
    if category != "elogio":
        level += int(any(k in text for k in _URGENT)) + int(neg >= 2)
    priority = PRIORITIES[min(level, 3)]

    summary = re.sub(r"\s+", " ", f"{subject.strip()} — {body.strip()}")[:200]
    return Result(category, priority, sentiment, summary, round(confidence, 2),
                  TEAM_BY_CATEGORY[category], "rules")


SYSTEM_PROMPT = (
    "Você é um sistema de triagem de tickets de suporte de uma transportadora. "
    "Classifique o ticket e responda SOMENTE com um objeto JSON válido."
)


def build_user_prompt(subject: str, body: str) -> str:
    return (
        "Classifique o ticket abaixo.\n\n"
        f"category: um de {CATEGORIES}\n"
        "priority: baixa | media | alta | critica. Use 'critica' apenas se houver risco à saúde, carga "
        "perecível/medicamento, ameaça legal (Procon/advogado) ou perda total. 'alta' para extravio/avaria "
        "ou cliente muito irritado; elogio é sempre 'baixa'.\n"
        "sentiment: negativo | neutro | positivo\n"
        "summary: resumo objetivo em até 200 caracteres, em português, sem dados pessoais\n"
        "confidence: número de 0 a 1 com a sua confiança na categoria\n\n"
        "Dados pessoais já foram mascarados (ex.: [CPF], [EMAIL]); não tente reconstruí-los.\n\n"
        f"=== TICKET ===\nAssunto: {subject}\nMensagem: {body}\n=== FIM ===\n"
        'Formato: {"category": "...", "priority": "...", "sentiment": "...", "summary": "...", "confidence": 0.0}'
    )


def parse_llm_output(text: str) -> Classification:
    """Extrai e valida o JSON da resposta. Levanta ``ValueError`` se inválido."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("resposta sem objeto JSON")
    try:
        return Classification.model_validate(json.loads(text[start:end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"JSON inválido ou fora do schema: {exc}") from exc


def anthropic_llm_fn(model: str) -> Callable[[str, str], str]:
    """Cria a função ``(system, user) -> texto`` usando o SDK da Anthropic (import tardio)."""
    import anthropic

    client = anthropic.Anthropic()

    def call(system: str, user: str) -> str:
        resp = client.messages.create(
            model=model, max_tokens=400, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    return call


class TicketClassifier:
    """Classifica um ticket. Sem ``llm_fn`` usa apenas regras."""

    def __init__(self, llm_fn: Optional[Callable[[str, str], str]] = None, retries: int = 3,
                 backoff_s: float = 1.0):
        self.llm_fn = llm_fn
        self.retries = retries
        self.backoff_s = backoff_s

    def classify(self, subject: str, body: str) -> Result:
        if self.llm_fn is None:
            return classify_rules(subject, body)
        user = build_user_prompt(subject, body)
        for attempt in range(self.retries):
            try:
                c = parse_llm_output(self.llm_fn(SYSTEM_PROMPT, user))
                return Result(c.category, c.priority, c.sentiment, c.summary.strip(),
                              round(c.confidence, 2), TEAM_BY_CATEGORY[c.category], "llm")
            except ValueError:
                break  # saída inválida: repetir raramente ajuda com temperatura 0
            except Exception:  # noqa: BLE001 - erro transitório de rede/API
                if attempt < self.retries - 1:
                    time.sleep(self.backoff_s * 2 ** attempt)
        fallback = classify_rules(subject, body)
        fallback.classified_by = "rules_fallback"
        return fallback
