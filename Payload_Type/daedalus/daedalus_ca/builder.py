from pathlib import Path

from mythic_container.PayloadBuilder import (
    PayloadType,
    AgentType,
    BuildResponse,
    BuildStatus,
    SupportedOS
)

from daedalus_ca.commands.fetch_execute import FetchExecute
from daedalus_ca.commands.register_tool import RegisterTool
from daedalus_ca.commands.obfuscate_build import ObfuscateBuild

_AGENT_DIR = Path(__file__).parent


class DaedalusCA(PayloadType):
    name = "daedalus_ca"
    author = "@Lavender-exe"
    description = (
        "Command Augmentation container that extends any Mythic agent with "
        "CI/CD artifact fetching, tool registration, and obfuscation pipeline commands"
    )
    agent_type = AgentType.CommandAugment
    supported_os = [ SupportedOS.Windows, SupportedOS.Linux, SupportedOS.MacOS ]
    semver = "1.1"
    agent_path = _AGENT_DIR
    agent_icon_path = str(_AGENT_DIR.parent / "assets" / "daedalus.png")
    command_augment_supported_agents = ["apollo", "athena", "merlin", "poseidon", "starburst"]
    supports_dynamic_loading = False
    note = (
        "Daedalus CA adds fetch_execute, register_tool, and obfuscate_build "
        "commands to supported agent callbacks.\n\n"
        "Configure secrets in your Mythic user settings (Settings > Secrets):\n"
        "  JENKINS_API_KEY    - Jenkins API token\n"
        "  GITHUB_API_KEY     - GitHub personal access token\n"
        "  GITLAB_API_KEY     - GitLab personal access token\n"
        "  FORGEJO_API_KEY    - Forgejo API token\n"
        "  GITEA_API_KEY      - Gitea API token\n"
        "  REPO_TOKEN         - Access token for private source repos\n"
    )

    async def build(self) -> BuildResponse:
        return BuildResponse(
            status=BuildStatus.Error,
            build_message=(
                "daedalus_ca is a Command Augmentation container and cannot be "
                "built as a standalone payload. Its commands are automatically "
                "injected into supported agent callbacks."
            ),
        )
