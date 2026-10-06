"""
Brains: which AI models GawdBot thinks with, and in what order.

A "brain" is one model at one provider. A "chain" is an ordered list of brains for a job:
  main       the agent's thinking (must support tool calling)
  vision     looking at screenshots and images
  embedding  turning text into vectors for memory search

If the first brain in a chain fails (down, out of credit, no key), the next one is tried.
Think of it as an on call schedule: primary, then backup, then the backup's backup.

Where brains come from:
  1. brains.toml in the project folder, if it exists (see brains.example.toml)
  2. otherwise the old .env settings (LLM_PROVIDERS, OPENAI_MODEL, ...), so nothing breaks

Swapping live: `/brain use claude` in Telegram, Discord or the CLI chat moves that brain to
the front of the main chain. The choice is saved in data/brain_state.json and survives
restarts. brains.toml is re read whenever it changes, so edits apply without a restart.

PRIVACY: mark a brain `shares_data = true` when its provider may train on your prompts or
let people read them (most free tiers say so in their terms). Such a brain still answers,
but your saved memories are never put in front of it, and it is never used to embed them
unless MEMORY_TO_SHARED_BRAINS=true.

SECURITY: brains.toml holds the NAME of a secret (api_key = "ANTHROPIC_API_KEY"), never the
key itself. The key is fetched from the OS vault or .env at the moment of the call. A value
that looks like a real key is rejected so it never ends up in a file you might share.
"""
from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config

log = logging.getLogger(__name__)

KINDS = ("openai_compat", "anthropic")
ROLES = ("main", "vision", "embedding")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class BrainConfigError(ValueError):
    pass


@dataclass
class Brain:
    name: str
    kind: str                               # openai_compat (OpenAI, OpenRouter, NVIDIA, Ollama...) or anthropic
    model: str
    base_url: str = ""
    api_key: str = ""                       # NAME of the secret, e.g. "OPENAI_API_KEY"
    no_key: bool = False                    # local servers such as Ollama need no key
    tools: bool = True                      # can it call tools? required for the main chain
    vision: bool = False
    price_in: Optional[float] = None        # USD per million input tokens (None = unknown)
    price_out: Optional[float] = None       # USD per million output tokens
    max_tokens: int = 4096
    embed_input_type: bool = False          # NVIDIA style embeddings want input_type=query|passage
    shares_data: bool = False               # the provider may train on prompts or let humans read them
                                            # (most FREE tiers). Your saved memories are never sent to it.

    @property
    def label(self) -> str:
        return f"{self.name} ({self.model})"

    @property
    def provider(self) -> str:
        """Short provider name for cost reports: anthropic, openai, openrouter, nvidia, local..."""
        if self.kind == "anthropic":
            return "anthropic"
        host = (urlparse(self.base_url).hostname or "").lower()
        for needle, name in (("openai.com", "openai"), ("openrouter", "openrouter"), ("nvidia", "nvidia"),
                             ("groq", "groq"), ("x.ai", "xai"), ("googleapis", "google")):
            if needle in host:
                return name
        if host in ("localhost", "127.0.0.1", "::1"):
            return "local"
        return host or self.name

    def secret(self) -> str:
        if self.no_key:
            return "none"
        return config._secret(self.api_key) if self.api_key else ""

    def available(self) -> tuple[bool, str]:
        if not self.no_key and not self.secret():
            return False, f"its key {self.api_key or '(no api_key set)'} is not in the vault or .env"
        return True, ""

    def cost_usd(self, tokens_in: int, tokens_out: int) -> Optional[float]:
        if self.price_in is None or self.price_out is None:
            return None
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1_000_000


@dataclass
class BrainSetup:
    brains: dict[str, Brain]
    chains: dict[str, list[str]] = field(default_factory=dict)
    source: str = ""


# ── Loading from brains.toml ───────────────────────────────────────────────────

_BRAIN_FIELDS = {f.name for f in fields(Brain)} - {"name"}


def _check_secret_name(brain_name: str, value: str) -> None:
    if value and not _SECRET_NAME_RE.match(value):
        raise BrainConfigError(
            f"brains.{brain_name}.api_key must be the NAME of a secret like \"OPENAI_API_KEY\", "
            "not the key itself. Store the real key with: python main.py secrets set OPENAI_API_KEY"
        )


def _from_toml(path: Path) -> BrainSetup:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BrainConfigError(f"{path.name} is not valid TOML: {exc}") from exc

    brains: dict[str, Brain] = {}
    for name, spec in (data.get("brains") or {}).items():
        if not isinstance(spec, dict):
            raise BrainConfigError(f"brains.{name} must be a table")
        unknown = set(spec) - _BRAIN_FIELDS
        if unknown:   # catches typos like "modle" instead of silently ignoring them
            raise BrainConfigError(f"brains.{name} has unknown settings: {', '.join(sorted(unknown))}")
        if spec.get("kind") not in KINDS:
            raise BrainConfigError(f"brains.{name}.kind must be one of {KINDS}")
        if not spec.get("model"):
            raise BrainConfigError(f"brains.{name} needs a model")
        if spec.get("kind") == "openai_compat" and not spec.get("base_url"):
            raise BrainConfigError(f"brains.{name} needs a base_url")
        _check_secret_name(name, spec.get("api_key", ""))
        brains[name] = Brain(name=name, **spec)

    raw_chains = data.get("chains") or {}
    chains: dict[str, list[str]] = {}
    for role, names in raw_chains.items():
        if role not in ROLES:
            raise BrainConfigError(f"chains.{role} is not a known role; use {ROLES}")
        names = [names] if isinstance(names, str) else list(names)
        missing = [n for n in names if n not in brains]
        if missing:
            raise BrainConfigError(f"chains.{role} names brains that are not defined: {', '.join(missing)}")
        chains[role] = names
    chains.setdefault("main", [n for n, b in brains.items() if b.tools])
    chains.setdefault("vision", [n for n, b in brains.items() if b.vision])
    chains.setdefault("embedding", [])

    for n in chains["main"]:
        if not brains[n].tools:
            raise BrainConfigError(f"{n} is in the main chain but has tools = false; the agent needs tool calling")
    for n in chains["embedding"]:
        if brains[n].kind == "anthropic":
            raise BrainConfigError(f"{n} is an anthropic brain; Anthropic has no embeddings API")
    return BrainSetup(brains, chains, source=path.name)


# ── Fallback: build the same setup from the old .env variables ─────────────────

def _from_env() -> BrainSetup:
    b: dict[str, Brain] = {
        "nvidia": Brain("nvidia", "openai_compat", config.NVIDIA_MODEL, config.NVIDIA_BASE_URL, "NVIDIA_API_KEY"),
        "ollama": Brain("ollama", "openai_compat", config.OLLAMA_MODEL, config.OLLAMA_BASE_URL, no_key=True),
        "openai": Brain("openai", "openai_compat", config.OPENAI_MODEL, "https://api.openai.com/v1", "OPENAI_API_KEY", vision=True),
        "openrouter": Brain("openrouter", "openai_compat", config.OPENROUTER_MODEL, config.OPENROUTER_BASE_URL, "OPENROUTER_API_KEY"),
        "anthropic": Brain("anthropic", "anthropic", config.ANTHROPIC_MODEL, api_key="ANTHROPIC_API_KEY", vision=True),
        "nvidia-vision": Brain("nvidia-vision", "openai_compat", config.NVIDIA_VISION_MODEL, config.NVIDIA_BASE_URL, "NVIDIA_API_KEY", tools=False, vision=True),
        "ollama-vision": Brain("ollama-vision", "openai_compat", config.OLLAMA_VISION_MODEL, config.OLLAMA_BASE_URL, no_key=True, tools=False, vision=True),
        "nvidia-embed": Brain("nvidia-embed", "openai_compat", "nvidia/nv-embedqa-e5-v5", config.NVIDIA_BASE_URL, "NVIDIA_API_KEY", tools=False, embed_input_type=True),
        "ollama-embed": Brain("ollama-embed", "openai_compat", "nomic-embed-text", config.OLLAMA_BASE_URL, no_key=True, tools=False),
        "openai-embed": Brain("openai-embed", "openai_compat", "text-embedding-3-small", "https://api.openai.com/v1", "OPENAI_API_KEY", tools=False),
    }
    order = [p for p in config.LLM_PROVIDERS if p in ("nvidia", "ollama", "openai", "openrouter", "anthropic")]
    vision_map = {"nvidia": "nvidia-vision", "ollama": "ollama-vision", "openai": "openai", "anthropic": "anthropic"}
    embed_map = {"nvidia": "nvidia-embed", "ollama": "ollama-embed", "openai": "openai-embed"}
    chains = {
        "main": order,
        "vision": [vision_map[p] for p in order if p in vision_map],
        "embedding": [embed_map[p] for p in order if p in embed_map],
    }
    return BrainSetup(b, chains, source="environment (.env)")


_cache: tuple[Optional[float], Optional[BrainSetup]] = (None, None)


def load() -> BrainSetup:
    """Current brain setup. brains.toml is re read when its modification time changes."""
    global _cache
    path = Path(config.BRAINS_FILE)
    if not path.exists():
        return _from_env()
    mtime = path.stat().st_mtime
    if _cache[0] != mtime or _cache[1] is None:
        _cache = (mtime, _from_toml(path))
    return _cache[1]


# ── Live swapping ──────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return Path(config.BRAIN_STATE_FILE)


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def chain(role: str, setup: Optional[BrainSetup] = None) -> list[Brain]:
    """The brains for a role, in the order they will be tried."""
    setup = setup or load()
    names = list(setup.chains.get(role, []))
    if role == "main":
        pinned = _read_state().get("main")
        if pinned in setup.brains and setup.brains[pinned].tools:
            names = [pinned] + [n for n in names if n != pinned]
    return [setup.brains[n] for n in names]


def use(name: str) -> str:
    """Put a brain first in the main chain. Returns a message for the owner."""
    setup = load()
    if name not in setup.brains:
        return f"No brain called {name!r}. Known: {', '.join(sorted(setup.brains))}"
    if not setup.brains[name].tools:
        return f"{name} cannot call tools, so it cannot run the agent."
    ok, why = setup.brains[name].available()
    state = _read_state()
    state["main"] = name
    _write_state(state)
    note = "" if ok else f" Warning: {why}, so it will be skipped until you add it."
    return f"Main brain is now {setup.brains[name].label}.{note}"


def reset() -> str:
    state = _read_state()
    state.pop("main", None)
    _write_state(state)
    return "Main brain choice cleared. Using the configured order again."


def describe() -> str:
    setup = load()
    pinned = _read_state().get("main")
    lines = [f"Brains from {setup.source}:"]
    for role in ROLES:
        lines.append(f"\n{role}:")
        brains = chain(role, setup)
        if not brains:
            lines.append("  (none configured)")
        for i, b in enumerate(brains, 1):
            ok, why = b.available()
            tags = []
            if role == "main" and b.name == pinned:
                tags.append("chosen")
            if b.price_in is None:
                tags.append("price unknown")
            if b.shares_data:
                tags.append("shares your data: memories withheld")
            status = "ready" if ok else f"skipped: {why}"
            lines.append(f"  {i}. {b.label} [{status}]" + (f" ({', '.join(tags)})" if tags else ""))
    lines.append("\nSwitch with: /brain use <name>   Undo with: /brain reset")
    return "\n".join(lines)


def handle_command(args: list[str]) -> str:
    """Shared /brain command for CLI, Telegram and Discord."""
    try:
        if not args or args[0] in ("list", "show", "status"):
            return describe()
        if args[0] == "use" and len(args) == 2:
            return use(args[1])
        if args[0] == "reset":
            return reset()
    except BrainConfigError as exc:
        return f"Brain config problem: {exc}"
    return "Usage: /brain, /brain use <name>, /brain reset"
