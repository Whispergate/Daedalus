from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

import httpx

from mythic_container.EventingBase import (
    CustomFunctionDefinition,
    Eventing,
    NewCustomEventingMessage,
    NewCustomEventingMessageResponse,
)
from mythic_container.SharedClasses import (
    ContainerOnStartMessage,
    ContainerOnStartMessageResponse,
)

from daedalus.providers import BuildStatus, get_provider

logger = logging.getLogger("daedalus")

MYTHIC_GRAPHQL = os.getenv("MYTHIC_GRAPHQL", "http://mythic_graphql:8080/v1/graphql")
MYTHIC_SERVER = os.getenv("MYTHIC_SERVER", "http://mythic_server:17443")

_POLL_START = 5.0
_POLL_MAX = 30.0
_POLL_BACKOFF = 1.5

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

_QUERY_PAYLOAD = """
query DaedalusPayload($uuid: String!) {
    payload(where: {uuid: {_eq: $uuid}}) {
        id
        filemetum { agent_file_id filename }
    }
}
"""

_QUERY_TAGTYPE = """
query DaedalusGetTagtype($name: String!) {
    tagtype(where: {name: {_eq: $name}}) { id }
}
"""

_INSERT_TAGTYPE = """
mutation DaedalusCreateTagtype($name: String!, $color: String!, $description: String!) {
    insert_tagtype_one(object: {name: $name, color: $color, description: $description}) { id }
}
"""

_INSERT_TAG = """
mutation DaedalusInsertTag(
    $tagtype_id: Int!, $payload_id: Int!,
    $source: String!, $url: String!, $data: jsonb!
) {
    insert_tag_one(object: {
        tagtype_id: $tagtype_id,
        payload_id: $payload_id,
        source: $source,
        url: $url,
        data: $data
    }) { id }
}
"""

_UPLOAD_FILE = """
mutation DaedalusUploadFile($filename: String!, $contents: String!) {
    uploadContainerFile(filename: $filename, contents: $contents) {
        agent_file_id
        status
        error
    }
}
"""


# ---------------------------------------------------------------------------
# Custom function: trigger_build
# ---------------------------------------------------------------------------

async def trigger_build(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        mythic_token = inputs.get("mythic_api_token", "")
        payload_uuid = inputs.get("payload_uuid", "").strip()
        poll_build = inputs.get("poll", "true").lower() in ("true", "1", "yes")
        raw_timeout = inputs.get("timeout", "300")
        try:
            poll_timeout = int(raw_timeout)
        except (ValueError, TypeError):
            poll_timeout = 300

        if not job:
            return NewCustomEventingMessageResponse(
                Success=False, Message="'job' input required (CI job/pipeline name)",
            )

        build_params = _extract_build_params(inputs)
        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        payload_int_id = None
        auth_headers = {}
        if payload_uuid and mythic_token:
            auth_headers = {"Authorization": f"Bearer {mythic_token}"}
            payload_int_id, agent_file_id, filename = await _query_payload(auth_headers, payload_uuid)
            if agent_file_id:
                payload_bytes = await _download_file(auth_headers, agent_file_id)
                build_params["SHELLCODE_SOURCE"] = f"mythic:{payload_uuid}"
                build_params["PAYLOAD_SIZE"] = str(len(payload_bytes))
                logger.warning(
                    "Payload %s resolved: %d bytes (%s)",
                    payload_uuid, len(payload_bytes), filename,
                )

        logger.warning(
            "Triggering build on %s - job=%s params=%s",
            provider_name, job, list(build_params.keys()),
        )
        result = await provider.trigger_build(job, build_params)

        if result.status == BuildStatus.FAILURE:
            return NewCustomEventingMessageResponse(
                Success=False, Message=f"Build trigger failed: {result.error}",
            )

        if not result.build_id:
            return NewCustomEventingMessageResponse(
                Success=True,
                Message=f"Build dispatched on {provider_name} (no build ID resolved yet). URL: {result.url}",
            )

        if not poll_build:
            return NewCustomEventingMessageResponse(
                Success=True,
                Message=f"Build triggered: {provider_name} #{result.build_id} - {result.url}",
            )

        final = await _poll_build(provider, job, result.build_id, poll_timeout)

        if final.status == BuildStatus.SUCCESS and payload_int_id and auth_headers:
            await _tag_payload_with_build(
                auth_headers, payload_int_id, provider_name, job, final,
            )

        status_label = final.status.value.upper()
        msg_parts = [
            f"Build {status_label}: {provider_name} #{final.build_id}",
            f"Duration: {final.duration_seconds:.0f}s" if final.duration_seconds else "",
            f"URL: {final.url}" if final.url else "",
            f"Error: {final.error}" if final.error else "",
        ]
        return NewCustomEventingMessageResponse(
            Success=final.status == BuildStatus.SUCCESS,
            Message=" | ".join(p for p in msg_parts if p),
        )

    except Exception as exc:
        logger.exception("Daedalus trigger_build failed")
        return NewCustomEventingMessageResponse(Success=False, Message=f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: check_status
# ---------------------------------------------------------------------------

async def check_status(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        build_id = inputs.get("build_id", "").strip()

        if not job or not build_id:
            return NewCustomEventingMessageResponse(
                Success=False, Message="'job' and 'build_id' inputs required",
            )

        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        result = await provider.get_build_status(job, build_id)

        include_log = inputs.get("include_log", "false").lower() in ("true", "1", "yes")
        log_snippet = ""
        if include_log:
            log_snippet = await provider.get_build_log(job, build_id, tail=50)

        msg_parts = [
            f"Status: {result.status.value.upper()}",
            f"Build: {provider_name} #{build_id}",
            f"Duration: {result.duration_seconds:.0f}s" if result.duration_seconds else "",
            f"URL: {result.url}" if result.url else "",
        ]
        message = " | ".join(p for p in msg_parts if p)
        if log_snippet:
            message += f"\n\n--- Log tail ---\n{log_snippet}"

        return NewCustomEventingMessageResponse(Success=True, Message=message)

    except Exception as exc:
        logger.exception("Daedalus check_status failed")
        return NewCustomEventingMessageResponse(Success=False, Message=f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: list_configs
# ---------------------------------------------------------------------------

async def list_configs(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()

        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        jobs = await provider.list_jobs()
        if not jobs:
            return NewCustomEventingMessageResponse(
                Success=True,
                Message=f"No jobs found on {provider_name} (or not accessible)",
            )

        lines = [f"Available jobs on {provider_name}:"]
        for j in jobs:
            status = j.get("status") or j.get("conclusion") or ""
            lines.append(f"  - {j['name']} [{status}] {j.get('url', '')}")

        return NewCustomEventingMessageResponse(Success=True, Message="\n".join(lines))

    except Exception as exc:
        logger.exception("Daedalus list_configs failed")
        return NewCustomEventingMessageResponse(Success=False, Message=f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: download_artifact
# ---------------------------------------------------------------------------

async def download_artifact(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        build_id = inputs.get("build_id", "").strip()
        artifact_name = inputs.get("artifact_name", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")
        payload_uuid = inputs.get("payload_uuid", "").strip()

        if not job or not build_id:
            return NewCustomEventingMessageResponse(
                Success=False, Message="'job' and 'build_id' inputs required",
            )

        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        artifact_bytes = await provider.download_artifact(
            job, build_id, artifact_name or "",
        )

        if mythic_token and artifact_bytes:
            auth_headers = {"Authorization": f"Bearer {mythic_token}"}
            encoded = base64.b64encode(artifact_bytes).decode()
            fname = artifact_name or f"daedalus-{provider_name}-{build_id}"

            data = await _gql(auth_headers, _UPLOAD_FILE, {
                "filename": fname,
                "contents": encoded,
            })
            upload_result = data.get("uploadContainerFile", {})
            file_id = upload_result.get("agent_file_id", "")

            if payload_uuid:
                payload_int_id, _, _ = await _query_payload(auth_headers, payload_uuid)
                if payload_int_id and file_id:
                    build_status = await provider.get_build_status(job, build_id)
                    await _tag_payload_with_build(
                        auth_headers, payload_int_id, provider_name, job, build_status,
                        extra_data={
                            "artifact_file_id": file_id,
                            "artifact_name": fname,
                        },
                    )

            return NewCustomEventingMessageResponse(
                Success=True,
                Message=(
                    f"Artifact downloaded ({len(artifact_bytes)} bytes) "
                    f"and uploaded to Mythic (file_id={file_id})"
                ),
            )

        return NewCustomEventingMessageResponse(
            Success=True,
            Message=(
                f"Artifact downloaded: {len(artifact_bytes)} bytes "
                f"(no Mythic token - not uploaded)"
            ),
        )

    except Exception as exc:
        logger.exception("Daedalus download_artifact failed")
        return NewCustomEventingMessageResponse(Success=False, Message=f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: scan_payload (Sphinx / LitterBox integration)
# ---------------------------------------------------------------------------

_INVOKE_CUSTOM_FUNCTION = """
mutation DaedalusInvokeCustomFunction(
    $container_name: String!,
    $function_name: String!,
    $inputs: jsonb!
) {
    eventingInvokeCustomFunction(
        container_name: $container_name,
        function_name: $function_name,
        inputs: $inputs
    ) { status error output }
}
"""

_QUERY_TAGS = """
query DaedalusTags($payload_id: Int!, $source: String!) {
    tag(where: {payload_id: {_eq: $payload_id}, source: {_eq: $source}},
        order_by: {id: desc}, limit: 1) {
        id
        data
        tagtype { name color }
    }
}
"""


async def scan_payload(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        payload_uuid = inputs.get("payload_uuid", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")
        litterbox_url = inputs.get("litterbox_url") or os.getenv("LITTERBOX_URL", "")
        scan_type = inputs.get("scan_type", "all").lower()
        edr_profile = inputs.get("edr_profile", "").strip()
        timeout = inputs.get("timeout", "120")
        method = inputs.get("method", "sphinx").lower()

        if not payload_uuid:
            return NewCustomEventingMessageResponse(
                Success=False, Message="'payload_uuid' input required",
            )
        if not mythic_token:
            return NewCustomEventingMessageResponse(
                Success=False, Message="'mythic_api_token' input required",
            )

        auth_headers = {"Authorization": f"Bearer {mythic_token}"}

        if method == "sphinx":
            result = await _scan_via_sphinx(
                auth_headers, payload_uuid, litterbox_url,
                scan_type, edr_profile, timeout,
            )
        elif method == "direct":
            result = await _scan_direct_litterbox(
                auth_headers, payload_uuid, litterbox_url,
                scan_type, edr_profile, int(timeout),
            )
        else:
            return NewCustomEventingMessageResponse(
                Success=False,
                Message=f"Unknown scan method: {method!r}. Use 'sphinx' or 'direct'",
            )

        return result

    except Exception as exc:
        logger.exception("Daedalus scan_payload failed")
        return NewCustomEventingMessageResponse(
            Success=False, Message=f"Daedalus scan error: {exc}",
        )


async def _scan_via_sphinx(
    headers: dict,
    payload_uuid: str,
    litterbox_url: str,
    scan_type: str,
    edr_profile: str,
    timeout: str,
) -> NewCustomEventingMessageResponse:
    sphinx_inputs = {
        "payload_uuid": payload_uuid,
        "mythic_api_token": headers["Authorization"].removeprefix("Bearer "),
        "scan_type": scan_type,
        "timeout": timeout,
    }
    if litterbox_url:
        sphinx_inputs["litterbox_url"] = litterbox_url
    if edr_profile:
        sphinx_inputs["edr_profile"] = edr_profile

    try:
        data = await _gql(headers, _INVOKE_CUSTOM_FUNCTION, {
            "container_name": "sphinx",
            "function_name": "execute_script",
            "inputs": sphinx_inputs,
        })
        result = data.get("eventingInvokeCustomFunction", {})
        status = result.get("status", "")
        output = result.get("output", "")
        error = result.get("error", "")

        if status == "success":
            return NewCustomEventingMessageResponse(
                Success=True,
                Message=f"Sphinx scan complete: {output}",
            )
        else:
            return NewCustomEventingMessageResponse(
                Success=False,
                Message=f"Sphinx scan failed: {error or output or status}",
            )

    except RuntimeError as exc:
        if "not found" in str(exc).lower() or "container" in str(exc).lower():
            return NewCustomEventingMessageResponse(
                Success=False,
                Message=(
                    "Sphinx container not found. "
                    "Install Sphinx or use method='direct' with LITTERBOX_URL set."
                ),
            )
        raise


async def _scan_direct_litterbox(
    headers: dict,
    payload_uuid: str,
    litterbox_url: str,
    scan_type: str,
    edr_profile: str,
    timeout: int,
) -> NewCustomEventingMessageResponse:
    if not litterbox_url:
        return NewCustomEventingMessageResponse(
            Success=False,
            Message="'litterbox_url' required for direct scan (or set LITTERBOX_URL)",
        )

    litterbox_url = litterbox_url.rstrip("/")
    payload_int_id, agent_file_id, filename = await _query_payload(headers, payload_uuid)

    if not agent_file_id:
        return NewCustomEventingMessageResponse(
            Success=False,
            Message=f"Payload {payload_uuid!r} not found in Mythic",
        )

    payload_bytes = await _download_file(headers, agent_file_id)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{litterbox_url}/upload",
            files={"file": (filename, payload_bytes, "application/octet-stream")},
        )
        if r.status_code != 200:
            return NewCustomEventingMessageResponse(
                Success=False,
                Message=f"LitterBox upload failed: HTTP {r.status_code} - {r.text[:300]}",
            )
        md5 = r.json()["file_info"]["md5"]

    async with httpx.AsyncClient(timeout=120) as client:
        scan_endpoints = []
        if scan_type in ("static", "both", "all"):
            scan_endpoints.append(("static", f"{litterbox_url}/analyze/static/{md5}"))
        if scan_type in ("dynamic", "both", "all"):
            scan_endpoints.append(("dynamic", f"{litterbox_url}/analyze/dynamic/{md5}"))
        if scan_type in ("edr", "all") and edr_profile:
            scan_endpoints.append(("edr", f"{litterbox_url}/analyze/edr/{edr_profile}/{md5}"))

        for label, url in scan_endpoints:
            try:
                await client.post(url)
            except httpx.HTTPError as exc:
                logger.warning("Scan trigger %s failed: %r", label, exc)

    await asyncio.sleep(min(timeout, 5))

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{litterbox_url}/api/results/risk/{md5}")
            if r.status_code == 200:
                risk = r.json()
                level = risk.get("risk_level", "unknown")
                score = risk.get("risk_score", "N/A")
                return NewCustomEventingMessageResponse(
                    Success=True,
                    Message=(
                        f"LitterBox scan complete: {level} risk "
                        f"(score={score}) | hash={md5} | "
                        f"results: {litterbox_url}/results/info/{md5}"
                    ),
                )
        except httpx.HTTPError:
            pass

    return NewCustomEventingMessageResponse(
        Success=True,
        Message=f"Scan triggered on LitterBox (md5={md5}). Results may still be processing.",
    )


async def get_verdict(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = msg.Inputs
        payload_uuid = inputs.get("payload_uuid", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")

        if not payload_uuid or not mythic_token:
            return NewCustomEventingMessageResponse(
                Success=False,
                Message="'payload_uuid' and 'mythic_api_token' required",
            )

        auth_headers = {"Authorization": f"Bearer {mythic_token}"}
        payload_int_id, _, _ = await _query_payload(auth_headers, payload_uuid)

        if not payload_int_id:
            return NewCustomEventingMessageResponse(
                Success=False,
                Message=f"Payload {payload_uuid!r} not found",
            )

        sphinx_tags = await _gql(auth_headers, _QUERY_TAGS, {
            "payload_id": payload_int_id,
            "source": "sphinx",
        })
        tags = sphinx_tags.get("tag", [])

        daedalus_tags_data = await _gql(auth_headers, _QUERY_TAGS, {
            "payload_id": payload_int_id,
            "source": "daedalus",
        })
        d_tags = daedalus_tags_data.get("tag", [])

        lines = []
        if tags:
            t = tags[0]
            tag_data = t.get("data", {})
            tagtype = t.get("tagtype", {})
            lines.append(f"Sphinx verdict: {tagtype.get('name', 'unknown')}")
            lines.append(f"  Risk level: {tag_data.get('risk_level', 'N/A')}")
            lines.append(f"  Risk score: {tag_data.get('risk_score', 'N/A')}")
            factors = tag_data.get("risk_factors", [])
            if factors:
                lines.append(f"  Risk factors: {', '.join(str(f) for f in factors[:5])}")
            edr_factors = tag_data.get("risk_factors_edr", [])
            if edr_factors:
                for ef in edr_factors[:3]:
                    lines.append(
                        f"  EDR ({ef.get('profile', '?')}): "
                        f"{ef.get('total_alerts', 0)} alerts"
                    )
            results_url = tag_data.get("results_url", "")
            if results_url:
                lines.append(f"  Results: {results_url}")
        else:
            lines.append("No Sphinx verdict found for this payload")

        if d_tags:
            dt = d_tags[0]
            dt_data = dt.get("data", {})
            dt_type = dt.get("tagtype", {})
            lines.append(f"Daedalus build: {dt_type.get('name', 'unknown')}")
            lines.append(f"  Provider: {dt_data.get('provider', 'N/A')}")
            lines.append(f"  Job: {dt_data.get('job', 'N/A')}")
            lines.append(f"  Build: #{dt_data.get('build_id', 'N/A')}")
            if dt_data.get("url"):
                lines.append(f"  URL: {dt_data['url']}")

        return NewCustomEventingMessageResponse(
            Success=True, Message="\n".join(lines),
        )

    except Exception as exc:
        logger.exception("Daedalus get_verdict failed")
        return NewCustomEventingMessageResponse(
            Success=False, Message=f"Daedalus error: {exc}",
        )


# ---------------------------------------------------------------------------
# Build polling
# ---------------------------------------------------------------------------

async def _poll_build(provider, job: str, build_id: str, timeout: int):
    from daedalus.providers.base import BuildResult

    interval = _POLL_START
    elapsed = 0.0

    while elapsed < timeout:
        await asyncio.sleep(interval)
        elapsed += interval

        result = await provider.get_build_status(job, build_id)
        logger.warning(
            "Poll %s #%s → %s (%.0fs elapsed)",
            provider.name, build_id, result.status.value, elapsed,
        )

        if result.status.is_terminal:
            return result

        interval = min(interval * _POLL_BACKOFF, _POLL_MAX)

    return BuildResult(
        provider=provider.name,
        build_id=build_id,
        status=BuildStatus.UNKNOWN,
        error=f"Polling timed out after {timeout}s",
    )


# ---------------------------------------------------------------------------
# Mythic helpers
# ---------------------------------------------------------------------------

async def _gql(headers: dict, query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        r = await client.post(
            MYTHIC_GRAPHQL,
            headers=headers,
            json={"query": query, "variables": variables},
        )
        r.raise_for_status()
        body = r.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body.get("data", {})


async def _query_payload(headers: dict, uuid: str):
    data = await _gql(headers, _QUERY_PAYLOAD, {"uuid": uuid})
    payloads = data.get("payload", [])
    if not payloads:
        return None, None, "payload.bin"
    p = payloads[0]
    fm = p.get("filemetum") or {}
    filename = _decode_bytea(fm.get("filename", "")) or "payload.bin"
    return p["id"], fm.get("agent_file_id"), filename


def _decode_bytea(val: str) -> str:
    if not val:
        return ""
    if val.startswith("\\x") or val.startswith("x"):
        hex_str = val.lstrip("\\").lstrip("x")
        try:
            return bytes.fromhex(hex_str).decode("utf-8", errors="replace")
        except ValueError:
            return val
    return val


async def _download_file(headers: dict, agent_file_id: str) -> bytes:
    async with httpx.AsyncClient(verify=False, timeout=60) as client:
        r = await client.get(
            f"{MYTHIC_SERVER}/direct/download/{agent_file_id}",
            headers=headers,
        )
        r.raise_for_status()
        return r.content


async def _tag_payload_with_build(
    headers: dict,
    payload_id: int,
    provider_name: str,
    job: str,
    build_result,
    extra_data: dict | None = None,
) -> None:
    status_str = build_result.status.value.upper()
    if build_result.status == BuildStatus.SUCCESS:
        label, color = "Daedalus: Build OK", "#4CAF50"
    elif build_result.status == BuildStatus.FAILURE:
        label, color = "Daedalus: Build Failed", "#f44336"
    else:
        label, color = f"Daedalus: {status_str}", "#FF9800"

    data = await _gql(headers, _QUERY_TAGTYPE, {"name": label})
    tagtypes = data.get("tagtype", [])
    if tagtypes:
        tagtype_id = tagtypes[0]["id"]
    else:
        result = await _gql(headers, _INSERT_TAGTYPE, {
            "name": label,
            "color": color,
            "description": "Daedalus CI/CD build result",
        })
        tagtype_id = result["insert_tagtype_one"]["id"]

    tag_data = {
        "provider": provider_name,
        "job": job,
        "build_id": build_result.build_id,
        "status": status_str,
        "duration_seconds": build_result.duration_seconds,
        "url": build_result.url,
        "error": build_result.error,
    }
    if extra_data:
        tag_data.update(extra_data)

    await _gql(headers, _INSERT_TAG, {
        "tagtype_id": tagtype_id,
        "payload_id": payload_id,
        "source": "daedalus",
        "url": build_result.url or "",
        "data": tag_data,
    })


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

_BUILD_PARAM_KEYS = {
    "language", "format", "output_format", "obfuscation",
    "shellcode_path", "scan", "scan_type", "ref", "workflow",
    "LANGUAGE", "FORMAT", "OUTPUT_FORMAT", "OBFUSCATION",
    "SHELLCODE_PATH", "SCAN", "SCAN_TYPE",
}


def _extract_build_params(inputs: dict) -> dict[str, str]:
    params = {}
    for key, val in inputs.items():
        if key.startswith("param_"):
            params[key[6:].upper()] = str(val)
        elif key in _BUILD_PARAM_KEYS:
            params[key.upper()] = str(val)
    return params


def _provider_kwargs_from_inputs(inputs: dict, provider_name: str) -> dict:
    prefix = f"{provider_name}_"
    kwargs = {}

    env_map = {
        "jenkins": {
            "url": "JENKINS_URL",
            "user": "JENKINS_USER",
            "token": "JENKINS_TOKEN",
        },
        "forgejo": {
            "url": "FORGEJO_URL",
            "token": "FORGEJO_TOKEN",
            "owner": "FORGEJO_OWNER",
            "repo": "FORGEJO_REPO",
        },
        "github": {
            "token": "GITHUB_TOKEN",
            "owner": "GITHUB_OWNER",
            "repo": "GITHUB_REPO",
            "api_base": "GITHUB_API_BASE",
        },
        "gitlab": {
            "url": "GITLAB_URL",
            "token": "GITLAB_TOKEN",
            "project_id": "GITLAB_PROJECT_ID",
        },
        "gitea": {
            "url": "GITEA_URL",
            "token": "GITEA_TOKEN",
            "owner": "GITEA_OWNER",
            "repo": "GITEA_REPO",
        },
    }

    mapping = env_map.get(provider_name, {})
    for param, env_var in mapping.items():
        val = (
            inputs.get(f"{prefix}{param}")
            or inputs.get(param)
            or os.getenv(env_var, "")
        )
        if val:
            kwargs[param] = val

    return kwargs


# ---------------------------------------------------------------------------
# Workflow registration
# ---------------------------------------------------------------------------

WORKFLOWS_DIR = Path(__file__).parent / "workflows"

_IMPORT_MUTATION = """
mutation ImportWorkflow(
    $contents: String!, $filename: String!,
    $container_name: String!, $delete_old_version: Boolean
) {
    eventingImportContainerWorkflow(
        contents: $contents,
        filename: $filename,
        container_name: $container_name,
        delete_old_version: $delete_old_version
    ) { status error eventgroup_id }
}
"""


async def _register_workflows(api_token: str) -> None:
    headers = {"Authorization": f"Bearer {api_token}"}
    yamls = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    if not yamls:
        logger.warning("No workflow YAML files found in %s", WORKFLOWS_DIR)
        return

    max_retries = 12
    retry_delay = 5.0

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        for yml in yamls:
            contents = yml.read_text()
            variables = {
                "contents": contents,
                "filename": yml.name,
                "container_name": "daedalus",
                "delete_old_version": True,
            }
            registered = False
            for attempt in range(1, max_retries + 1):
                try:
                    r = await client.post(
                        MYTHIC_GRAPHQL,
                        headers=headers,
                        json={"query": _IMPORT_MUTATION, "variables": variables},
                    )
                    body = r.json()
                    data = body.get("data", {}).get(
                        "eventingImportContainerWorkflow", {},
                    )
                    if data.get("status") == "success":
                        logger.warning(
                            "Registered workflow %s (id=%s)",
                            yml.name, data.get("eventgroup_id"),
                        )
                        registered = True
                        break
                    else:
                        logger.error(
                            "Failed to register %s: %s",
                            yml.name, data.get("error") or body,
                        )
                        break
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if attempt < max_retries:
                        logger.warning(
                            "Cannot reach Mythic GraphQL for %s (attempt %d/%d): %r, retrying in %.0fs",
                            yml.name, attempt, max_retries, exc, retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(
                            "Failed to register workflow %s after %d attempts: %r",
                            yml.name, max_retries, exc,
                        )
                except Exception as exc:
                    logger.error("Failed to register workflow %s: %r", yml.name, exc)
                    break


# ---------------------------------------------------------------------------
# Eventing class
# ---------------------------------------------------------------------------

class DaedalusEventing(Eventing):
    name = "daedalus"
    description = (
        "Trigger CI/CD builds (Jenkins, Forgejo, GitHub, GitLab, Gitea) "
        "from Mythic and tag payloads with results"
    )
    custom_functions = [
        CustomFunctionDefinition(
            Name="trigger_build",
            Description=(
                "Trigger a CI/CD build, optionally poll for completion, "
                "and tag the payload with the result"
            ),
            Function=trigger_build,
        ),
        CustomFunctionDefinition(
            Name="check_status",
            Description=(
                "Check the status of a CI/CD build "
                "by provider, job, and build ID"
            ),
            Function=check_status,
        ),
        CustomFunctionDefinition(
            Name="list_configs",
            Description=(
                "List available CI/CD jobs/pipelines "
                "on the configured provider"
            ),
            Function=list_configs,
        ),
        CustomFunctionDefinition(
            Name="download_artifact",
            Description=(
                "Download a build artifact from CI/CD "
                "and upload it to Mythic"
            ),
            Function=download_artifact,
        ),
        CustomFunctionDefinition(
            Name="scan_payload",
            Description=(
                "Submit a payload to LitterBox for scanning "
                "via Sphinx or direct API call"
            ),
            Function=scan_payload,
        ),
        CustomFunctionDefinition(
            Name="get_verdict",
            Description=(
                "Retrieve Sphinx scan verdict and Daedalus build tags "
                "for a payload"
            ),
            Function=get_verdict,
        ),
    ]

    async def on_container_start(
        self, message: ContainerOnStartMessage,
    ) -> ContainerOnStartMessageResponse:
        if message.APIToken:
            await _register_workflows(message.APIToken)
        return ContainerOnStartMessageResponse(ContainerName=self.name)
