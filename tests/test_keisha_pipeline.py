"""
Pipeline tests: scoring, tiering, CSV intake, and the sending guardrails.

The guardrail tests matter most. Everything else here is about quality; those
are about not mailing a stranger who asked to be left alone.
"""
from __future__ import annotations

import asyncio

import pytest


def run(coro):
    """Run one coroutine to completion. Saves a pytest-asyncio dependency."""
    return asyncio.run(coro)


# ── Scoring ───────────────────────────────────────────────────────────────────

def test_target_niche_with_no_website_qualifies_as_tier_b(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com"})
    assert v["qualified"] is True
    assert v["tier"] == "B"
    assert v["score"] == 85


def test_missed_call_evidence_promotes_to_tier_a(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com",
                           "missed_calls": 1})
    assert v["tier"] == "A"
    assert v["score"] == 100


def test_off_target_niche_does_not_qualify(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "dental", "website": "", "email": "j@a.com"})
    assert v["qualified"] is False
    assert v["tier"] == ""


def test_existing_website_does_not_qualify_on_niche_alone(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "https://x.com", "email": "j@a.com"})
    assert v["score"] == 50
    assert v["qualified"] is False


def test_unreachable_lead_never_qualifies(keisha_env):
    """A perfect score is still useless with no way to contact them."""
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "", "missed_calls": 1,
                           "email": "", "phone": ""})
    assert v["qualified"] is False


def test_score_explains_every_point(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com",
                           "missed_calls": 1})
    assert len(v["reasons"]) == 4, "each signal should contribute a stated reason"
    assert sum(int(r.split()[0].lstrip("+")) for r in v["reasons"]) == v["score"]


def test_suppressed_address_is_blocked_regardless_of_score(keisha_env):
    _, leads, keisha = keisha_env
    leads.suppress("j@a.com")
    v = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com",
                           "missed_calls": 1})
    assert v["qualified"] is False
    assert v["blocked"] == "suppressed"


def test_do_not_contact_status_is_blocked(keisha_env):
    _, _, keisha = keisha_env
    v = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com",
                           "status": "do_not_contact"})
    assert v["blocked"] == "do_not_contact"


def test_strict_mode_drops_tier_b(keisha_env, monkeypatch):
    config, _, keisha = keisha_env
    monkeypatch.setattr(config, "KEISHA_REQUIRE_MISSED_CALLS", True)

    warm = keisha.score_lead({"niche": "plumbing", "website": "", "email": "j@a.com"})
    hot = keisha.score_lead({"niche": "plumbing", "website": "", "email": "k@a.com",
                             "missed_calls": 1})

    assert warm["qualified"] is False, "tier B should not qualify in strict mode"
    assert hot["qualified"] is True and hot["tier"] == "A", "tier A must still qualify"


# ── CSV intake ────────────────────────────────────────────────────────────────

def test_csv_headers_match_regardless_of_spacing_and_case(keisha_env, csv_file):
    """
    Regression: 'Missed Call Issue' never matched the 'missed_call_issue'
    alias, so every imported lead silently fell to tier B.
    """
    _, leads, keisha = keisha_env
    path = csv_file("leads.csv",
                    "Business,Email,Industry,City,Missed Call Issue\n"
                    "Apex Plumbing,john@apex.com,plumbing,Houston,yes\n")
    keisha.import_csv(path)

    lead = leads.find_leads()[0]
    assert lead["missed_calls"] == 1, "the missed-call column was not recognized"
    assert lead["tier"] == "A"


@pytest.mark.parametrize("header", [
    "Missed Call Issue", "missed_calls", "MISSED-CALLS", "has_missed_call_issue",
])
def test_missed_call_header_aliases(keisha_env, csv_file, header):
    _, leads, keisha = keisha_env
    path = csv_file("leads.csv",
                    f"Business,Email,Industry,{header}\n"
                    "Apex Plumbing,john@apex.com,plumbing,true\n")
    keisha.import_csv(path)
    assert leads.find_leads()[0]["tier"] == "A", f"header {header!r} not recognized"


def test_csv_import_skips_rows_with_no_business_name(keisha_env, csv_file):
    _, leads, keisha = keisha_env
    path = csv_file("leads.csv",
                    "Business,Email,Industry\n"
                    "Apex Plumbing,john@apex.com,plumbing\n"
                    ",orphan@nowhere.com,plumbing\n")
    result = keisha.import_csv(path)

    assert "1 skipped" in result
    assert len(leads.find_leads()) == 1


def test_csv_reimport_without_the_column_keeps_tier_a(keisha_env, csv_file):
    """The sticky flag, proven end to end through the importer."""
    _, leads, keisha = keisha_env
    keisha.import_csv(csv_file("full.csv",
                               "Business,Email,Industry,Missed Call Issue\n"
                               "Apex Plumbing,john@apex.com,plumbing,yes\n"))
    keisha.import_csv(csv_file("sparse.csv",
                               "Business,Email,Industry\n"
                               "Apex Plumbing,john@apex.com,plumbing\n"))

    assert leads.find_leads()[0]["tier"] == "A"


def test_missing_csv_file_reports_instead_of_crashing(keisha_env):
    _, _, keisha = keisha_env
    assert "No such file" in keisha.import_csv("/nonexistent/nope.csv")


# ── Drafting ──────────────────────────────────────────────────────────────────

def test_draft_requires_a_qualified_lead(keisha_env):
    _, leads, keisha = keisha_env
    lead_id, _ = leads.upsert_lead("Bright Star Dental", email="f@b.com", niche="dental")
    keisha.qualify(lead_id)

    assert "not qualified" in run(keisha.draft(lead_id, use_llm=False))
    assert leads.list_outbox() == []


def test_draft_refuses_a_second_message_to_the_same_lead(keisha_env):
    _, leads, keisha = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="j@a.com", niche="plumbing")
    keisha.qualify(lead_id)

    run(keisha.draft(lead_id, use_llm=False))
    assert "already has a message in flight" in run(keisha.draft(lead_id, use_llm=False))
    assert len(leads.list_outbox()) == 1


def test_draft_lands_pending_never_sent(keisha_env):
    _, leads, keisha = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="j@a.com", niche="plumbing")
    keisha.qualify(lead_id)
    run(keisha.draft(lead_id, use_llm=False))

    item = leads.list_outbox()[0]
    assert item["status"] == "pending"
    assert item["sent_ts"] is None


def test_pitch_carries_the_canspam_footer(keisha_env):
    _, _, keisha = keisha_env
    _, body = keisha.generate_pitch({"business_name": "Apex Plumbing", "niche": "plumbing",
                                     "contact_name": "John", "website": "", "id": 1})
    assert "Test Co LLC" in body, "sender identity missing"
    assert "1 Test St, Houston, TX" in body, "postal address missing — CAN-SPAM requires it"
    assert "STOP" in body, "opt-out instruction missing"


def test_pitch_greets_a_person_by_name_and_a_nameless_lead_neutrally(keisha_env):
    _, _, keisha = keisha_env
    _, named = keisha.generate_pitch({"business_name": "Apex Plumbing", "contact_name":
                                      "John Rivera", "niche": "plumbing", "website": "", "id": 1})
    _, anon = keisha.generate_pitch({"business_name": "Rivera Roofing", "contact_name": "",
                                     "niche": "roofing", "website": "", "id": 2})
    assert named.startswith("Hi John,")
    assert anon.startswith("Hi there,"), "must not greet a company by its legal name"


def test_pitch_only_claims_a_missing_website_when_one_is_missing(keisha_env):
    _, _, keisha = keisha_env
    _, body = keisha.generate_pitch({"business_name": "Apex Plumbing", "contact_name": "John",
                                     "niche": "plumbing", "website": "https://apex.com", "id": 1})
    assert "couldn't find a website" not in body, "asserted something the record contradicts"


def test_acronym_trades_are_capitalized(keisha_env):
    _, _, keisha = keisha_env
    _, body = keisha.generate_pitch({"business_name": "Gulf Coast HVAC", "contact_name": "Denise",
                                     "niche": "hvac", "website": "", "id": 1})
    assert "HVAC shops" in body and "hvac shops" not in body


# ── The sending guardrails ────────────────────────────────────────────────────

def _queue_one(leads, keisha, email="j@a.com", name="Apex Plumbing"):
    lead_id, _ = leads.upsert_lead(name, email=email, niche="plumbing")
    keisha.qualify(lead_id)
    run(keisha.draft(lead_id, use_llm=False))
    return leads.list_outbox()[0]["id"]


def test_pending_messages_are_never_sent(keisha_env):
    """The single most important test in this file."""
    _, leads, keisha = keisha_env
    _queue_one(leads, keisha)

    result = run(keisha.send_approved())

    assert "Nothing approved to send" in result
    assert leads.list_outbox(status="pending")[0]["sent_ts"] is None


def test_approved_messages_are_sent(keisha_env):
    _, leads, keisha = keisha_env
    outbox_id = _queue_one(leads, keisha)
    keisha.approve(outbox_id)

    assert "1 sent" in run(keisha.send_approved())
    assert leads.get_outbox_item(outbox_id)["status"] == "sent"


def test_dry_run_never_opens_an_smtp_connection(keisha_env, monkeypatch):
    _, leads, keisha = keisha_env
    monkeypatch.setattr(keisha, "_send_smtp",
                        lambda *a, **k: pytest.fail("dry_run must not send"))
    keisha.approve(_queue_one(leads, keisha))
    run(keisha.send_approved())


def test_a_message_cannot_be_approved_twice(keisha_env):
    _, leads, keisha = keisha_env
    outbox_id = _queue_one(leads, keisha)
    keisha.approve(outbox_id)
    assert "already 'approved'" in keisha.approve(outbox_id)


def test_rejecting_returns_the_lead_for_a_rewrite(keisha_env):
    _, leads, keisha = keisha_env
    outbox_id = _queue_one(leads, keisha)
    item = leads.get_outbox_item(outbox_id)

    keisha.reject(outbox_id, "too generic")

    assert leads.get_lead(item["lead_id"])["status"] == "qualified"
    assert "Drafted outbox" in run(keisha.draft(item["lead_id"], use_llm=False))


def test_suppression_after_approval_still_stops_the_send(keisha_env):
    """
    Someone can ask out between your approval and the send run. The check at
    draft time is not enough; it has to happen again at send time.
    """
    _, leads, keisha = keisha_env
    outbox_id = _queue_one(leads, keisha)
    keisha.approve(outbox_id)
    leads.suppress("j@a.com", "asked to stop")

    result = run(keisha.send_approved())

    assert "1 skipped" in result
    assert leads.get_outbox_item(outbox_id)["status"] != "sent"


def test_daily_limit_is_enforced(keisha_env, monkeypatch):
    config, leads, keisha = keisha_env
    monkeypatch.setattr(config, "KEISHA_DAILY_LIMIT", 1)

    for i in range(3):
        keisha.approve(_queue_one(leads, keisha, email=f"a{i}@x.com", name=f"Shop {i}"))

    assert "1 sent" in run(keisha.send_approved())
    assert "Daily limit reached" in run(keisha.send_approved())
    assert len(leads.list_outbox(status="sent")) == 1


def test_daily_count_comes_from_the_database_not_memory(keisha_env, monkeypatch):
    """
    Regression against the prototype's in-memory counter: a restart must not
    hand you a fresh allowance. Reloading the modules simulates that restart.
    """
    import importlib
    config, leads, keisha = keisha_env
    monkeypatch.setattr(config, "KEISHA_DAILY_LIMIT", 1)
    keisha.approve(_queue_one(leads, keisha))
    run(keisha.send_approved())

    importlib.reload(keisha)                        # "restart the bot"
    monkeypatch.setattr(config, "KEISHA_DAILY_LIMIT", 1)
    keisha.approve(_queue_one(leads, keisha, email="b@x.com", name="Second Shop"))

    assert "Daily limit reached" in run(keisha.send_approved()), \
        "a restart reset the daily limit"


def test_blocking_an_address_is_reported_plainly(keisha_env):
    _, _, keisha = keisha_env
    assert "never contact it again" in keisha.do_not_contact("j@a.com", "asked to stop")


# ── Pipeline ──────────────────────────────────────────────────────────────────

def test_pipeline_drafts_tier_a_first(keisha_env, csv_file):
    config, leads, keisha = keisha_env
    keisha.import_csv(csv_file("leads.csv",
        "Business,Email,Industry,Missed Call Issue\n"
        "Warm Shop,warm@x.com,plumbing,no\n"
        "Hot Shop,hot@x.com,plumbing,yes\n"))

    run(keisha.run_pipeline(limit=1, use_llm=False))

    queued = leads.list_outbox()
    assert len(queued) == 1
    assert queued[0]["business_name"] == "Hot Shop", "budget went to the colder lead"


def test_pipeline_can_restrict_to_one_tier(keisha_env, csv_file):
    _, leads, keisha = keisha_env
    keisha.import_csv(csv_file("leads.csv",
        "Business,Email,Industry,Missed Call Issue\n"
        "Warm Shop,warm@x.com,plumbing,no\n"
        "Hot Shop,hot@x.com,plumbing,yes\n"))

    run(keisha.run_pipeline(tier="A", use_llm=False))

    assert [i["business_name"] for i in leads.list_outbox()] == ["Hot Shop"]


def test_report_runs_on_an_empty_database(keisha_env):
    _, _, keisha = keisha_env
    assert "Total leads: 0" in keisha.report()
