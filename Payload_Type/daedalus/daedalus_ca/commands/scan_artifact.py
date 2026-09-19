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
from daedalus.eventing import _litterbox_upload_and_scan
from daedalus_ca.secrets import resolve_provider_kwargs
from daedalus_ca.shared import select_artifact, provider_credential_parameters

logger = logging.getLogger("daedalus_ca.scan_artifact")

_RISK_COLORS = {
    "low": "green",
    "medium": "orange",
    "high": "red",
    "critical": "purple",
}


class ScanArtifactArguments(TaskArguments):
    def __init__(self, command_line: str, task_id: int = 0, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="job",
                type=ParameterType.String,
                description="Job/workflow name to fetch the artifact from",
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
                name="build_id",
                type=ParameterType.String,
                description="Build number or run ID",
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
                name="litterbox_url",
                type=ParameterType.String,
                description="LitterBox API URL (falls back to LITTERBOX_URL env var)",
                default_value="",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=5),
                ],
            ),
            CommandParameter(
                name="scan_type",
                type=ParameterType.ChooseOne,
                description="Scan type to run",
                choices=["static", "dynamic", "both", "edr", "all"],
                default_value="both",
                parameter_group_info=[
                    ParameterGroupInfo(required=False, ui_position=6),
                ],
            ),
            CommandParameter(
                name="edr_profile",
                type=ParameterType.String,
                description="EDR profile for edr/all scan type (e.g. defender, crowdstrike)",
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


class ScanArtifact(CommandBase):
    cmd = "daedalus_scan_artifact"
    description = (
        "Fetch a CI/CD artifact and submit it to LitterBox for malware scanning. "
        "Returns risk assessment results. Use to vet payloads before deployment."
    )
    help_cmd = "daedalus_scan_artifact -job loader-c-mingw -scan_type both"
    author = "@Lavender-exe"
    version = 1
    script_only = True
    argument_class = ScanArtifactArguments
    attackmapping = ["T1027.005"]
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
            litterbox_url = taskData.args.get_arg("litterbox_url") or os.getenv("LITTERBOX_URL", "")
            scan_type = taskData.args.get_arg("scan_type") or "both"
            edr_profile = taskData.args.get_arg("edr_profile") or os.getenv("EDR_PROFILE", "")

            if not job:
                raise ValueError("job is required")
            if not litterbox_url:
                raise ValueError("litterbox_url is required (set LITTERBOX_URL env var or pass explicitly)")

            kwargs = resolve_provider_kwargs(taskData, provider_name)
            provider = get_provider(provider_name, **kwargs)

            if not artifact_name:
                artifacts = await provider.list_artifacts(job, build_id)
                selected = select_artifact(artifacts)
                if not selected:
                    raise ValueError(f"No artifacts found for {job} #{build_id}")
                artifact_name = selected["relativePath"]

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"Downloading {artifact_name} from {provider_name}/{job} #{build_id}...\n"
                    f"Scan type: {scan_type}"
                    + (f" (EDR: {edr_profile})" if edr_profile else "")
                ).encode(),
            ))

            artifact_bytes = await provider.download_artifact(job, build_id, artifact_name)
            logger.info("Downloaded %s (%d bytes) for scanning", artifact_name, len(artifact_bytes))

            md5, risk_data = await _litterbox_upload_and_scan(
                litterbox_url=litterbox_url,
                filename=os.path.basename(artifact_name),
                file_bytes=artifact_bytes,
                scan_type=scan_type,
                edr_profile=edr_profile,
            )

            lines = [
                f"Scan Results: {artifact_name}",
                f"  Source:    {provider_name}/{job} #{build_id}",
                f"  Size:     {len(artifact_bytes)} bytes",
                f"  MD5:      {md5}",
            ]

            if risk_data:
                risk_level = risk_data.get("risk_level", "unknown")
                color = _RISK_COLORS.get(risk_level, "grey")
                lines.append(f"  Risk:     {risk_level.upper()} ({color})")

                if risk_data.get("static"):
                    static = risk_data["static"]
                    lines.append(f"  Static:   {static.get('score', 'N/A')} — {static.get('verdict', 'N/A')}")
                if risk_data.get("dynamic"):
                    dynamic = risk_data["dynamic"]
                    lines.append(f"  Dynamic:  {dynamic.get('score', 'N/A')} — {dynamic.get('verdict', 'N/A')}")
                if risk_data.get("edr"):
                    edr = risk_data["edr"]
                    lines.append(f"  EDR:      {edr.get('detected', 'N/A')} — {edr.get('product', edr_profile)}")

                detections = risk_data.get("detections", [])
                if detections:
                    lines.append(f"  Detections ({len(detections)}):")
                    for d in detections[:10]:
                        engine = d.get("engine", "unknown")
                        result = d.get("result", "detected")
                        lines.append(f"    - {engine}: {result}")
                    if len(detections) > 10:
                        lines.append(f"    ... and {len(detections) - 10} more")
            else:
                lines.append("  Risk:     PENDING (results not ready yet, use daedalus_get_verdict to check later)")

            output = "\n".join(lines)

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=output.encode(),
            ))

            risk_label = risk_data.get("risk_level", "pending").upper() if risk_data else "PENDING"
            response.DisplayParams = f"[{risk_label}] {artifact_name} from {provider_name}/{job} #{build_id}"
            response.Completed = True

        except Exception as e:
            logger.error("scan_artifact failed: %s", e, exc_info=True)
            response.Success = False
            response.Error = str(e)
            response.Completed = True

        return response
