"""
Keisha — autonomous lead qualification and outreach drafting.

The pipeline, end to end:

    import/add  →  qualify  →  draft  →  [ YOU APPROVE ]  →  send  →  report
                   (score)     (outbox: pending)  (approved)   (sent)

Keisha never sends anything on her own. Every drafted message lands in the
outbox as `pending` and stays there until a human flips it to `approved`.
That single rule is what separates an assistant from a liability.

Storage lives in core/leads.py; this module holds the judgement calls.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from core import leads

log = logging.getLogger(__name__)

# ── Scoring weights ───────────────────────────────────────────────────────────
# Kept as named constants instead of magic numbers sprinkled through the code,
# so you can tune the targeting by editing one block and nothing else.
W_TARGET_NICHE = 40   # they're in a trade you actually serve
W_NO_WEBSITE   = 35   # the gap you sell into
W_MISSED_CALLS = 15   # stated pain: phone rings, nobody answers
W_REACHABLE    = 10   # you have an email or a phone number to use

MAX_SCORE = W_TARGET_NICHE + W_NO_WEBSITE + W_MISSED_CALLS + W_REACHABLE


# ── Small helpers ─────────────────────────────────────────────────────────────

def _truthy(value) -> bool:
    """Turn whatever a CSV or an LLM hands us ('yes', 'TRUE', 1, '') into a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "t")


def _start_of_today() -> float:
    """Unix timestamp for local midnight — the boundary for the daily send limit."""
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _first_name(contact_name: str) -> str:
    """
    'John Rivera' → 'John'.

    With no contact name we say "there", not the business name: "Hi Rivera
    Roofing," reads like a mail merge that broke, and that kills the email
    before the first sentence.
    """
    name = contact_name.strip()
    return name.split()[0] if name else "there"


# Trades whose names are acronyms and look wrong in lowercase.
_TRADE_DISPLAY = {"hvac": "HVAC", "it": "IT", "hvacr": "HVACR"}


def _trade(niche: str) -> str:
    """'hvac' → 'HVAC', 'plumbing' → 'plumbing', '' → 'service'."""
    n = (niche or "").strip().lower()
    return _TRADE_DISPLAY.get(n, n) or "service"


# ── Qualification ─────────────────────────────────────────────────────────────

def score_lead(lead: dict) -> dict:
    """
    Score one lead 0-100 and explain every point.

    Returns {"score": int, "qualified": bool, "reasons": [str], "blocked": str|None}.

    Returning the *reasons* alongside the number matters: when Keisha tells you
    a lead is a 75, you can see exactly which four things added up to 75 and
    argue with the ones you disagree with. A bare number teaches you nothing.
    """
    reasons: list[str] = []
    score = 0

    niche = (lead.get("niche") or "").strip().lower()
    website = (lead.get("website") or "").strip()
    email = (lead.get("email") or "").strip()
    phone = (lead.get("phone") or "").strip()
    missed_calls = _truthy(lead.get("missed_calls", lead.get("has_missed_call_issue", False)))

    # Hard blockers: these override the score entirely. No number is high
    # enough to justify contacting someone who asked not to be contacted.
    if lead.get("status") == "do_not_contact":
        return {"score": 0, "qualified": False, "reasons": ["Marked do-not-contact."],
                "blocked": "do_not_contact"}
    if email and leads.is_suppressed(email):
        return {"score": 0, "qualified": False, "reasons": ["Address is on the suppression list."],
                "blocked": "suppressed"}

    if niche and niche in config.KEISHA_TARGET_NICHES:
        score += W_TARGET_NICHE
        reasons.append(f"+{W_TARGET_NICHE} in a target niche ({niche})")
    else:
        reasons.append(f"+0 niche {niche or 'unknown'!r} is outside your target list")

    if not website:
        score += W_NO_WEBSITE
        reasons.append(f"+{W_NO_WEBSITE} no website found — that's the gap you close")
    else:
        reasons.append("+0 already has a website")

    if missed_calls:
        score += W_MISSED_CALLS
        reasons.append(f"+{W_MISSED_CALLS} known missed-call problem")

    if email or phone:
        score += W_REACHABLE
        reasons.append(f"+{W_REACHABLE} reachable ({'email' if email else 'phone'})")
    else:
        reasons.append("+0 no email and no phone — you cannot reach them")

    qualified = score >= config.KEISHA_MIN_SCORE and bool(email or phone)
    return {"score": score, "qualified": qualified, "reasons": reasons, "blocked": None}


def qualify(lead_id: int) -> str:
    """Score a stored lead and write the verdict back to its row."""
    lead = leads.get_lead(lead_id)
    if not lead:
        return f"No lead #{lead_id}."

    verdict = score_lead(lead)
    status = "qualified" if verdict["qualified"] else "rejected"
    if verdict["blocked"]:
        status = "do_not_contact"

    reason = "; ".join(verdict["reasons"])
    leads.set_lead_status(lead_id, status, score=verdict["score"], reason=reason)

    headline = "QUALIFIED" if verdict["qualified"] else "PASS"
    return (
        f"#{lead_id} {lead['business_name']} — {headline} "
        f"({verdict['score']}/{MAX_SCORE}, threshold {config.KEISHA_MIN_SCORE})\n  "
        + "\n  ".join(verdict["reasons"])
    )


# ── Intake ────────────────────────────────────────────────────────────────────

def add_lead(
    business_name: str,
    contact_name: str = "",
    email: str = "",
    phone: str = "",
    website: str = "",
    niche: str = "",
    city: str = "",
    notes: str = "",
    missed_calls: bool = False,
    source: str = "manual",
) -> str:
    """Add (or merge) a single lead, then immediately score it."""
    if missed_calls and "missed call" not in notes.lower():
        notes = (notes + " | missed-call issue reported").strip(" |")

    lead_id, created = leads.upsert_lead(
        business_name=business_name, contact_name=contact_name, email=email,
        phone=phone, website=website, niche=niche.strip().lower(),
        city=city, notes=notes, source=source,
    )
    verb = "Added" if created else "Updated existing"
    return f"{verb} lead #{lead_id}.\n" + qualify(lead_id)


# Column names we accept in an imported CSV, mapped to our own field names.
# Left side is what people actually name their spreadsheet columns; right side
# is what the database calls it.
_CSV_ALIASES = {
    "business": "business_name", "business_name": "business_name", "company": "business_name",
    "name": "business_name", "contact": "contact_name", "contact_name": "contact_name",
    "owner": "contact_name", "first_name": "contact_name",
    "email": "email", "e-mail": "email", "email_address": "email",
    "phone": "phone", "phone_number": "phone", "telephone": "phone",
    "website": "website", "site": "website", "url": "website", "web": "website",
    "niche": "niche", "industry": "niche", "category": "niche", "trade": "niche",
    "city": "city", "town": "city", "location": "city",
    "notes": "notes", "note": "notes",
    "missed_calls": "missed_calls", "missed_call_issue": "missed_calls",
}


def import_csv(path: str, default_niche: str = "") -> str:
    """
    Read a spreadsheet of leads and load them in.

    Column headers are matched loosely (see _CSV_ALIASES), so a file exported
    from Google Maps, Yelp, or typed by a VA all work without you renaming
    anything first. Rows with no business name are skipped rather than
    silently creating junk records.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return f"No such file: {p}"

    added = updated = skipped = 0
    results: list[str] = []

    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row: dict = {}
            for key, value in raw.items():
                if key is None:
                    continue
                field = _CSV_ALIASES.get(key.strip().lower())
                if field:
                    row[field] = (value or "").strip()

            if not row.get("business_name"):
                skipped += 1
                continue

            niche = (row.get("niche") or default_niche).strip().lower()
            lead_id, created = leads.upsert_lead(
                business_name=row["business_name"],
                contact_name=row.get("contact_name", ""),
                email=row.get("email", ""),
                phone=row.get("phone", ""),
                website=row.get("website", ""),
                niche=niche,
                city=row.get("city", ""),
                notes=row.get("notes", ""),
                source=f"csv:{p.name}",
            )
            if created:
                added += 1
            else:
                updated += 1

            # Carry the missed-call flag through to scoring for this row.
            stored = leads.get_lead(lead_id) or {}
            stored["missed_calls"] = row.get("missed_calls", "")
            verdict = score_lead(stored)
            status = "do_not_contact" if verdict["blocked"] else (
                "qualified" if verdict["qualified"] else "rejected")
            leads.set_lead_status(lead_id, status, score=verdict["score"],
                                  reason="; ".join(verdict["reasons"]))
            if verdict["qualified"]:
                results.append(f"  #{lead_id} {row['business_name']} — {verdict['score']}/{MAX_SCORE}")

    summary = (f"Imported {p.name}: {added} new, {updated} updated, {skipped} skipped.\n"
               f"{len(results)} qualified:")
    return summary + ("\n" + "\n".join(results[:25]) if results else " none.")


# ── Pitch writing ─────────────────────────────────────────────────────────────

def _footer() -> str:
    """
    The legally-required tail on commercial email (US CAN-SPAM Act): who is
    writing, a real postal address, and a working way to opt out. Missing any
    of these is what turns cold email from marketing into a fineable offense.
    """
    parts = []
    who = config.KEISHA_BUSINESS_NAME or config.KEISHA_SENDER_NAME
    if who:
        parts.append(who)
    if config.KEISHA_MAILING_ADDRESS:
        parts.append(config.KEISHA_MAILING_ADDRESS)
    parts.append("Not interested? Reply STOP and I'll take you off my list for good.")
    return "\n".join(parts)


def generate_pitch(lead: dict) -> tuple[str, str]:
    """
    Write the subject line and body for one lead. Returns (subject, body).

    Deliberately claims only what we can actually verify from the lead record.
    "I couldn't find a website for you" is checkable and true; "you're losing
    customers to missed calls" is a guess, and people can smell a guess.
    """
    first = _first_name(lead.get("contact_name", ""))
    business = lead.get("business_name", "your business")
    city = (lead.get("city") or "").strip()
    where = f" in {city}" if city else ""
    trade = _trade(lead.get("niche", ""))

    # Vary the subject line by lead id. Identical subjects sent in bulk are one
    # of the loudest spam signals there is.
    subjects = [
        f"{business} — couldn't find your website",
        f"Quick question about {business}",
        f"{business}: the calls you're not catching",
    ]
    subject = subjects[int(lead.get("id", 0)) % len(subjects)]

    observation = (
        f"I went looking for {business} online and couldn't find a website."
        if not (lead.get("website") or "").strip()
        else f"I had a look at {business} online."
    )

    booking = (f"\n\nIf it's useful, grab any slot here: {config.KEISHA_CALENDAR_LINK}"
               if config.KEISHA_CALENDAR_LINK else "")

    body = (
        f"Hi {first},\n\n"
        f"{observation} Most {trade} shops{where} I talk to are in the same spot — "
        f"the work is good, but the phone rings while you're on a job and that call "
        f"goes to the next name on the list.\n\n"
        f"I put together free 10-minute audits for exactly this: what people find "
        f"when they search for you, and what a simple site plus an AI receptionist "
        f"that answers every call would do for your booked jobs.\n\n"
        f"No charge and no pitch deck — if it's not worth your time I'll say so.\n\n"
        f"Worth 10 minutes this week?{booking}\n\n"
        f"— {config.KEISHA_SENDER_NAME}\n\n"
        f"---\n{_footer()}"
    )
    return subject, body


async def generate_pitch_llm(lead: dict) -> tuple[str, str]:
    """
    Same job as generate_pitch, but asks the LLM to personalize it.

    Falls back to the template on any failure, so an LLM outage never stops the
    pipeline — it just makes the copy a bit more generic for a while.
    """
    subject, template = generate_pitch(lead)
    try:
        from core import llm
        prompt = (
            "Rewrite this cold outreach email so it sounds like one working person "
            "writing to another. Keep it under 120 words, keep the free-audit offer, "
            "keep the closing question, and keep the '---' footer block exactly as "
            "written. Do not invent facts about the business. Return only the email.\n\n"
            f"Business: {lead.get('business_name')}\n"
            f"Trade: {lead.get('niche')}\n"
            f"City: {lead.get('city')}\n"
            f"Notes: {lead.get('notes')}\n\n"
            f"Draft:\n{template}"
        )
        resp = await llm.chat([{"role": "user", "content": prompt}])
        text = (resp.get("content") or "").strip()
        if len(text) > 80 and "---" in text:
            return subject, text
        log.warning("LLM pitch looked malformed; using template")
    except Exception as e:
        log.warning("LLM pitch failed (%s); using template", e)
    return subject, template


# ── Drafting into the approval queue ──────────────────────────────────────────

async def draft(lead_id: int, channel: str = "email", use_llm: bool = True) -> str:
    """
    Write a pitch for one lead and park it in the outbox as `pending`.

    Refuses to draft for a lead that is unqualified, suppressed, or already has
    a message in flight — that last check is what stops the same plumber
    getting three copies of your email.
    """
    lead = leads.get_lead(lead_id)
    if not lead:
        return f"No lead #{lead_id}."
    if lead["status"] == "do_not_contact":
        return f"#{lead_id} {lead['business_name']} is do-not-contact. Skipped."
    if lead["email"] and leads.is_suppressed(lead["email"]):
        return f"#{lead_id} {lead['business_name']} is suppressed. Skipped."
    if lead["status"] not in ("qualified", "queued"):
        return (f"#{lead_id} {lead['business_name']} is '{lead['status']}', not qualified. "
                f"Run qualify first.")
    if leads.has_open_message(lead_id):
        return f"#{lead_id} {lead['business_name']} already has a message in flight. Skipped."

    to_address = lead["email"] if channel == "email" else lead["phone"]
    if not to_address:
        return f"#{lead_id} {lead['business_name']} has no {channel} address. Skipped."

    subject, body = await generate_pitch_llm(lead) if use_llm else generate_pitch(lead)
    outbox_id = leads.queue_message(lead_id, channel, to_address, subject, body)
    leads.set_lead_status(lead_id, "queued")
    return (f"Drafted outbox #{outbox_id} → {lead['business_name']} <{to_address}>\n"
            f"Subject: {subject}\n\n{body}\n\n"
            f"(PENDING — nothing sent. Approve with: keisha approve {outbox_id})")


async def run_pipeline(limit: int = 20, use_llm: bool = True) -> str:
    """
    The one-command daily run: score every untouched lead, then draft for
    everyone who passes. Still ends with a queue full of pending drafts and
    zero messages sent.
    """
    fresh = leads.find_leads(status="new", limit=limit)
    for lead in fresh:
        qualify(lead["id"])

    ready = leads.find_leads(status="qualified", limit=limit)
    if not ready:
        return ("Pipeline run complete. No qualified leads waiting.\n"
                f"(Scored {len(fresh)} new lead(s).)")

    # Drafts are independent of each other, so write them concurrently rather
    # than waiting on each LLM call in turn.
    drafted = await asyncio.gather(
        *[draft(lead["id"], use_llm=use_llm) for lead in ready],
        return_exceptions=True,
    )
    lines = [str(d) if not isinstance(d, Exception) else f"Error: {d}" for d in drafted]
    return (f"Pipeline run: scored {len(fresh)} new, drafted {len(ready)}.\n"
            f"All PENDING your approval.\n\n" + "\n\n".join(lines))


# ── Review, approve, reject ───────────────────────────────────────────────────

def review(limit: int = 10, status: str = "pending") -> str:
    """Show what's waiting on you, in full, so you approve with your eyes open."""
    items = leads.list_outbox(status=status, limit=limit)
    if not items:
        return f"Outbox is empty ({status})."
    blocks = []
    for it in items:
        blocks.append(
            f"── outbox #{it['id']} [{it['status']}] via {it['channel']} ──\n"
            f"To: {it['business_name']} <{it['to_address']}>\n"
            f"Subject: {it['subject']}\n\n{it['body']}"
        )
    return (f"{len(items)} message(s) {status}:\n\n" + "\n\n".join(blocks) +
            "\n\nApprove: keisha approve <id>   Reject: keisha reject <id> \"reason\"")


def approve(outbox_id: int) -> str:
    item = leads.get_outbox_item(outbox_id)
    if not item:
        return f"No outbox item #{outbox_id}."
    if item["status"] != "pending":
        return f"Outbox #{outbox_id} is already '{item['status']}'."
    leads.set_outbox_status(outbox_id, "approved")
    return f"Approved outbox #{outbox_id}. It will go out on the next send run."


def reject(outbox_id: int, note: str = "") -> str:
    item = leads.get_outbox_item(outbox_id)
    if not item:
        return f"No outbox item #{outbox_id}."
    leads.set_outbox_status(outbox_id, "rejected", note=note or "rejected by owner")
    leads.set_lead_status(item["lead_id"], "qualified")   # free it up for a rewrite
    return f"Rejected outbox #{outbox_id}. The lead is back in the qualified pool."


def do_not_contact(value: str, reason: str = "requested") -> str:
    """Block an address or a whole domain forever. Honor every STOP, immediately."""
    return leads.suppress(value, reason)


# ── Sending ───────────────────────────────────────────────────────────────────

def _send_smtp(to_address: str, subject: str, body: str) -> None:
    """Deliver one email over SMTP. Blocking — callers run it in a thread."""
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = f"{config.KEISHA_SENDER_NAME} <{config.KEISHA_SENDER_EMAIL}>"
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


async def send_approved(limit: int = 25) -> str:
    """
    Deliver messages you approved — and only those.

    Three gates stand between a draft and a real inbox:
      1. the message must be `approved` (a human said yes),
      2. the recipient must not be suppressed (re-checked at send time,
         because someone may have asked out since you approved),
      3. the rolling daily total must be under KEISHA_DAILY_LIMIT.
    """
    already = leads.sent_since(_start_of_today())
    remaining = config.KEISHA_DAILY_LIMIT - already
    if remaining <= 0:
        return (f"Daily limit reached ({already}/{config.KEISHA_DAILY_LIMIT} sent today). "
                f"Pausing until tomorrow.")

    queue = leads.list_outbox(status="approved", limit=min(limit, remaining))
    if not queue:
        return f"Nothing approved to send. ({already}/{config.KEISHA_DAILY_LIMIT} sent today.)"

    mode = config.KEISHA_SEND_MODE.lower()
    sent = failed = skipped = 0
    lines: list[str] = []

    for item in queue:
        if leads.is_suppressed(item["to_address"]):
            leads.set_outbox_status(item["id"], "rejected", note="suppressed before send")
            skipped += 1
            lines.append(f"  #{item['id']} SKIPPED — {item['to_address']} is suppressed")
            continue

        if mode == "smtp":
            if not (config.SMTP_HOST and config.KEISHA_SENDER_EMAIL):
                return ("KEISHA_SEND_MODE=smtp but SMTP_HOST / KEISHA_SENDER_EMAIL are "
                        "not set. Nothing sent.")
            try:
                await asyncio.to_thread(_send_smtp, item["to_address"],
                                        item["subject"], item["body"])
            except Exception as e:
                leads.set_outbox_status(item["id"], "failed", note=str(e)[:300])
                failed += 1
                lines.append(f"  #{item['id']} FAILED — {e}")
                continue
        else:
            log.info("[dry_run] would send to %s: %s", item["to_address"], item["subject"])
            lines.append(f"  #{item['id']} DRY RUN → {item['to_address']}: {item['subject']}")

        leads.set_outbox_status(item["id"], "sent")
        leads.set_lead_status(item["lead_id"], "contacted")
        sent += 1

    total = already + sent
    header = (f"Send run ({mode}): {sent} sent, {failed} failed, {skipped} skipped. "
              f"{total}/{config.KEISHA_DAILY_LIMIT} today.")
    if mode != "smtp":
        header += "\nNOTHING actually left your machine — set KEISHA_SEND_MODE=smtp for real delivery."
    return header + ("\n" + "\n".join(lines) if lines else "")


# ── Reporting ─────────────────────────────────────────────────────────────────

def report() -> str:
    """A plain-language snapshot of the whole pipeline."""
    s = leads.stats()
    sent_today = leads.sent_since(_start_of_today())

    def block(title: str, counts: dict) -> str:
        if not counts:
            return f"{title}: none"
        rows = "\n".join(f"  {k:<15} {v}" for k, v in sorted(counts.items()))
        return f"{title}:\n{rows}"

    return (
        f"{config.KEISHA_NAME} — pipeline for {config.KEISHA_OWNER}\n"
        f"{'=' * 48}\n"
        f"Total leads: {s['total_leads']}   Suppressed: {s['suppressed']}\n"
        f"Sent today:  {sent_today}/{config.KEISHA_DAILY_LIMIT}   "
        f"Send mode: {config.KEISHA_SEND_MODE}\n\n"
        f"{block('Leads by status', s['leads_by_status'])}\n\n"
        f"{block('Outbox by status', s['outbox_by_status'])}\n\n"
        f"Targeting: {', '.join(config.KEISHA_TARGET_NICHES)} "
        f"(min score {config.KEISHA_MIN_SCORE}/{MAX_SCORE})"
    )
