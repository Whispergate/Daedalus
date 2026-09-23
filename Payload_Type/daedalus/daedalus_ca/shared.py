from __future__ import annotations

import json
from pathlib import Path

from mythic_container.MythicCommandBase import (
    CommandParameter,
    ParameterGroupInfo,
    ParameterType,
)

BINARY_EXTS = (".exe", ".dll", ".bin", ".o", ".so", ".elf", ".cpl", ".sys")
SKIP_SUFFIXES = (".sha256", ".sha1", ".md5", ".sig", ".asc", ".json", ".txt", ".log")

_SUPPORT_FILE = Path(__file__).parent / "agent_support.json"
_AGENT_SUPPORT: dict[str, dict] = {}


def load_agent_support() -> dict[str, dict]:
    global _AGENT_SUPPORT
    if not _AGENT_SUPPORT:
        entries = json.loads(_SUPPORT_FILE.read_text())
        _AGENT_SUPPORT = {e["agent"]: e for e in entries}
    return _AGENT_SUPPORT


def select_artifact(artifacts: list[dict]) -> dict | None:
    binaries = [
        a for a in artifacts
        if any(a.get("relativePath", "").lower().endswith(e) for e in BINARY_EXTS)
    ]
    if binaries:
        return binaries[0]
    non_meta = [
        a for a in artifacts
        if not any(a.get("relativePath", "").lower().endswith(s) for s in SKIP_SUFFIXES)
    ]
    if non_meta:
        return non_meta[0]
    return artifacts[0] if artifacts else None


def provider_credential_parameters(start_position: int = 20) -> list[CommandParameter]:
    """Shared provider credential override parameters for CA commands."""
    pos = start_position
    params = []
    creds = [
        ("jenkins_url",        "Jenkins server URL (override - normally from Secrets/env)"),
        ("jenkins_user",       "Jenkins username (override)"),
        ("jenkins_token",      "Jenkins API token (override - set JENKINS_API_KEY in Mythic Secrets instead)"),
        ("github_token",       "GitHub token (override - set GITHUB_API_KEY in Mythic Secrets instead)"),
        ("github_owner",       "GitHub repo owner (override)"),
        ("github_repo",        "GitHub repo name (override)"),
        ("gitlab_url",         "GitLab server URL (override)"),
        ("gitlab_token",       "GitLab token (override - set GITLAB_API_KEY in Mythic Secrets instead)"),
        ("gitlab_project_id",  "GitLab project ID (override)"),
        ("forgejo_url",        "Forgejo server URL (override)"),
        ("forgejo_token",      "Forgejo token (override - set FORGEJO_API_KEY in Mythic Secrets instead)"),
        ("forgejo_owner",      "Forgejo repo owner (override)"),
        ("forgejo_repo",       "Forgejo repo name (override)"),
        ("gitea_url",          "Gitea server URL (override)"),
        ("gitea_token",        "Gitea token (override - set GITEA_API_KEY in Mythic Secrets instead)"),
        ("gitea_owner",        "Gitea repo owner (override)"),
        ("gitea_repo",         "Gitea repo name (override)"),
    ]
    for name, description in creds:
        params.append(CommandParameter(
            name=name,
            type=ParameterType.String,
            description=description,
            default_value="",
            parameter_group_info=[ParameterGroupInfo(required=False, ui_position=pos)],
        ))
        pos += 1
    return params
