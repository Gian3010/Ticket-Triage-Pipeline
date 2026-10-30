"""Mascaramento de PII (LGPD) ANTES de enviar texto a qualquer LLM ou gravar no banco."""
from __future__ import annotations

import hashlib
import re

_CNPJ = re.compile(r"(?<![\w-])\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?![\w])")
_CPF = re.compile(r"(?<![\w-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\w])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w-])(?:\+?55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}-?\d{4}(?![\w])")


def mask_pii(text: str) -> str:
    """Substitui CNPJ, CPF, e-mail e telefone por marcadores. A ordem importa (CNPJ antes de CPF)."""
    text = _CNPJ.sub("[CNPJ]", text)
    text = _CPF.sub("[CPF]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[TELEFONE]", text)
    return text


def customer_ref(email: str) -> str:
    """Pseudonimiza o cliente: hash truncado, estável entre execuções (permite contar reincidência)."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
