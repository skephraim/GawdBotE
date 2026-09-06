"""
Keisha CLI — drive the lead pipeline from a terminal.

    python main.py keisha report
    python main.py keisha import leads.csv --niche plumbing
    python main.py keisha add "Apex Plumbing" --contact John --email john@apex.com \
                              --niche plumbing --city Houston --missed-calls
    python main.py keisha run                 # score + draft everything waiting
    python main.py keisha review              # read what's pending
    python main.py keisha approve 3
    python main.py keisha reject 4 "too generic"
    python main.py keisha send                # sends ONLY what you approved
    python main.py keisha block someone@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keisha", description="Keisha lead pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("report", help="Pipeline snapshot")

    p_imp = sub.add_parser("import", help="Import leads from a CSV file")
    p_imp.add_argument("path")
    p_imp.add_argument("--niche", default="", help="Niche to assume for rows that don't state one")

    p_add = sub.add_parser("add", help="Add one lead by hand")
    p_add.add_argument("business_name")
    p_add.add_argument("--contact", default="")
    p_add.add_argument("--email", default="")
    p_add.add_argument("--phone", default="")
    p_add.add_argument("--website", default="")
    p_add.add_argument("--niche", default="")
    p_add.add_argument("--city", default="")
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--missed-calls", action="store_true", dest="missed_calls")

    p_list = sub.add_parser("leads", help="List stored leads")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--niche", default=None)
    p_list.add_argument("--limit", type=int, default=25)

    p_q = sub.add_parser("qualify", help="Re-score one lead")
    p_q.add_argument("lead_id", type=int)

    p_d = sub.add_parser("draft", help="Draft a pitch for one lead into the outbox")
    p_d.add_argument("lead_id", type=int)
    p_d.add_argument("--no-llm", action="store_true", help="Use the plain template only")

    p_run = sub.add_parser("run", help="Score all new leads and draft for the qualified ones")
    p_run.add_argument("--limit", type=int, default=20)
    p_run.add_argument("--no-llm", action="store_true")

    p_rev = sub.add_parser("review", help="Show messages awaiting your approval")
    p_rev.add_argument("--limit", type=int, default=10)
    p_rev.add_argument("--status", default="pending")

    p_ap = sub.add_parser("approve", help="Approve one outbox message")
    p_ap.add_argument("outbox_id", type=int)

    p_rj = sub.add_parser("reject", help="Reject one outbox message")
    p_rj.add_argument("outbox_id", type=int)
    p_rj.add_argument("note", nargs="?", default="")

    p_send = sub.add_parser("send", help="Send approved messages (respects the daily limit)")
    p_send.add_argument("--limit", type=int, default=25)

    p_blk = sub.add_parser("block", help="Add an email or domain to do-not-contact")
    p_blk.add_argument("value")
    p_blk.add_argument("reason", nargs="?", default="requested")

    sub.add_parser("blocked", help="Show the do-not-contact list")

    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    # Imported here rather than at module top so `--help` stays instant and
    # doesn't touch the database.
    from core import leads
    from tools import keisha

    if args.cmd == "report":
        print(keisha.report())

    elif args.cmd == "import":
        print(keisha.import_csv(args.path, default_niche=args.niche))

    elif args.cmd == "add":
        print(keisha.add_lead(
            business_name=args.business_name, contact_name=args.contact,
            email=args.email, phone=args.phone, website=args.website,
            niche=args.niche, city=args.city, notes=args.notes,
            missed_calls=args.missed_calls,
        ))

    elif args.cmd == "leads":
        rows = leads.find_leads(status=args.status, niche=args.niche, limit=args.limit)
        if not rows:
            print("No leads match.")
            return
        print(f"{'ID':<5}{'SCORE':<7}{'STATUS':<12}{'NICHE':<12}BUSINESS")
        for r in rows:
            print(f"{r['id']:<5}{r['score']:<7}{r['status']:<12}"
                  f"{(r['niche'] or '-'):<12}{r['business_name']}")

    elif args.cmd == "qualify":
        print(keisha.qualify(args.lead_id))

    elif args.cmd == "draft":
        print(asyncio.run(keisha.draft(args.lead_id, use_llm=not args.no_llm)))

    elif args.cmd == "run":
        print(asyncio.run(keisha.run_pipeline(limit=args.limit, use_llm=not args.no_llm)))

    elif args.cmd == "review":
        print(keisha.review(limit=args.limit, status=args.status))

    elif args.cmd == "approve":
        print(keisha.approve(args.outbox_id))

    elif args.cmd == "reject":
        print(keisha.reject(args.outbox_id, args.note))

    elif args.cmd == "send":
        print(asyncio.run(keisha.send_approved(limit=args.limit)))

    elif args.cmd == "block":
        print(keisha.do_not_contact(args.value, args.reason))

    elif args.cmd == "blocked":
        rows = leads.list_suppressed()
        if not rows:
            print("Do-not-contact list is empty.")
            return
        for r in rows:
            print(f"  {r['value']:<40} {r['reason']}")


if __name__ == "__main__":
    main()
