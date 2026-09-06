# Testing & CI

## Running the tests

```bash
make test           # installs test deps into the venv, then runs pytest
# or directly:
pip install -r requirements-dev.txt
pytest
```

49 tests, well under a second. Run them before every push — `make test` runs
exactly what CI runs, so green locally means green on the PR.

Useful flags while you work:

```bash
pytest -v                                   # name every test as it runs
pytest tests/test_leads_db.py               # one file
pytest -k "sticky"                          # only tests matching a word
pytest -x                                   # stop at the first failure
```

---

## What CI is, and why this repo needed it

**Continuous Integration** is a robot that runs your checks on every push. You
find out in 30 seconds, automatically, instead of finding out in production.

This isn't theoretical here. While building Keisha's tiering, three bugs got
written and only caught because someone thought to look:

1. **A crash on old databases.** `sqlite3.OperationalError: no such column: tier`
   — an index was created before the migration that adds the column. Only
   surfaced because the migration path happened to get tested by hand.
2. **A silent wrong answer.** CSV headers were matched with a bare `lower()`,
   so `Missed Call Issue` never matched the `missed_call_issue` alias and
   *every imported lead quietly fell to tier B.* No error. No warning. Just
   wrong.
3. **A forgotten fact.** `missed_calls` wasn't a database column, so re-scoring
   a stored lead lost it.

Number 2 is the scary one. A crash is loud and you fix it. A silent wrong
answer just quietly makes your business worse until you happen to notice.

Every one of those now has a test. Reintroduce any of the bugs and the suite
goes red — which was verified by actually putting each bug back and watching
it fail:

| Bug reintroduced | Tests that caught it |
|---|---|
| Header normalization removed | 5 failed |
| `MAX()` sticky flag removed | 2 failed |
| Index created before migration | 1 failed |

**That check matters more than the test count.** A test that has never seen its
bug fail is a hope, not a test. When you write one, break the code on purpose
and confirm it goes red. If it stays green, the test is decorative.

---

## The two CI jobs

`.github/workflows/ci.yml` runs on every push to `main` and every pull request.

### 1. `compile` — every Python file parses

```bash
find . -name '*.py' | xargs python -m py_compile
```

Installs nothing. `py_compile` only *parses* a file, it never imports it, so
this job is fast and can't be broken by a bad package. It catches typos,
bad indentation, and syntax errors in files nobody imported yet.

### 2. `test` — the suite, on Python 3.11 and 3.12

The `matrix` runs the same job once per version. `fail-fast: false` means a
failure on 3.11 doesn't cancel 3.12 — you want to know if it's *both* versions
or just one, because that difference is usually the diagnosis.

CI deliberately installs **`requirements-dev.txt`, not `requirements.txt`**.
The tests only touch `config`, `core/leads.py`, and `tools/keisha.py`. Pulling
in the GUI and audio stack (`pyautogui` and friends) on a headless server buys
nothing but new ways for CI to fail on something unrelated to your change.

The `concurrency` block cancels a superseded run when you push again, so CI
reports on your latest commit instead of queueing up stale ones.

---

## How the tests are built

### The fixture problem, and `importlib.reload`

`core/leads.py` reads its database path **once, at import time**:

```python
_DB_PATH = Path(config.LEADS_DB)
```

So setting an environment variable *after* the module loaded changes nothing —
the value was already captured. Like writing a new address on an envelope
that's already in the mailbox.

`tests/conftest.py` handles it by reloading the modules after setting the
variable. `importlib.reload` re-runs a module top to bottom, as if importing
it fresh:

```python
monkeypatch.setenv("LEADS_DB", str(tmp_path / "test-leads.db"))
importlib.reload(config)
importlib.reload(core.leads)      # now it reads the NEW path
importlib.reload(tools.keisha)
```

The `keisha_env` fixture also pins every setting it depends on. A test that
silently reads *your* `.env` passes or fails based on machine state, which
makes it worse than no test at all — it lies to whoever runs it next.

### `tmp_path` — a fresh directory per test

pytest hands each test its own empty temp directory and cleans it up. Every
test starts from an empty database and can't see what any other test did.
Tests that share state fail in groups and pass alone, which is among the most
miserable things to debug.

### `parametrize` — one test, many inputs

```python
@pytest.mark.parametrize("header", [
    "Missed Call Issue", "missed_calls", "MISSED-CALLS", "has_missed_call_issue",
])
def test_missed_call_header_aliases(keisha_env, csv_file, header):
    ...
```

That's four separate tests from one function, each reported by name. Better
than a loop inside one test: when one spelling breaks, the failure names the
exact spelling instead of just saying "the loop failed."

### Async without an extra dependency

Some of Keisha's functions are `async`. Rather than add `pytest-asyncio`:

```python
def run(coro):
    return asyncio.run(coro)

result = run(keisha.send_approved())
```

`asyncio.run` starts an event loop, runs the coroutine to completion, and shuts
the loop down. One helper, no new package.

---

## What's covered

| Area | Guards against |
|---|---|
| **Dedupe** | The same business landing in the list twice; a messy duplicate overwriting a clean name; a sparse re-import erasing a known phone number |
| **Sticky flags** | Losing a known missed-call problem on re-import |
| **Migration** | Old databases crashing on new columns; migrations running twice |
| **Scoring** | Off-target niches qualifying; unreachable leads qualifying; the score not matching its own stated reasons |
| **Tiering** | A sorting behind B; unqualified leads sorting first; strict mode failing to drop tier B |
| **CSV intake** | Unrecognized header spellings; junk rows creating empty leads |
| **Pitch copy** | A missing CAN-SPAM footer; greeting a company by its legal name; claiming a missing website when the record shows one |
| **Guardrails** | **Sending a pending message**; sending to a suppressed address; blowing the daily limit; a restart resetting the limit; duplicate outreach |

The guardrail row is the one that matters. `test_pending_messages_are_never_sent`
is the single most important test in the suite: it is the line between an
assistant and a liability.

---

## Adding a test

1. Put it in `tests/`, in a `test_*.py` file, as a `def test_*()` function.
2. Take the `keisha_env` fixture if it touches the database — it hands back
   `(config, leads, keisha)` wired to a throwaway database.
3. Assert one behavior, and name the test after that behavior. A name like
   `test_suppression_after_approval_still_stops_the_send` tells you what broke
   from the CI log alone, without opening the file.
4. **Break the code on purpose and watch it fail.** Then fix it and watch it
   pass. Only then is it a test.
