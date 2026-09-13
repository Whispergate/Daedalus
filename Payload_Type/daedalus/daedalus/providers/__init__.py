from daedalus.providers.base import CIProvider, BuildResult, BuildStatus
from daedalus.providers.jenkins import JenkinsProvider
from daedalus.providers.forgejo import ForgejoProvider
from daedalus.providers.github import GitHubProvider
from daedalus.providers.gitlab import GitLabProvider
from daedalus.providers.gitea import GiteaProvider

PROVIDERS = {
    "jenkins": JenkinsProvider,
    "forgejo": ForgejoProvider,
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
    "gitea": GiteaProvider,
}


def get_provider(name: str, **kwargs) -> CIProvider:
    cls = PROVIDERS.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown CI provider: {name!r}. Available: {list(PROVIDERS)}")
    return cls(**kwargs)


__all__ = [
    "CIProvider", "BuildResult", "BuildStatus",
    "JenkinsProvider", "ForgejoProvider", "GitHubProvider",
    "GitLabProvider", "GiteaProvider",
    "PROVIDERS", "get_provider",
]
