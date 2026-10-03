"""
Tool registry: the agent's order catalog.

Define a tool ONCE, with a decorator, and everything else is generated for you:

    from core.registry import tool, SAFE

    @tool(risk=SAFE)
    def word_count(text: str) -> str:
        '''Count the words in some text.

        Args:
            text: The text to count.
        '''
        return str(len(text.split()))

What the decorator does for you:
  * builds the JSON schema the AI model needs, from the type hints
  * takes the description from the docstring, and per argument help from "Args:"
  * files the tool under its name so the agent can find and call it
  * records its RISK, which decides whether the owner must approve each call

Risk tiers:
  SAFE  runs without asking (read only, low harm)
  ASK   needs the owner's approval every time (changes things, spends, sends)

The default is ASK. If you forget to set a risk, the tool asks first. FAIL CLOSED.
A risk can also be a function of the arguments, e.g. reading inside the project is
SAFE but reading anywhere else on the disk is ASK.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import types
from dataclasses import dataclass
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

log = logging.getLogger(__name__)

SAFE = "safe"
ASK = "ask"
_TIERS = (SAFE, ASK)

MAX_RESULT_CHARS = 20_000   # protects the model's context window from one giant result

RiskRule = Union[str, Callable[[dict], str]]


@dataclass
class Tool:
    name: str
    fn: Callable[..., Any]
    description: str
    parameters: dict
    risk: RiskRule = ASK

    def risk_for(self, args: dict) -> str:
        """Decide the risk tier for this specific call. Anything unexpected means ASK."""
        try:
            tier = self.risk(args) if callable(self.risk) else self.risk
        except Exception as exc:
            log.warning("Risk rule for %s failed (%s), treating as ASK", self.name, exc)
            return ASK
        return tier if tier in _TIERS else ASK

    def spec(self) -> dict:
        """The tool description in the OpenAI function calling format (the agent's lingua franca)."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


TOOLS: dict[str, Tool] = {}


# ── Schema generation from type hints ──────────────────────────────────────────

_PRIMITIVES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}


def _json_schema(tp: Any) -> dict:
    """Turn a Python type hint into a JSON schema fragment."""
    origin = get_origin(tp)

    # Optional[X] and X | None: drop the None and describe X.
    if origin in (Union, types.UnionType):
        members = [a for a in get_args(tp) if a is not type(None)]
        return _json_schema(members[0]) if len(members) == 1 else {"type": "string"}

    if origin is Literal:
        choices = list(get_args(tp))
        return {"type": _PRIMITIVES.get(type(choices[0]), "string"), "enum": choices}

    if origin in (list, tuple, set) or tp in (list, tuple, set):
        inner = get_args(tp)
        return {"type": "array", "items": _json_schema(inner[0]) if inner else {"type": "string"}}

    if origin is dict:
        return {"type": "object"}

    return {"type": _PRIMITIVES.get(tp, "string")}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a docstring into (summary, {arg_name: help}). Reads a Google style 'Args:' block."""
    doc = inspect.cleandoc(doc or "")
    summary, _, rest = doc.partition("Args:")
    arg_help: dict[str, str] = {}
    current = None
    for line in rest.splitlines():
        m = re.match(r"^\s+(\w+)\s*(?:\([^)]*\))?:\s*(.*)$", line)
        if m:
            current = m.group(1)
            arg_help[current] = m.group(2).strip()
        elif current and line.strip():
            if re.match(r"^\S", line):     # a new unindented section like "Returns:" ends Args
                break
            arg_help[current] += " " + line.strip()
    return " ".join(summary.split()), arg_help


def build_parameters(fn: Callable) -> dict:
    """Build the JSON schema for a function's arguments."""
    hints = get_type_hints(fn)
    _, arg_help = _parse_docstring(fn.__doc__ or "")
    props: dict[str, dict] = {}
    required: list[str] = []
    for pname, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        schema = _json_schema(hints.get(pname, str))
        if pname in arg_help:
            schema["description"] = arg_help[pname]
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        elif param.default is not None:
            schema["default"] = param.default
        props[pname] = schema
    out: dict = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


# ── The decorator ──────────────────────────────────────────────────────────────

def tool(risk: RiskRule = ASK, name: str | None = None, description: str | None = None):
    """Register a function as an agent tool. See the module docstring for an example."""
    if isinstance(risk, str) and risk not in _TIERS:
        raise ValueError(f"risk must be one of {_TIERS} or a function, got {risk!r}")

    def wrap(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        if tool_name in TOOLS:
            raise ValueError(f"A tool named {tool_name!r} is already registered")
        summary, _ = _parse_docstring(fn.__doc__ or "")
        desc = description or summary
        if not desc:
            raise ValueError(f"Tool {tool_name!r} needs a docstring: the model reads it to know when to use the tool")
        TOOLS[tool_name] = Tool(tool_name, fn, desc, build_parameters(fn), risk)
        return fn

    return wrap


# ── Lookup and calling ─────────────────────────────────────────────────────────

def get(name: str) -> Tool | None:
    return TOOLS.get(name)


def specs() -> list[dict]:
    """All tool specs, sorted by name so the prompt is stable (stable prompts cache better)."""
    return [TOOLS[n].spec() for n in sorted(TOOLS)]


def _cap(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n[... cut off, {len(text) - MAX_RESULT_CHARS:,} more characters]"


async def call(name: str, args: dict) -> str:
    """
    Run a tool and return its result as text. Never raises for a bad call: problems come
    back as an error message so the model can correct itself. Approval is NOT checked
    here; the agent loop does that before calling.
    """
    t = TOOLS.get(name)
    if t is None:
        return f"Unknown tool: {name}. Use only the tools you were given."
    try:
        bound = inspect.signature(t.fn).bind(**args)
    except TypeError as exc:
        return f"Tool error: bad arguments for {name}: {exc}"

    if inspect.iscoroutinefunction(t.fn):
        result = await t.fn(*bound.args, **bound.kwargs)
    else:
        # Plain functions (subprocess, mouse, disk) run in a worker thread so one slow
        # tool cannot freeze Telegram, Discord and everything else sharing the event loop.
        result = await asyncio.to_thread(t.fn, *bound.args, **bound.kwargs)
    return _cap("" if result is None else str(result))
