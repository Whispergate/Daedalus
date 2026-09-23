from __future__ import annotations

import os

JENKINS_API_KEY = "JENKINS_API_KEY"
GITHUB_API_KEY = "GITHUB_API_KEY"
GITLAB_API_KEY = "GITLAB_API_KEY"
FORGEJO_API_KEY = "FORGEJO_API_KEY"
GITEA_API_KEY = "GITEA_API_KEY"
REPO_TOKEN = "REPO_TOKEN"

_SECRET_TO_ENV = {
    "jenkins_token": JENKINS_API_KEY,
    "jenkins_user": None,
    "jenkins_url": None,
    "github_token": GITHUB_API_KEY,
    "gitlab_token": GITLAB_API_KEY,
    "forgejo_token": FORGEJO_API_KEY,
    "gitea_token": GITEA_API_KEY,
    "repo_token": REPO_TOKEN,
}

_PROVIDER_SECRET_KEY = {
    "jenkins": JENKINS_API_KEY,
    "github": GITHUB_API_KEY,
    "gitlab": GITLAB_API_KEY,
    "forgejo": FORGEJO_API_KEY,
    "gitea": GITEA_API_KEY,
}

_ENV_MAP = {
    "jenkins": {"url": "JENKINS_URL", "user": "JENKINS_USER", "token": "JENKINS_TOKEN"},
    "forgejo": {"url": "FORGEJO_URL", "token": "FORGEJO_TOKEN", "owner": "FORGEJO_OWNER", "repo": "FORGEJO_REPO"},
    "github": {"token": "GITHUB_TOKEN", "owner": "GITHUB_OWNER", "repo": "GITHUB_REPO", "api_base": "GITHUB_API_BASE"},
    "gitlab": {"url": "GITLAB_URL", "token": "GITLAB_TOKEN", "project_id": "GITLAB_PROJECT_ID"},
    "gitea": {"url": "GITEA_URL", "token": "GITEA_TOKEN", "owner": "GITEA_OWNER", "repo": "GITEA_REPO"},
}


def resolve_provider_kwargs(taskData, provider_name: str) -> dict:
    """Build provider kwargs from task args → Mythic Secrets → env vars."""
    get = taskData.args.get_arg
    secrets = taskData.Secrets or {}
    prefix = f"{provider_name}_"
    kwargs = {}

    for param, env_var in _ENV_MAP.get(provider_name, {}).items():
        val = get(f"{prefix}{param}") or get(param) or ""

        if not val and param == "token":
            secret_key = _PROVIDER_SECRET_KEY.get(provider_name)
            if secret_key and secret_key in secrets:
                val = secrets[secret_key]

        if not val:
            val = os.getenv(env_var, "")

        if val:
            kwargs[param] = val

    return kwargs


def resolve_secret(taskData, arg_name: str, env_var: str, secret_key: str | None = None) -> str:
    """Resolve a single value: task arg → Mythic Secret → env var."""
    val = taskData.args.get_arg(arg_name) or ""
    if not val and secret_key:
        secrets = taskData.Secrets or {}
        val = secrets.get(secret_key, "")
    if not val:
        val = os.getenv(env_var, "")
    return val
