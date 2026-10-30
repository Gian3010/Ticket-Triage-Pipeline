"""CLI:  python -m triage {generate,run,eval,roi,reset}"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from . import db
from .classifier import classify_rules
from .config import Settings
from .pipeline import run_inbox
from .roi import compute_roi, format_report
from .synthetic import generate_tickets, write_csv


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="triage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="gera tickets sintéticos na caixa de entrada")
    g.add_argument("--n", type=int, default=1300)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--name", default=None, help="nome do CSV (padrão: tickets_<seed>.csv)")

    sub.add_parser("run", help="processa a caixa de entrada")
    sub.add_parser("roi", help="relatório de ROI simulado")
    e = sub.add_parser("eval", help="acurácia do classificador de regras contra o gabarito sintético")
    e.add_argument("--n", type=int, default=2000)
    e.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    settings = Settings()

    if args.cmd == "generate":
        path = write_csv(generate_tickets(args.n, seed=args.seed),
                         settings.inbox_dir / (args.name or f"tickets_{args.seed}.csv"))
        print(f"{args.n} tickets gerados em {path}")
    elif args.cmd == "run":
        s = run_inbox(settings)
        print(f"lidos={s.tickets_read} novos={s.tickets_new} llm={s.by_llm} regras={s.by_rules} "
              f"fallback={s.by_fallback} revisão={s.needs_review} inválidos={s.invalid_rows}")
    elif args.cmd == "roi":
        engine = db.get_engine(settings.database_url)
        db.init_db(engine)
        rep = compute_roi(engine, settings)
        print(format_report(rep, settings) if rep else "Banco vazio. Rode `generate` e `run` primeiro.")
    elif args.cmd == "eval":
        rows = generate_tickets(args.n, seed=args.seed)
        ok, per = 0, Counter()
        tot = Counter(r["gold_category"] for r in rows)
        for r in rows:
            if classify_rules(r["subject"], r["body"]).category == r["gold_category"]:
                ok += 1
                per[r["gold_category"]] += 1
        print(f"Classificador de regras — acurácia de categoria: {ok / len(rows):.1%} ({len(rows)} tickets)")
        for cat in sorted(tot):
            print(f"  {cat:<20} {per[cat] / tot[cat]:.1%}  (n={tot[cat]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
