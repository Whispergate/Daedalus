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

logger = logging.getLogger("daedalus_ca.list_builds")


class ListBuildsArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="provider",
                type=ParameterType.ChooseOneCustom,
                description="CI/CD provider",
                choices=["jenkins", "github", "gitlab", "forgejo", "gitea"],
                default_value="jenkins",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=1),
                ],
            ),
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job name to list builds for (leave empty to list all jobs)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=2),
                ],
            ),
            CommandParameter(
                name="build_id",
                type=ParameterType.String,
                description="Build ID to list artifacts for (requires job)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=3),
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


class ListBuilds(CommandBase):
    cmd = "daedalus_list_builds"
    description = (
        "List CI/CD jobs, builds for a job, or artifacts for a specific build. "
        "Use with no arguments to list all jobs, with -job to list recent builds, "
        "or with -job and -build_id to list artifacts."
    )
    help_cmd = "daedalus_list_builds\ndaedalus_list_builds -job loader-c-mingw\ndaedalus_list_builds -job loader-c-mingw -build_id 42"
    author = "@Lavender-exe"
    version = 1
    script_only = True
    argument_class = ListBuildsArguments
    attackmapping = ["T1082"]
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
            build_id = taskData.args.get_arg("build_id") or ""

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            if build_id and job:
                artifacts = await provider.list_artifacts(job, build_id)
                if not artifacts:
                    output = f"No artifacts found for {job} #{build_id}"
                else:
                    lines = [f"Artifacts for {provider_name}/{job} #{build_id}:"]
                    for a in artifacts:
                        path = a.get("relativePath", a.get("name", "unknown"))
                        size = a.get("size", "")
                        size_str = f" ({size} bytes)" if size else ""
                        lines.append(f"  {path}{size_str}")
                    output = "\n".join(lines)
                response.DisplayParams = f"artifacts {provider_name}/{job} #{build_id}"

            elif job:
                status = await provider.get_build_status(job, "lastBuild")
                last_success = await provider.get_build_status(job, "lastSuccessfulBuild")

                lines = [f"Build info for {provider_name}/{job}:"]
                if status.build_id:
                    lines.append(f"  Latest:     #{status.build_id} → {status.status.value}")
                    if status.duration_seconds:
                        lines.append(f"  Duration:   {status.duration_seconds:.0f}s")
                    if status.url:
                        lines.append(f"  URL:        {status.url}")
                else:
                    lines.append("  Latest:     (no builds found)")

                if last_success.build_id and last_success.build_id != status.build_id:
                    lines.append(f"  Last OK:    #{last_success.build_id}")

                artifacts = await provider.list_artifacts(job, status.build_id or "lastBuild")
                if artifacts:
                    lines.append(f"  Artifacts:  {len(artifacts)}")
                    for a in artifacts[:5]:
                        path = a.get("relativePath", a.get("name", "unknown"))
                        lines.append(f"    - {path}")
                    if len(artifacts) > 5:
                        lines.append(f"    ... and {len(artifacts) - 5} more")

                output = "\n".join(lines)
                response.DisplayParams = f"builds {provider_name}/{job}"

            else:
                jobs = await provider.list_jobs()
                if not jobs:
                    output = f"No jobs found on {provider_name}"
                else:
                    lines = [f"Jobs on {provider_name} ({len(jobs)} total):"]
                    for j in jobs:
                        name = j.get("name", "unknown")
                        status = j.get("status", "")
                        status_str = f" [{status}]" if status else ""
                        lines.append(f"  {name}{status_str}")
                    output = "\n".join(lines)
                response.DisplayParams = f"jobs on {provider_name}"

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=output.encode(),
            ))
            response.Completed = True

        except Exception as e:
            logger.error("list_builds failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
