# Keisha — autonomous lead qualification & outreach

Keisha finds the good leads in a pile of businesses, writes a pitch for each
one, and then **stops and waits for you**. She does not send anything on her
own. That is the whole design.

```
 import/add  →  qualify  →  draft  →  [ YOU APPROVE ]  →  send  →  report
                 (score)    (outbox:      (approved)     (sent)
                            pending)
```

---

## 1. Quick start

```bash
cp .env.example .env          # then fill in the KEISHA_* block
pip install -r requirements.txt

python main.py keisha import examples/leads.sample.csv
python main.py keisha run              # score everything, draft the winners
python main.py keisha review           # read what she wrote
python main.py keisha approve 1        # you say yes, one message at a time
python main.py keisha send             # only approved messages go out
python main.py keisha report           # where the pipeline stands
```

Out of the box `KEISHA_SEND_MODE=dry_run`, so `send` logs what *would* go out
and delivers nothing. Flip it to `smtp` only when you have read the drafts and
believe them.

### Every command

| Command | What it does |
|---|---|
| `keisha import <file.csv> [--niche X]` | Bulk-load leads, dedupe, score |
| `keisha add "Name" --email … --niche …` | Add one lead by hand |
| `keisha leads [--status X] [--niche Y] [--tier A\|B]` | List what's in the pipeline |
| `keisha qualify <lead_id>` | Re-score one lead, with the reasons |
| `keisha draft <lead_id> [--no-llm]` | Write one pitch into the queue |
| `keisha run [--limit N] [--tier A\|B]` | Score all new + draft all qualified, tier A first |
| `keisha review [--status pending]` | Read what's waiting on you |
| `keisha approve <outbox_id>` | Authorize one message |
| `keisha reject <outbox_id> "why"` | Kill it; lead goes back in the pool |
| `keisha send [--limit N]` | Deliver approved messages only |
| `keisha block <email\|domain> "why"` | Permanent do-not-contact |
| `keisha blocked` | Show the block list |
| `keisha report` | Pipeline snapshot |

The same twelve actions are registered as agent tools (`keisha_add_lead`,
`keisha_approve`, …), so you can also say "Keisha, run the pipeline" over
Telegram, Discord, Slack, or voice.

---

## 2. How scoring works

`tools/keisha.py` scores every lead out of 100 and shows its work:

| Signal | Points | Why |
|---|---|---|
| In one of `KEISHA_TARGET_NICHES` | **+40** | A trade you actually serve |
| No website on file | **+35** | The gap you sell into |
| Known missed-call problem | **+15** | Stated pain, not a guess |
| Reachable (email or phone) | **+10** | You can't pitch a ghost |

A lead needs `KEISHA_MIN_SCORE` (default 60) **and** a way to reach them.

### Tiers: both signals count, but not equally

Qualified leads split into two tiers, and Keisha works **A before B**:

| Tier | Means | Why it sorts there |
|---|---|---|
| **A** | Qualified **and** a known missed-call problem | You can point at a problem they already admit they have |
| **B** | Qualified on target niche + no website alone | Still worth pitching — but you're inferring the pain |

Tier B is your **backlog, not your trash**. `keisha run` drafts A first and
spends your daily budget on the hottest leads; `keisha run --tier A` restricts
to A entirely; `keisha leads --tier B` shows what's waiting behind them.

If you'd rather never pitch on an inference, set `KEISHA_REQUIRE_MISSED_CALLS=true`
and Tier B stops qualifying at all:

```
#2 Gulf Coast HVAC — PASS (85/100, threshold 60)
  +40 in a target niche (hvac)
  +35 no website found — that's the gap you close
  +10 reachable (email)
  PASS: strict mode is on and there's no missed-call evidence
```

Keisha learns the missed-call fact from a CSV column (`Missed Call Issue`,
`missed_calls`, and similar all work — headers are matched loosely) or from
`keisha add … --missed-calls`. The flag is **sticky**: once stored, a later
import that simply doesn't mention it cannot erase it.

Two things override the number entirely — no score is high enough to beat
them: a lead marked `do_not_contact`, and any address on the suppression list.

Tune it by editing the `W_*` constants at the top of `tools/keisha.py`, or the
`KEISHA_TARGET_NICHES` / `KEISHA_MIN_SCORE` values in `.env`.

---

## 3. The guardrails, and why each one exists

| Guardrail | What it stops |
|---|---|
| Nothing sends without `approved` status | Keisha emailing 400 strangers at 3 a.m. |
| Daily limit counted **from the database** | Restarting the bot resetting your cap to zero |
| Suppression re-checked at send time | Mailing someone who said STOP after you approved |
| One open message per lead | The same plumber getting three copies |
| `dry_run` is the default send mode | A bad template reaching real inboxes |
| CAN-SPAM footer on every email | A fineable offense (see below) |

**On the law, plainly:** US commercial email is governed by the CAN-SPAM Act.
It requires a real physical postal address, an honest subject line, and a
working opt-out that you honor promptly. Penalties run into five figures *per
email*. Keisha appends the footer automatically from `KEISHA_BUSINESS_NAME`
and `KEISHA_MAILING_ADDRESS` — fill those in, and honor every STOP with
`keisha block <address>`.

**On DMs:** automated direct messages violate the Terms of Service on
Instagram, X, and LinkedIn, and the ban is fast. Keisha will draft DM copy for
you; send those by hand.

---

## 4. What changed from the prototype (and why)

The original sketch was a good skeleton. Four things it couldn't survive
contact with reality, and what replaced them:

| Prototype | Problem | Now |
|---|---|---|
| `self.outreach_count` in memory | Restart the script, counter is back to 0, cap blown twice over | `leads.sent_since()` counts real sent rows in SQLite |
| `qualify_lead()` returns `True`/`False` | Can't tell a 61 from a 99, can't prioritize | Score 0-100 with every point explained |
| Leads passed in as function arguments | Nothing stored — no dedupe, no history, no follow-up | `leads` table with a dedupe key |
| One qualified pile | Nothing tells you which lead to call first | Tier A (missed-call evidence) before Tier B |
| `print()` instead of sending | No approval step, so "wire up the API" = instantly live | Outbox queue with an explicit human yes |

One more, about the copy itself. The prototype opened with *"I noticed you're
losing potential clients due to missed calls."* You don't actually know that.
Keisha says *"I went looking for you online and couldn't find a website"* —
which she **can** verify from the lead record. People can smell a guess, and a
provable observation opens more doors than a flattering assumption.

---

## 5. Learning the code

You asked to learn Python and SQL as we go. Here is what's actually in these
files, in plain terms.

### Python: the ideas worth stealing

**Separating storage from judgement.** `core/leads.py` only puts rows in
tables. `tools/keisha.py` only makes decisions. Think of a restaurant: the
walk-in cooler doesn't decide what's for dinner, and the chef doesn't need to
know how the compressor works. Change the recipe without rewiring the fridge.

**A context manager (`@contextmanager`) for the database.**

```python
@contextmanager
def _conn():
    c = sqlite3.connect(_DB_PATH)
    try:
        yield c          # ← your code runs here
        c.commit()
    finally:
        c.close()        # ← runs even if your code blew up
```

`yield` hands the connection to the `with` block; whatever happens next, the
`finally` closes it. It's the seatbelt that fastens itself.

**Named constants instead of magic numbers.** `W_NO_WEBSITE = 35` at the top of
the file beats a bare `35` buried three functions down. When you want to retune
targeting you edit one labeled block, not a scavenger hunt.

**`async` / `await` and `asyncio.gather`.** Writing 20 pitches means 20 calls to
an LLM, each mostly spent *waiting on the network*. `await` means "I'm waiting,
go run something else." `gather` starts all 20 waits at once. Like putting 20
loads in 20 washing machines instead of babysitting one — the machines do the
work, you just can't be in 20 rooms at once.

**Fail soft on the optional part.** `generate_pitch_llm` falls back to the plain
template if the LLM errors. An outage makes your copy a bit more generic; it
does not stop your pipeline.

### SQL: the queries in this project, explained

**Create a table.** `IF NOT EXISTS` makes it safe to run every single time —
first run builds it, every run after does nothing.

```sql
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- SQLite numbers these for you
    dedupe_key    TEXT NOT NULL UNIQUE,               -- the database enforces no duplicates
    business_name TEXT NOT NULL,
    score         INTEGER NOT NULL DEFAULT 0
);
```

`UNIQUE` is the important one. Rather than *hoping* your Python checks for
duplicates everywhere, you tell the database "this can never repeat" and it
refuses, forever, no matter which code path asks.

**Read rows with a filter.** The `?` is a **placeholder** — never paste values
into SQL with f-strings:

```sql
SELECT * FROM leads WHERE status = ? ORDER BY score DESC LIMIT ?;
```

```python
db.execute(sql, ("qualified", 25))     # ✅ safe
db.execute(f"... status = '{status}'") # ❌ SQL injection
```

Why: if `status` ever contains `'; DROP TABLE leads; --`, the f-string version
runs it as a command. The `?` version treats it as a harmless string. Same
reason you hand a bank teller a written slip instead of shouting instructions
across the lobby.

**Update without clobbering.** This one is worth understanding:

```sql
UPDATE leads SET email = COALESCE(NULLIF(?, ''), email) WHERE id = ?;
```

Read it inside out: `NULLIF(?, '')` turns an empty string into `NULL`.
`COALESCE(a, b)` returns the first non-`NULL` thing it's given. Together:
*"use the new email if there is one, otherwise keep what I already have."*
That's what lets a sparse re-import fill in blanks without erasing good data.

**Join two tables.** The outbox stores a `lead_id`, not a business name. To
show both at once you `JOIN` on the matching column:

```sql
SELECT o.id, o.subject, l.business_name
FROM outbox o
JOIN leads l ON l.id = o.lead_id
WHERE o.status = 'pending';
```

`o` and `l` are aliases — short nicknames so you don't retype the table name.

**Count things by group.**

```sql
SELECT status, COUNT(*) AS n FROM leads GROUP BY status;
```

`GROUP BY` collapses many rows into one row per distinct status; `COUNT(*)`
says how many fell into each pile. That single line is the whole report.

**Sort with a `CASE`.** Tiers are `'A'`, `'B'`, or `''` for unqualified. A plain
`ORDER BY tier` would put the empty strings **first**, which is backwards:

```sql
ORDER BY CASE WHEN tier = '' THEN 1 ELSE 0 END,   -- unqualified to the bottom
         tier ASC,                                 -- then A before B
         score DESC;                               -- then best score first
```

`CASE` is SQL's `if`. Here it invents a throwaway sort column — 0 for real
tiers, 1 for blanks — so the blanks fall to the end.

**Migrations.** `CREATE TABLE IF NOT EXISTS` builds a table that isn't there,
but it will **not** touch one that already is. So when this project added the
`tier` and `missed_calls` columns, every database created before that change
was missing them. That's what `_migrate()` handles:

```python
have = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
if "tier" not in have:
    c.execute("ALTER TABLE leads ADD COLUMN tier TEXT NOT NULL DEFAULT ''")
```

`PRAGMA table_info` asks SQLite to describe its own table — like asking a
building what rooms it has before you try to walk into one.

Order matters here, and getting it wrong is how I broke it the first time:
tables → migrate → **then** indexes. An index on a column the table doesn't
have yet fails outright, and that's exactly what an old database hitting a new
index looks like.

**`MAX()` for a sticky flag.**

```sql
UPDATE leads SET missed_calls = MAX(missed_calls, ?) WHERE id = ?;
```

Since the column is 0 or 1, `MAX(old, new)` means "true once, true forever."
A re-import that omits the column passes 0, and `MAX(1, 0)` is still 1 — the
fact you learned survives a lazier spreadsheet. A one-way door.

**An index.** `CREATE INDEX idx_leads_status ON leads (status);` — the book's
index instead of reading every page. You won't feel it at 200 leads. At 200,000
it's the difference between instant and a coffee break.

### Cerner CCL: the same ideas, different dialect

CCL (Cerner Command Language) is what you'll write against Oracle Health /
Millennium. It reads like SQL wearing a different coat. The exact same "show me
qualified leads" query:

```sql
/* ---------- Standard SQL (what leads.py runs) ---------- */
SELECT business_name, score
FROM leads
WHERE status = 'qualified'
ORDER BY score DESC;
```

```
/* ---------- CCL ---------- */
select into "nl:"
    business = l.business_name,
    score    = l.score
from
    leads l
where
    l.status = "qualified"
order by
    l.score desc
with nocounter
go
```

What's different, line by line:

- **`select into "nl:"`** — CCL always needs a destination. `"nl:"` means "no
  list", i.e. print to screen. You'd write `into "MINE"` for a file, or
  `into "value"` to load a record structure.
- **`business = l.business_name`** — the `alias = column` form. In standard SQL
  that's `SELECT l.business_name AS business`.
- **Double quotes for string literals.** `"qualified"`, not `'qualified'`.
  Single quotes mean something else in CCL. This trips up everyone once.
- **`with nocounter`** — a *report option*, suppressing the "N rows returned"
  line. Options live in a `with` clause: `with nocounter, maxcol = 300,
  separator = " "`.
- **`go`** — CCL's statement terminator, where standard SQL uses `;`.

Variables and control flow, which plain SQL doesn't have at all:

```
declare(min_score = i4, protect)   /* i4 = 4-byte integer; protect = don't let
                                      another program clobber it */
set min_score = 60

select into "nl:"
    business = l.business_name
from leads l
where l.score >= min_score
with nocounter
go
```

Date/time and code values, the two things you touch constantly in Millennium:

```
    admit = format(e.admit_dt_tm, "@SHORTDATETIME")   /* readable timestamp   */
    , sex = uar_get_code_display(p.sex_cd)            /* code value → "Female"*/
```

`uar_get_code_display()` is the one to memorize. Millennium stores nearly
everything as a numeric **code value** pointing into `code_value`; that function
turns `309` into `"Female"`. Your first hundred CCL bugs will be forgetting it
and staring at a column of numbers.

Outer joins look unusual too — CCL wraps the *column*, not the join:

```
from person p, encounter e
where p.person_id = outerjoin(e.person_id)
```

The mapping to hold onto:

| Concept | SQL | CCL |
|---|---|---|
| Destination | (implicit) | `select into "nl:"` |
| Alias | `AS name` | `name = column` |
| String literal | `'text'` | `"text"` |
| Statement end | `;` | `go` |
| Row-count off | n/a | `with nocounter` |
| Variable | n/a | `declare(x = i4)` / `set x = 60` |
| Code lookup | join a lookup table | `uar_get_code_display(x_cd)` |
| Outer join | `LEFT JOIN … ON` | `= outerjoin(col)` |

**One honest caveat:** CCL is a live clinical system's language. Test scripts in
your non-production domain first, and never run an untested `update`/`delete`
against production. Read-only `select` is where you learn.

---

## 6. Where things live

| File | Job |
|---|---|
| `core/leads.py` | SQLite storage: leads, outbox, suppression. No decisions. |
| `tools/keisha.py` | Scoring, pitch writing, the queue, the guardrails. |
| `cli/keisha.py` | The `python main.py keisha …` commands. |
| `core/agent.py` | Registers the 12 `keisha_*` tools for chat/voice. |
| `config.py` | All `KEISHA_*` / `SMTP_*` settings, read from `.env`. |
| `data/leads.db` | The database. Gitignored — back it up. |
