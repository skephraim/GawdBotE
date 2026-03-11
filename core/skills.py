"""
Skills manager — loads SKILL.md files and injects relevant ones into agent context.
Skills teach the agent how to use specific tools/integrations (Notion, GitHub, tmux, etc.).

Skills are markdown files with YAML frontmatter:
  name: skill-name
  description: one-line description for relevance matching
  ...
  ---
  # Skill content injected into system prompt when relevant
"""
from __future__ import annotations
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

SKILLS_DIR = config.PROJECT_ROOT / "skills"

# ── Skill data class ───────────────────────────────────────────────────────────

class Skill:
    def __init__(self, name: str, description: str, content: str, path: Path, os_restriction: list[str] = None):
        self.name = name
        self.description = description
        self.content = content
        self.path = path
        self.os_restriction = os_restriction or []  # e.g. ["darwin"] = macOS only

    def __repr__(self) -> str:
        return f"Skill({self.name!r})"


# ── Skill loader ───────────────────────────────────────────────────────────────

_cache: dict[str, Skill] | None = None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    front, body = parts[1], parts[2]
    meta: dict = {}
    for line in front.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("{") and not line.startswith("["):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    # Extract OS restrictions from metadata JSON blob
    os_match = re.search(r'"os":\s*\[([^\]]*)\]', front)
    if os_match:
        meta["_os"] = [s.strip().strip('"') for s in os_match.group(1).split(",")]
    return meta, body.strip()


def load_all() -> dict[str, Skill]:
    """Load all skills from the skills/ directory. Returns {name: Skill}."""
    global _cache
    if _cache is not None:
        return _cache

    skills: dict[str, Skill] = {}
    if not SKILLS_DIR.exists():
        log.warning("Skills directory not found: %s", SKILLS_DIR)
        return skills

    for skill_file in SKILLS_DIR.rglob("SKILL.md"):
        try:
            text = skill_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            name = meta.get("name") or skill_file.parent.name
            description = meta.get("description", "")
            os_restriction = meta.get("_os", [])
            skills[name] = Skill(name=name, description=description, content=body, path=skill_file, os_restriction=os_restriction)
        except Exception as e:
            log.warning("Failed to load skill %s: %s", skill_file, e)

    log.info("Loaded %d skills", len(skills))
    _cache = skills
    return skills


def reload() -> dict[str, Skill]:
    """Force-reload all skills."""
    global _cache
    _cache = None
    return load_all()


# ── Relevance matching ─────────────────────────────────────────────────────────

def find_relevant(message: str, max_skills: int = 3) -> list[Skill]:
    """
    Return skills relevant to the user message using keyword matching.
    Scores skills by how many words from their description/name appear in the message.
    """
    skills = load_all()
    message_lower = message.lower()
    words = set(re.findall(r'\w+', message_lower))

    scored: list[tuple[float, Skill]] = []
    for skill in skills.values():
        # Skip macOS-only skills on non-macOS
        if skill.os_restriction and "darwin" in skill.os_restriction:
            import sys
            if sys.platform != "darwin":
                continue

        # Score based on name + description keyword overlap
        skill_words = set(re.findall(r'\w+', (skill.name + " " + skill.description).lower()))
        overlap = words & skill_words
        if overlap:
            # Boost exact name match
            score = len(overlap) + (5 if skill.name.lower() in message_lower else 0)
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_skills]]


def build_skill_context(message: str) -> str:
    """
    Return a string of relevant skill content to inject into the system prompt.
    Empty string if no skills are relevant.
    """
    relevant = find_relevant(message)
    if not relevant:
        return ""

    parts = ["\n## Available Skills\n"]
    for skill in relevant:
        parts.append(f"### Skill: {skill.name}\n{skill.content}\n")

    return "\n".join(parts)


# ── Skill management tools ─────────────────────────────────────────────────────

def list_skills() -> str:
    """Return a formatted list of all available skills."""
    skills = load_all()
    if not skills:
        return "No skills installed. Run: clawhub install <skill-name>"
    lines = [f"  {s.name}: {s.description[:80]}" for s in sorted(skills.values(), key=lambda x: x.name)]
    return f"{len(skills)} installed skills:\n" + "\n".join(lines)


def search_skills(query: str) -> str:
    """Search skills by name or description."""
    skills = load_all()
    q = query.lower()
    matches = [s for s in skills.values()
               if q in s.name.lower() or q in s.description.lower()]
    if not matches:
        return f"No skills matching '{query}'. Try `clawhub search {query}` to find installable skills."
    lines = [f"  {s.name}: {s.description[:100]}" for s in matches]
    return "\n".join(lines)


def install_skill(name: str, version: str = "") -> str:
    """Install a skill via the clawhub CLI."""
    if not shutil.which("clawhub"):
        return (
            "clawhub CLI not installed.\n"
            "Install it: npm i -g clawhub\n"
            f"Then run: clawhub install {name}"
        )
    import subprocess
    cmd = ["clawhub", "install", name, "--dir", str(SKILLS_DIR)]
    if version:
        cmd += ["--version", version]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Reload cache after install
    reload()
    if result.returncode == 0:
        return f"Skill '{name}' installed.\n{result.stdout.strip()}"
    return f"Install failed:\n{result.stderr or result.stdout}"


def update_skill(name: str = "", force: bool = False) -> str:
    """Update a skill (or all skills) via clawhub."""
    if not shutil.which("clawhub"):
        return "clawhub CLI not installed. Install: npm i -g clawhub"
    import subprocess
    cmd = ["clawhub", "update", "--dir", str(SKILLS_DIR)]
    if name:
        cmd.insert(2, name)
    else:
        cmd.append("--all")
    if force:
        cmd.append("--force")
    result = subprocess.run(cmd, capture_output=True, text=True)
    reload()
    return result.stdout.strip() or result.stderr.strip() or "Done."
