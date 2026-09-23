from __future__ import annotations

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
    SendMythicRPCFileCreate,
    SendMythicRPCResponseCreate,
    MythicRPCFileCreateMessage,
    MythicRPCResponseCreateMessage,
)

from daedalus.providers import get_provider
from daedalus_ca.secrets import resolve_provider_kwargs
from daedalus_ca.shared import select_artifact, provider_credential_parameters

logger = logging.getLogger("daedalus_ca.register_tool")


class RegisterToolArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job/workflow name to fetch the tool from",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=1),
                ],
            ),
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider (credentials resolved from Mythic Secrets)",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=2),
                ],
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID (e.g. lastSuccessfulBuild)",
                default_value="lastSuccessfulBuild",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=3),
                ],
            ),
            CommandParameter(
                name="artifact_name",
                type=ParameterType.String,
                description="Specific artifact filename (auto-selects binary if empty)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=4),
                ],
            ),
            CommandParameter(
                name="tool_name",
                type=ParameterType.String,
                description="Name to register the tool as in Mythic (defaults to artifact filename)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=5),
                ],
            ),
            CommandParameter(
                name="comment",
                type=ParameterType.String,
                description="Comment to attach to the registered file",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=6),
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


class RegisterTool(CommandBase):
    cmd = "daedalus_register_tool"
    description = (
        "Fetch a BOF or .NET assembly from any CI/CD provider and register it "
        "as a file in Mythic for later use. Credentials are resolved from "
        "Mythic Secrets automatically."
    )
    help_cmd = "daedalus_register_tool -job loader-c-mingw"
    author = "@Lavender-exe"
    version = 3
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

            if not artifact_name:
                artifacts = await provider.list_artifacts(job, build_id)
                selected = select_artifact(artifacts)
                if not selected:
                    raise ValueError(f"No artifacts found for {job} #{build_id}")
                artifact_name = selected["relativePath"]

            if not artifact_name:
                artifact_name = job

            logger.warning("Downloading %s/%s #%s/%s", provider_name, job, build_id, artifact_name)
            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.warning("Downloaded %s (%d bytes)", artifact_name, len(artifact_bytes))

            filename = tool_name or os.path.basename(artifact_name)
            file_comment = comment or f"Registered by Daedalus CA from {provider_name}/{job} #{build_id}"

            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=artifact_bytes,
                Filename=filename,
                DeleteAfterFetch=False,
                Comment=file_comment,
            ))
            if not file_resp.Success:
                raise RuntimeError(f"File registration failed: {file_resp.Error}")

            agent_file_id = file_resp.AgentFileId
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
