from __future__ import annotations

import argparse
import re
from pathlib import Path

from cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
AGENTS_PATH = ROOT / "AGENTS.md"


def _extract_bash_block(path: Path, heading: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"## {re.escape(heading)}\n\n```bash\n(.*?)```", re.DOTALL)
    match = pattern.search(text)
    assert match, f"Could not find bash block for heading {heading!r} in {path.name}"
    return [line.strip() for line in match.group(1).strip().splitlines() if line.strip()]


def _extract_bullet_block(path: Path, anchor: str, terminator: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(anchor)}\n\n(.*?)(?=\n\n{re.escape(terminator)})",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"Could not find bullet block for {anchor!r} in {path.name}"
    return re.findall(r"^- `([^`]+)`", match.group(1), flags=re.MULTILINE)


def _documented_cli_examples() -> list[str]:
    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    commands: list[str] = []
    for name in subparsers_action.choices:
        base = f".venv/bin/python main.py {name}"
        if name == "run":
            commands.extend([base, f"{base} --ticker KPITTECH", f"{base} --rebalance-only"])
        elif name == "analyst" or name == "compare-providers":
            commands.append(f"{base} --ticker BSE")
        elif name == "usage-report":
            commands.append(f"{base} --last 10")
        else:
            commands.append(base)
    return commands


def test_readme_cli_usage_matches_supported_parser_commands() -> None:
    documented = _extract_bash_block(README_PATH, "CLI usage")
    expected = _documented_cli_examples()
    assert sorted(documented) == sorted(expected)


def test_agents_supported_commands_match_supported_parser_commands() -> None:
    documented = _extract_bash_block(AGENTS_PATH, "Supported Commands")
    expected = _documented_cli_examples()
    assert sorted(documented) == sorted(expected)


def test_agents_skill_registry_matches_installed_repo_local_skills() -> None:
    documented = _extract_bullet_block(
        AGENTS_PATH,
        "Current skills available under `.github/skills/`:",
        "Refresh this registry whenever the contents of `.github/skills/` change so the instructions remain accurate.",
    )
    installed = sorted(path.parent.name for path in (ROOT / ".github" / "skills").glob("*/SKILL.md"))
    assert sorted(documented) == installed


def test_agents_custom_agent_registry_matches_installed_repo_local_agents() -> None:
    documented = _extract_bullet_block(
        AGENTS_PATH,
        "Current agents available under `.github/agents/`:",
        "Refresh this registry whenever the contents of `.github/agents/` change so the instructions remain accurate.",
    )
    installed = sorted(path.name for path in (ROOT / ".github" / "agents").glob("*.agent.md"))
    assert sorted(documented) == installed
