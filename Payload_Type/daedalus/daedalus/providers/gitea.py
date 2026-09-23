from __future__ import annotations

from daedalus.providers.forgejo import ForgejoProvider


class GiteaProvider(ForgejoProvider):
    """Gitea Acts runner provider.

    Gitea's Actions API is compatible with Forgejo's (both derive from the
    same codebase). This provider inherits all behaviour from ForgejoProvider
    and exists so users can configure GITEA_* env vars separately and the
    provider name is clear in logs and tags.
    """

    name = "gitea"
