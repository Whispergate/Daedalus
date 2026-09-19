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
from daedalus_ca.commands.list_builds import ListBuilds
from daedalus_ca.commands.check_build import CheckBuild
from daedalus_ca.commands.scan_artifact import ScanArtifact
from daedalus_ca.commands.quick_build import QuickBuild
from daedalus_ca.commands.get_log import GetLog

_AGENT_DIR = Path(__file__).parent


class DaedalusCA(PayloadType):
    name = "daedalus_ca"
    author = "@Lavender-exe"
    description = (
        "Command Augmentation container that extends any Mythic agent with "
        "CI/CD build management, artifact fetching, scanning, and obfuscation pipeline commands"
    )
    agent_type = AgentType.CommandAugment
    supported_os = [ SupportedOS.Windows ]
    semver = "1.1"
    agent_path = _AGENT_DIR
    agent_icon_path = str(_AGENT_DIR.parent / "assets" / "daedalus.png")
    command_augment_supported_agents = ["apollo", "athena", "merlin", "starburst"]
    supports_dynamic_loading = False
    note = (
        "Daedalus CA adds the following commands to supported agent callbacks:\n\n"
        "Build & Fetch:\n"
        "  daedalus_fetch_execute    - Fetch artifact and execute in-memory (BOF/assembly)\n"
        "  daedalus_register_tool    - Fetch artifact and register in Mythic for later use\n"
        "  daedalus_obfuscate_build  - Clone repo, obfuscate, build, and fetch\n"
        "  daedalus_quick_build      - Trigger any CI job, poll, download, and register/execute\n\n"
        "Monitoring & Discovery:\n"
        "  daedalus_list_builds      - List CI jobs, builds, or artifacts\n"
        "  daedalus_check_build      - Check build status with optional log tail\n"
        "  daedalus_get_log          - Retrieve full build console output\n\n"
        "Scanning:\n"
        "  daedalus_scan_artifact    - Fetch artifact and scan via LitterBox\n\n"
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
