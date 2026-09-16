from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mythic_container.MythicCommandBase import (
    CommandBase,
    CommandAttributes,
    CommandParameter,
    ParameterType,
    PTTaskMessageAllData,
    PTTaskCreateTaskingMessageResponse,
    TaskArguments,
)
from mythic_container.MythicGoRPC import (
    SendMythicRPCFileCreate,
    SendMythicRPCResponseCreate,
    MythicRPCFileCreateMessage,
    MythicRPCResponseCreateMessage,
)

from daedalus.providers import get_provider
from daedalus_ca.secrets import resolve_provider_kwargs

logger = logging.getLogger("daedalus_ca.register_tool")

_BINARY_EXTS = (".exe", ".dll", ".bin", ".o", ".so", ".elf", ".cpl", ".sys")
_SKIP_SUFFIXES = (".sha256", ".sha1", ".md5", ".sig", ".asc", ".json", ".txt", ".log")


def _select_artifact(artifacts: list[dict]) -> dict | None:
    binaries = [a for a in artifacts if any(a.get("relativePath", "").lower().endswith(e) for e in _BINARY_EXTS)]
    if binaries:
        return binaries[0]
    non_meta = [a for a in artifacts if not any(a.get("relativePath", "").lower().endswith(s) for s in _SKIP_SUFFIXES)]
    if non_meta:
        return non_meta[0]
    return artifacts[0] if artifacts else None


class RegisterToolArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider to fetch artifacts from",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
            ),
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job/workflow name to fetch the tool from",
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID (e.g. lastSuccessfulBuild)",
                default_value="lastSuccessfulBuild",
            ),
            CommandParameter(
                name="artifact_name",
                type=ParameterType.String,
                description="Specific artifact filename (auto-selects binary if empty)",
                default_value="",
            ),
            CommandParameter(
                name="tool_name",
                type=ParameterType.String,
                description="Name to register the tool as in Mythic (defaults to artifact filename)",
                default_value="",
            ),
            CommandParameter(
                name="comment",
                type=ParameterType.String,
                description="Comment to attach to the registered file",
                default_value="",
            ),
            CommandParameter(
                name="jenkins_url", type=ParameterType.String, description="Jenkins server URL", default_value="",
            ),
            CommandParameter(
                name="jenkins_user", type=ParameterType.String, description="Jenkins username", default_value="",
            ),
            CommandParameter(
                name="jenkins_token", type=ParameterType.String, description="Jenkins API token", default_value="",
            ),
            CommandParameter(
                name="github_token", type=ParameterType.String, description="GitHub token", default_value="",
            ),
            CommandParameter(
                name="github_owner", type=ParameterType.String, description="GitHub repo owner", default_value="",
            ),
            CommandParameter(
                name="github_repo", type=ParameterType.String, description="GitHub repo name", default_value="",
            ),
            CommandParameter(
                name="gitlab_url", type=ParameterType.String, description="GitLab server URL", default_value="",
            ),
            CommandParameter(
                name="gitlab_token", type=ParameterType.String, description="GitLab token", default_value="",
            ),
            CommandParameter(
                name="gitlab_project_id", type=ParameterType.String, description="GitLab project ID", default_value="",
            ),
            CommandParameter(
                name="forgejo_url", type=ParameterType.String, description="Forgejo server URL", default_value="",
            ),
            CommandParameter(
                name="forgejo_token", type=ParameterType.String, description="Forgejo token", default_value="",
            ),
            CommandParameter(
                name="forgejo_owner", type=ParameterType.String, description="Forgejo repo owner", default_value="",
            ),
            CommandParameter(
                name="forgejo_repo", type=ParameterType.String, description="Forgejo repo name", default_value="",
            ),
            CommandParameter(
                name="gitea_url", type=ParameterType.String, description="Gitea server URL", default_value="",
            ),
            CommandParameter(
                name="gitea_token", type=ParameterType.String, description="Gitea token", default_value="",
            ),
            CommandParameter(
                name="gitea_owner", type=ParameterType.String, description="Gitea repo owner", default_value="",
            ),
            CommandParameter(
                name="gitea_repo", type=ParameterType.String, description="Gitea repo name", default_value="",
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0:
            if self.command_line[0] == "{":
                self.load_args_from_json_string(self.command_line)

    async def parse_dictionary(self, dictionary: dict):
        self.load_args_from_dictionary(dictionary)


class RegisterTool(CommandBase):
    cmd = "register_tool"
    description = (
        "Fetch a BOF or .NET assembly from any CI/CD provider (Jenkins, GitHub "
        "Actions, GitLab CI, Forgejo, Gitea) and register it as a file in "
        "Mythic for later use by any callback."
    )
    help_cmd = "register_tool -provider jenkins -job loader-c-mingw -tool_name whoami.o"
    author = "@Lavender-exe"
    version = 2
    script_only = True
    argument_class = RegisterToolArguments
    attackmapping = ["T1105"]
    attributes = CommandAttributes(
        spawn_and_injectable=False,
        builtin=True,
        suggested_command=False,
        load_only=False,
    )

    async def create_go_tasking(
        self, taskData: PTTaskMessageAllData
    ) -> PTTaskCreateTaskingMessageResponse:
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )

        try:
            provider_name = (taskData.args.get_arg("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
            job = taskData.args.get_arg("job")
            build_id = taskData.args.get_arg("build_id") or "lastSuccessfulBuild"
            artifact_name = taskData.args.get_arg("artifact_name") or ""
            tool_name = taskData.args.get_arg("tool_name") or ""
            comment = taskData.args.get_arg("comment") or ""

            if not job:
                raise ValueError("job is required")

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            if not artifact_name and hasattr(provider, "list_artifacts"):
                artifacts = await provider.list_artifacts(job, build_id)
                selected = _select_artifact(artifacts)
                if not selected:
                    raise ValueError(f"No artifacts found for {job} #{build_id}")
                artifact_name = selected["relativePath"]

            if not artifact_name:
                artifact_name = job

            logger.warning("Downloading %s/%s #%s/%s", provider_name, job, build_id, artifact_name)
            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.warning("Downloaded %s (%d bytes)", artifact_name, len(artifact_bytes))

            filename = tool_name or artifact_name
            file_comment = comment or f"Registered by Daedalus CA from {provider_name}/{job} #{build_id}"

            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=artifact_bytes,
                Filename=filename,
                DeleteAfterFetch=False,
                Comment=file_comment,
            ))
            if not file_resp.success:
                raise RuntimeError(f"File registration failed: {file_resp.error}")

            agent_file_id = file_resp.agent_file_id
            logger.warning("Registered tool %s → %s", filename, agent_file_id)

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Registered {filename} ({len(artifact_bytes)} bytes)\n"
                    f"Source: {provider_name}/{job} #{build_id}\n"
                    f"File ID: {agent_file_id}"
                ).encode(),
            ))

            response.DisplayParams = f"{filename} from {provider_name}/{job} #{build_id}"
            response.Completed = True

        except Exception as e:
            logger.error("register_tool failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
