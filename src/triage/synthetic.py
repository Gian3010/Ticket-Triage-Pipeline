"""Gerador de tickets sintéticos (fictícios) para o cenário da Rápido Express.

Inclui PII falsa (CPF/telefone/e-mail) para exercitar o mascaramento e a coluna ``gold_category``
(rótulo verdadeiro) para medir o classificador. O pipeline ignora ``gold_category``.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

COLUMNS = ["ticket_id", "received_at", "customer_email", "channel", "subject", "body", "gold_category"]
FIRST = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gabriela", "Hugo", "Inês", "João"]
CITIES = ["São Paulo", "Campinas", "Curitiba", "Recife", "Salvador", "Goiânia", "Porto Alegre"]
CHANNELS = ["email", "whatsapp", "formulario_site", "reclame_aqui"]

TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "atraso_entrega": [
        ("Pedido atrasado", "Meu pedido #{o} deveria ter chegado dia {d} e até agora nada. Já passou do prazo, podem me dar uma posição?"),
        ("Entrega demorando demais", "Compramos há {n} dias e a mercadoria ainda não chegou em {c}. Isso é um absurdo, preciso para esta semana."),
        ("Cadê minha encomenda?", "A previsão de entrega era ontem e nada. Pedido #{o}. Estou muito insatisfeito com a demora."),
    ],
    "extravio_avaria": [
        ("Mercadoria danificada", "Recebi o pedido #{o} com a caixa amassada e o produto quebrado. Quero providências."),
        ("Carga extraviada", "Minha encomenda #{o} foi extraviada, o rastreio parou em {c} há {n} dias. Isso é inaceitável."),
        ("URGENTE: caixa violada", "Os medicamentos do pedido #{o} chegaram com a caixa aberta e faltando itens. Preciso de solução imediata, vou acionar o Procon."),
    ],
    "cobranca_fatura": [
        ("Cobrança duplicada", "Fui cobrado duas vezes na fatura referente ao pedido #{o}. Solicito estorno do valor."),
        ("Segunda via de boleto", "Preciso da segunda via do boleto da nota fiscal do pedido #{o}, venceu e não recebi."),
        ("Valor errado na fatura", "O valor cobrado a mais no frete não confere com o combinado. Podem revisar a fatura?"),
    ],
    "rastreamento": [
        ("Código de rastreio", "Onde está meu pedido #{o}? O código de rastreio não atualiza desde ontem."),
        ("Status do pedido", "Gostaria de localizar a minha encomenda, o status do pedido #{o} está sem atualização."),
    ],
    "alteracao_endereco": [
        ("Mudança de endereço", "Preciso alterar o endereço de entrega do pedido #{o}, mudei de endereço para {c}."),
        ("Endereço errado", "Informei o endereço errado no pedido #{o}. Podem redirecionar para o novo endereço em {c}?"),
    ],
    "cancelamento": [
        ("Cancelar pedido", "Quero cancelar o pedido #{o}, não quero mais. Como faço a devolução?"),
        ("Desistência da compra", "Vou desistir do pedido #{o}, por favor cancelem antes do envio."),
    ],
    "elogio": [
        ("Parabéns pelo atendimento", "Quero agradecer a equipe: entrega do pedido #{o} foi excelente e dentro do prazo. Recomendo!"),
        ("Elogio ao motorista", "Parabéns ao motorista de {c}, muito educado. Ótimo atendimento, nota 10."),
    ],
    "outros": [
        ("Dúvida geral", "Boa tarde, gostaria de saber o horário de funcionamento do centro de distribuição de {c}."),
        ("Parceria comercial", "Represento uma empresa e gostaria de falar com o comercial sobre contratação de frete."),
    ],
}
WEIGHTS = {
    "atraso_entrega": 0.27, "extravio_avaria": 0.10, "cobranca_fatura": 0.14, "rastreamento": 0.22,
    "alteracao_endereco": 0.07, "cancelamento": 0.08, "elogio": 0.07, "outros": 0.05,
}
# Frases "contaminantes" que dificultam regras simples (ex.: reclamação de atraso que cita rastreio)
NOISE = [
    " Já tentei o rastreio no site e não ajudou.",
    " Já falei com vocês por telefone e ninguém resolveu.",
    "",
    "",
    "",
]


def _pii(rng: random.Random) -> str:
    kind = rng.choice(["cpf", "tel", "email", "none", "none"])
    if kind == "cpf":
        n = "".join(str(rng.randint(0, 9)) for _ in range(11))
        return f" Meu CPF é {n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}."
    if kind == "tel":
        return f" Contato: ({rng.randint(11, 85)}) 9{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}."
    if kind == "email":
        return f" Podem responder em contato{rng.randint(1, 999)}@exemplo.com.br."
    return ""


def generate_tickets(n: int, seed: int = 7, start: datetime | None = None) -> list[dict]:
    rng = random.Random(seed)
    start = start or datetime(2026, 10, 26, 8, 0, 0)
    cats, weights = list(WEIGHTS), list(WEIGHTS.values())
    rows = []
    for i in range(n):
        cat = rng.choices(cats, weights=weights)[0]
        subject, body = rng.choice(TEMPLATES[cat])
        text = body.format(o=rng.randint(1_000_000, 9_999_999), d=f"{rng.randint(1, 28):02d}/10",
                           n=rng.randint(2, 15), c=rng.choice(CITIES)) + rng.choice(NOISE) + _pii(rng)
        name = rng.choice(FIRST).lower()
        rows.append({
            "ticket_id": f"TCK-{seed}-{i:06d}",
            "received_at": (start + timedelta(minutes=rng.randint(0, 7 * 24 * 60))).isoformat(timespec="seconds"),
            "customer_email": f"{name}{rng.randint(1, 400)}@exemplo.com.br",
            "channel": rng.choice(CHANNELS),
            "subject": subject, "body": text, "gold_category": cat,
        })
    return rows


def write_csv(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path
