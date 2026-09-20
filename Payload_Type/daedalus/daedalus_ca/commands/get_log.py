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
    SendMythicRPCResponseCreate,
    MythicRPCResponseCreateMessage,
)

from daedalus.providers import get_provider
from daedalus_ca.secrets import resolve_provider_kwargs
from daedalus_ca.shared import provider_credential_parameters

logger = logging.getLogger("daedalus_ca.get_log")


class GetLogArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job/workflow name",
                parameter_group_info=[
                    ParameterGroupInfo(required=True, ui_position=1),
                ],
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID",
                default_value="lastBuild",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=2),
                ],
            ),
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=3),
                ],
            ),
            CommandParameter(
                name="lines",
                type=ParameterType.Number,
                description="Number of log lines to retrieve (from the end)",
                default_value=100,
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=4),
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


class GetLog(CommandBase):
    cmd = "daedalus_get_log"
    description = (
        "Retrieve the console output / build log from a CI/CD build. "
        "Use to debug build failures or inspect compilation output."
    )
    help_cmd = "daedalus_get_log -job loader-c-mingw -build_id 42 -lines 200"
    author = "@Lavender-exe"
    version = 1
    script_only = True
    argument_class = GetLogArguments
    attackmapping = ["T1082"]
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
            build_id = taskData.args.get_arg("build_id") or "lastBuild"
            raw_lines = taskData.args.get_arg("lines") or 100
            tail = int(raw_lines) if raw_lines else 100

            if not job:
                raise ValueError("job is required")

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            status = await provider.get_build_status(job, build_id)
            log_text = await provider.get_build_log(job, status.build_id or build_id, tail=tail)

            header = (
                f"Build Log: {provider_name}/{job} #{status.build_id or build_id} "
                f"({status.status.value})"
            )
            output = f"{header}\n{'=' * len(header)}\n{log_text}"

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=output.encode(),
            ))

            response.DisplayParams = f"{status.status.value} - {provider_name}/{job} #{status.build_id or build_id}"
            response.Completed = True

        except Exception as e:
            logger.error("get_log failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
