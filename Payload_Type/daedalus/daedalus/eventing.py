from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import httpx
from pathlib import Path


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
from daedalus_ca.shared import select_artifact

logger = logging.getLogger("daedalus")

_UNRESOLVED_TEMPLATE = re.compile(r"^\{\{.*\}\}$")

_STARTUP_API_TOKEN: str = ""
_YAML_ENV_DEFAULTS: dict[str, str] = {}
_VERIFY_SSL: bool = os.getenv("DAEDALUS_VERIFY_SSL", "").lower() in ("1", "true", "yes")


def _load_yaml_env_defaults() -> dict[str, str]:
    """Merge all workflow YAML environment blocks into one defaults dict.

    Called once at container startup. Values from the YAML files serve as
    last-resort defaults when Mythic fails to resolve templates and doesn't
    pass the environment block through msg.Environment.
    """
    import yaml

    merged: dict[str, str] = {}
    workflows_dir = Path(__file__).parent / "workflows"
    for yml in sorted(workflows_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(yml.read_text())
            env_block = doc.get("environment") or {}
            for k, v in env_block.items():
                if v and str(v) and k not in merged:
                    merged[k] = str(v)
        except Exception:
            pass
    return merged


def _resolve_inputs(msg: NewCustomEventingMessage) -> dict:
    """Build a resolved inputs dict from the message.

    Mythic should interpolate ``{{env.VAR}}``, ``{{trigger.*}}``, and
    ``{{mythic.*}}`` before the value reaches a custom function.  When it
    doesn't (known bug in < v4.0), the literal template string arrives
    instead.

    This function:
      1. Strips unresolved ``{{...}}`` template strings to empty.
      2. Fills empty values from ``msg.Environment`` (the workflow's env block).
      3. Injects the startup API token when ``mythic_api_token`` is missing.
      4. Pulls ``payload_uuid`` from ``msg.ActionData`` for trigger-sourced events.
    """
    raw = msg.Inputs or {}
    env = msg.Environment or {}
    action = msg.ActionData or {}

    # Map from input keys to their corresponding env-block keys.
    _INPUT_TO_ENV = {
        "provider": "PROVIDER",
        "job": "JOB",
        "poll": "POLL",
        "timeout": "TIMEOUT",
        "language": "LANGUAGE",
        "output_format": "OUTPUT_FORMAT",
        "obfuscation": "OBFUSCATION",
        "payload_uuid": "PAYLOAD_UUID",
        "litterbox_url": "LITTERBOX_URL",
        "scan_type": "SCAN_TYPE",
        "edr_profile": "EDR_PROFILE",
        "method": "SCAN_METHOD",
        "build_id": "BUILD_ID",
        "artifact_name": "ARTIFACT_NAME",
        "include_log": "INCLUDE_LOG",
        "calypso_mode": "CALYPSO_MODE",
        "packer_preset": "PACKER_PRESET",
        "packer_flags": "PACKER_FLAGS",
        "signing_profile": "SIGNING_PROFILE",
        "pe_sanitise": "PE_SANITISE",
        "target_arch": "TARGET_ARCH",
        "operator_id": "OPERATOR_ID",
        "campaign_tag": "CAMPAIGN_TAG",
        "litterbox_scan": "LITTERBOX_SCAN",
        "shellcode_source": "SHELLCODE_SOURCE",
        "mythic_payload_uuid": "MYTHIC_PAYLOAD_UUID",
        "repo_url": "REPO_URL",
        "repo_token": "REPO_TOKEN",
        "source_path": "SOURCE_PATH",
        "ref": "REF",
        # Provider connection details (keyed with provider prefix so
        # _provider_kwargs_from_inputs finds them via inputs.get("jenkins_url") etc.)
        "jenkins_url": "JENKINS_URL",
        "jenkins_user": "JENKINS_USER",
        "jenkins_token": "JENKINS_TOKEN",
        "forgejo_url": "FORGEJO_URL",
        "forgejo_token": "FORGEJO_TOKEN",
        "forgejo_owner": "FORGEJO_OWNER",
        "forgejo_repo": "FORGEJO_REPO",
        "github_token": "GITHUB_TOKEN",
        "github_owner": "GITHUB_OWNER",
        "github_repo": "GITHUB_REPO",
        "github_api_base": "GITHUB_API_BASE",
        "gitlab_url": "GITLAB_URL",
        "gitlab_token": "GITLAB_TOKEN",
        "gitlab_project_id": "GITLAB_PROJECT_ID",
        "gitea_url": "GITEA_URL",
        "gitea_token": "GITEA_TOKEN",
        "gitea_owner": "GITEA_OWNER",
        "gitea_repo": "GITEA_REPO",
    }

    unresolved = []
    out: dict = {}
    for key, val in raw.items():
        if isinstance(val, str) and _UNRESOLVED_TEMPLATE.match(val):
            unresolved.append(key)
            out[key] = ""
        else:
            out[key] = val

    if unresolved:
        logger.info("Unresolved templates cleared: %s", unresolved)

    # Fill blanks from the workflow environment block, then from YAML defaults.
    filled = []
    for input_key, env_key in _INPUT_TO_ENV.items():
        if not out.get(input_key):
            val = env.get(env_key) or _YAML_ENV_DEFAULTS.get(env_key) or ""
            if val:
                out[input_key] = val
                source = "env" if env.get(env_key) else "yaml"
                filled.append(f"{input_key}={env_key}({source})")

    if filled:
        logger.info("Inputs filled from defaults: %s", filled)

    # API token: prefer resolved input, then startup token.
    if not out.get("mythic_api_token") and _STARTUP_API_TOKEN:
        out["mythic_api_token"] = _STARTUP_API_TOKEN
        logger.info("Using startup API token as fallback")

    # Trigger-sourced payload UUID (payload_build_finish events).
    logger.warning(
        "payload_uuid resolution: input=%r, ActionData keys=%s",
        out.get("payload_uuid", ""),
        list((action or {}).keys()),
    )
    if not out.get("payload_uuid"):
        trigger_uuid = (
            action.get("payload_uuid")
            or action.get("uuid")
            or action.get("payload", {}).get("uuid", "")
        )
        if trigger_uuid:
            out["payload_uuid"] = trigger_uuid
            logger.warning("Payload UUID from trigger ActionData: %s", trigger_uuid)
        else:
            logger.warning("No payload_uuid found in inputs or ActionData")

    logger.info(
        "Resolved inputs: %s",
        {k: (v[:30] + "..." if isinstance(v, str) and len(v) > 30 else v)
         for k, v in out.items() if v},
    )
    return out


def _ok(message: str, **extra) -> NewCustomEventingMessageResponse:
    return NewCustomEventingMessageResponse(Success=True, StdOut=message, **extra)


def _err(message: str, **extra) -> NewCustomEventingMessageResponse:
    return NewCustomEventingMessageResponse(Success=False, StdErr=message, **extra)


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

_INSERT_FILE_TAG = """
mutation DaedalusInsertFileTag(
    $tagtype_id: Int!, $filemeta_id: Int!,
    $source: String!, $url: String!, $data: jsonb!
) {
    insert_tag_one(object: {
        tagtype_id: $tagtype_id,
        filemeta_id: $filemeta_id,
        source: $source,
        url: $url,
        data: $data
    }) { id }
}
"""

_QUERY_FILEMETA = """
query DaedalusFilemeta($agent_file_id: String!) {
    filemeta(where: {agent_file_id: {_eq: $agent_file_id}}) { id }
}
"""

async def _upload_file(headers: dict, filename: str, contents: bytes) -> str:
    """Upload a file to Mythic via the REST webhook. Returns agent_file_id."""
    async with httpx.AsyncClient(verify=_VERIFY_SSL, timeout=30) as client:
        r = await client.post(
            f"{MYTHIC_SERVER}/api/v1.4/task_upload_file_webhook",
            headers={"Authorization": headers.get("Authorization", "")},
            files={"file": (filename, contents, "application/octet-stream")},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Upload failed: {data.get('error', data)}")
        logger.info("Uploaded %s (%d bytes) → %s", filename, len(contents), data["agent_file_id"])
        return data["agent_file_id"]


# ---------------------------------------------------------------------------
# Custom function: trigger_build
# ---------------------------------------------------------------------------

async def trigger_build(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        language = inputs.get("language", "").lower().strip()
        mythic_token = inputs.get("mythic_api_token", "")
        payload_uuid = inputs.get("payload_uuid", "").strip()
        poll_build = inputs.get("poll", "true").lower() in ("true", "1", "yes")
        raw_timeout = inputs.get("timeout", "300")
        try:
            poll_timeout = int(raw_timeout)
        except (ValueError, TypeError):
            poll_timeout = 300

        if not job and language:
            if not language.startswith("loader-"):
                language = f"loader-{language}"
            job = language
            inputs["language"] = language
            logger.info("Job auto-resolved from language → %s", job)

        if not job:
            return _err("'job' input required (CI job/pipeline name)")

        build_params = _extract_build_params(inputs)
        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        payload_int_id = None
        auth_headers = {}
        if payload_uuid and mythic_token:
            build_params["MYTHIC_PAYLOAD_UUID"] = payload_uuid
            auth_headers = {"Authorization": f"Bearer {mythic_token}"}
            payload_int_id, agent_file_id, filename = await _query_payload(auth_headers, payload_uuid)
            if agent_file_id:
                payload_bytes = await _download_file(auth_headers, agent_file_id)
                build_params["SHELLCODE_SOURCE"] = f"mythic:{payload_uuid}"
                build_params["PAYLOAD_SIZE"] = str(len(payload_bytes))
                logger.info(
                    "Payload %s resolved: %d bytes (%s)",
                    payload_uuid, len(payload_bytes), filename,
                )

        logger.info(
            "Triggering build on %s - job=%s params=%s provider_kwargs=%s",
            provider_name, job, list(build_params.keys()),
            {k: (v[:20] + "..." if isinstance(v, str) and len(v) > 20 else v)
             for k, v in provider_kwargs.items()},
        )
        result = await provider.trigger_build(job, build_params)
        logger.info(
            "Build trigger result: status=%s build_id=%s url=%s error=%s",
            result.status.value, result.build_id, result.url, result.error,
        )

        if result.status == BuildStatus.FAILURE:
            return _err(f"Build trigger failed: {result.error}")

        if not result.build_id:
            return _ok(f"Build dispatched on {provider_name} (no build ID resolved yet). URL: {result.url}")

        if not poll_build:
            return _ok(f"Build triggered: {provider_name} #{result.build_id} - {result.url}")

        logger.info(
            "Polling build %s #%s (timeout=%ds)", provider_name, result.build_id, poll_timeout,
        )
        final = await _poll_build(provider, job, result.build_id, poll_timeout)
        logger.info(
            "Build poll complete: status=%s build_id=%s duration=%.0fs error=%s",
            final.status.value, final.build_id,
            final.duration_seconds or 0, final.error,
        )

        if final.status == BuildStatus.SUCCESS and payload_int_id and auth_headers:
            logger.info("Tagging payload %d with build result", payload_int_id)
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
        summary = " | ".join(p for p in msg_parts if p)
        if final.status == BuildStatus.SUCCESS:
            return _ok(summary)
        return _err(summary)

    except Exception as exc:
        logger.exception("Daedalus trigger_build failed")
        return _err(f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: check_status
# ---------------------------------------------------------------------------

async def check_status(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        language = inputs.get("language", "").lower().strip()
        build_id = inputs.get("build_id", "").strip()

        if not job and language:
            if not language.startswith("loader-"):
                language = f"loader-{language}"
            job = language
            inputs["language"] = language
            logger.info("Job auto-resolved from language → %s", job)

        if not job or not build_id:
            return _err("'job' (or 'language') and 'build_id' inputs required")

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

        return _ok(message)

    except Exception as exc:
        logger.exception("Daedalus check_status failed")
        return _err(f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: list_configs
# ---------------------------------------------------------------------------

async def list_configs(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()

        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        jobs = await provider.list_jobs()
        if not jobs:
            return _ok(f"No jobs found on {provider_name} (or not accessible)")

        lines = [f"Available jobs on {provider_name}:"]
        for j in jobs:
            status = j.get("status") or j.get("conclusion") or ""
            lines.append(f"  - {j['name']} [{status}] {j.get('url', '')}")

        return _ok("\n".join(lines))

    except Exception as exc:
        logger.exception("Daedalus list_configs failed")
        return _err(f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: download_artifact
# ---------------------------------------------------------------------------

async def download_artifact(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        language = inputs.get("language", "").lower().strip()
        build_id = inputs.get("build_id", "").strip()
        artifact_name = inputs.get("artifact_name", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")
        payload_uuid = inputs.get("payload_uuid", "").strip()

        if not job and language:
            if not language.startswith("loader-"):
                language = f"loader-{language}"
            job = language
            inputs["language"] = language
            logger.info("Job auto-resolved from language → %s", job)

        if not job or not build_id:
            return _err("'job' (or 'language') and 'build_id' inputs required")

        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        artifact_bytes = await provider.download_artifact(
            job, build_id, artifact_name or "",
        )

        if mythic_token and artifact_bytes:
            auth_headers = {"Authorization": f"Bearer {mythic_token}"}
            fname = artifact_name or f"daedalus-{provider_name}-{build_id}"
            file_id = await _upload_file(auth_headers, fname, artifact_bytes)

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

            return _ok(
                f"Artifact downloaded ({len(artifact_bytes)} bytes) "
                f"and uploaded to Mythic (file_id={file_id})"
            )

        return _ok(
            f"Artifact downloaded: {len(artifact_bytes)} bytes "
            f"(no Mythic token - not uploaded)"
        )

    except Exception as exc:
        logger.exception("Daedalus download_artifact failed")
        return _err(f"Daedalus error: {exc}")


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
        inputs = _resolve_inputs(msg)
        payload_uuid = inputs.get("payload_uuid", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")
        litterbox_url = inputs.get("litterbox_url") or os.getenv("LITTERBOX_URL", "")
        scan_type = inputs.get("scan_type", "all").lower()
        edr_profile = inputs.get("edr_profile", "").strip()
        timeout = inputs.get("timeout", "120")
        method = inputs.get("method", "sphinx").lower()

        if not payload_uuid:
            return _err("'payload_uuid' input required")
        if not mythic_token:
            return _err("'mythic_api_token' input required")

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
            return _err(f"Unknown scan method: {method!r}. Use 'sphinx' or 'direct'")

        return result

    except Exception as exc:
        logger.exception("Daedalus scan_payload failed")
        return _err(f"Daedalus scan error: {exc}")


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
            return _ok(f"Sphinx scan complete: {output}")
        else:
            return _err(f"Sphinx scan failed: {error or output or status}")

    except RuntimeError as exc:
        if "not found" in str(exc).lower() or "container" in str(exc).lower():
            return _err(
                "Sphinx container not found. "
                "Install Sphinx or use method='direct' with LITTERBOX_URL set."
            )
        raise


async def _litterbox_upload_and_scan(
    litterbox_url: str,
    filename: str,
    file_bytes: bytes,
    scan_type: str,
    edr_profile: str,
    poll_delay: int = 5,
    max_poll_attempts: int = 6,
) -> tuple[str, dict]:
    """Upload to LitterBox, trigger scans, poll for results.

    Returns (md5, risk_data) where risk_data may be empty if results
    aren't ready yet.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{litterbox_url}/upload",
            files={"file": (filename, file_bytes, "application/octet-stream")},
        )
        if r.status_code != 200:
            raise RuntimeError(f"LitterBox upload failed: HTTP {r.status_code} - {r.text[:300]}")
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
                logger.info("Triggered %s scan", label)
            except httpx.HTTPError as exc:
                logger.warning("Scan trigger %s failed: %r", label, exc)

    risk_data = {}
    for attempt in range(max_poll_attempts):
        await asyncio.sleep(poll_delay)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{litterbox_url}/api/results/risk/{md5}")
                if r.status_code == 200:
                    data = r.json()
                    if data.get("risk_level"):
                        return md5, data
        except httpx.HTTPError:
            pass

    return md5, risk_data


async def _scan_direct_litterbox(
    headers: dict,
    payload_uuid: str,
    litterbox_url: str,
    scan_type: str,
    edr_profile: str,
    timeout: int,
) -> NewCustomEventingMessageResponse:
    if not litterbox_url:
        return _err("'litterbox_url' required for direct scan (or set LITTERBOX_URL)")

    litterbox_url = litterbox_url.rstrip("/")
    payload_int_id, agent_file_id, filename = await _query_payload(headers, payload_uuid)

    if not agent_file_id:
        return _err(f"Payload {payload_uuid!r} not found in Mythic")

    payload_bytes = await _download_file(headers, agent_file_id)

    try:
        md5, risk_data = await _litterbox_upload_and_scan(
            litterbox_url, filename, payload_bytes,
            scan_type, edr_profile, poll_delay=min(timeout, 5),
        )
    except RuntimeError as exc:
        return _err(str(exc))

    if risk_data:
        level = risk_data.get("risk_level", "unknown")
        score = risk_data.get("risk_score", "N/A")
        return _ok(
            f"LitterBox scan complete: {level} risk "
            f"(score={score}) | hash={md5} | "
            f"results: {litterbox_url}/results/info/{md5}"
        )

    return _ok(f"Scan triggered on LitterBox (md5={md5}). Results may still be processing.")


async def get_verdict(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        payload_uuid = inputs.get("payload_uuid", "").strip()
        mythic_token = inputs.get("mythic_api_token", "")

        if not payload_uuid or not mythic_token:
            return _err("'payload_uuid' and 'mythic_api_token' required")

        auth_headers = {"Authorization": f"Bearer {mythic_token}"}
        payload_int_id, _, _ = await _query_payload(auth_headers, payload_uuid)

        if not payload_int_id:
            return _err(f"Payload {payload_uuid!r} not found")

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

        return _ok("\n".join(lines))

    except Exception as exc:
        logger.exception("Daedalus get_verdict failed")
        return _err(f"Daedalus error: {exc}")


# ---------------------------------------------------------------------------
# Custom function: build_and_scan (unified pipeline)
# ---------------------------------------------------------------------------

async def build_and_scan(msg: NewCustomEventingMessage) -> NewCustomEventingMessageResponse:
    try:
        inputs = _resolve_inputs(msg)
        provider_name = (inputs.get("provider") or os.getenv("DAEDALUS_PROVIDER", "jenkins")).lower()
        job = inputs.get("job") or os.getenv("DAEDALUS_JOB", "")
        language = inputs.get("language", "").lower().strip()
        mythic_token = inputs.get("mythic_api_token", "")
        payload_uuid = inputs.get("payload_uuid", "").strip()
        litterbox_url = inputs.get("litterbox_url") or os.getenv("LITTERBOX_URL", "")
        scan_type = inputs.get("scan_type", "all").lower()
        edr_profile = inputs.get("edr_profile", "").strip()

        raw_timeout = inputs.get("timeout", "300")
        try:
            poll_timeout = int(raw_timeout)
        except (ValueError, TypeError):
            poll_timeout = 300

        if not job and language:
            if not language.startswith("loader-"):
                language = f"loader-{language}"
            job = language
            inputs["language"] = language
            logger.info("Job auto-resolved from language → %s", job)

        if not job:
            return _err("'job' (or 'language') input required")
        if not mythic_token:
            return _err("'mythic_api_token' input required")

        auth_headers = {"Authorization": f"Bearer {mythic_token}"}

        # --- Phase 1: Resolve source payload and trigger build ---
        build_params = _extract_build_params(inputs)
        provider_kwargs = _provider_kwargs_from_inputs(inputs, provider_name)
        provider = get_provider(provider_name, **provider_kwargs)

        payload_int_id = None
        if payload_uuid:
            build_params["MYTHIC_PAYLOAD_UUID"] = payload_uuid
            payload_int_id, agent_file_id, filename = await _query_payload(auth_headers, payload_uuid)
            if agent_file_id:
                payload_bytes = await _download_file(auth_headers, agent_file_id)
                build_params["SHELLCODE_SOURCE"] = f"mythic:{payload_uuid}"
                build_params["PAYLOAD_SIZE"] = str(len(payload_bytes))
                logger.info(
                    "Payload %s resolved: %d bytes (%s)",
                    payload_uuid, len(payload_bytes), filename,
                )

        logger.info(
            "build_and_scan: triggering %s job=%s params=%s",
            provider_name, job, list(build_params.keys()),
        )
        result = await provider.trigger_build(job, build_params)

        if result.status == BuildStatus.FAILURE:
            return _err(f"Build trigger failed: {result.error}")
        if not result.build_id:
            return _err(f"Build dispatched but no build ID resolved. URL: {result.url}")

        logger.info("build_and_scan: polling build #%s", result.build_id)
        final = await _poll_build(provider, job, result.build_id, poll_timeout)
        logger.info(
            "build_and_scan: build %s #%s → %s (%.0fs)",
            provider_name, final.build_id, final.status.value,
            final.duration_seconds or 0,
        )

        if final.status != BuildStatus.SUCCESS:
            if payload_int_id:
                await _tag_payload_with_build(
                    auth_headers, payload_int_id, provider_name, job, final,
                )
            status_label = final.status.value.upper()
            return _err(
                f"Build {status_label}: {provider_name} #{final.build_id}"
                + (f" | Error: {final.error}" if final.error else "")
            )

        # --- Tag source payload with build result ---
        if payload_int_id:
            await _tag_payload_with_build(
                auth_headers, payload_int_id, provider_name, job, final,
            )

        # --- Phase 2: Download artifact from CI and upload to Mythic ---
        artifact_name = inputs.get("artifact_name", "").strip()

        artifacts = await provider.list_artifacts(job, final.build_id)
        if not artifacts:
            logger.info("build_and_scan: no artifacts found for build #%s", final.build_id)
            return _ok(
                f"Build SUCCESS: {provider_name} #{final.build_id} "
                f"(duration={final.duration_seconds:.0f}s) | "
                f"No artifacts found to scan"
            )

        logger.info(
            "build_and_scan: artifacts available: %s",
            [a.get("relativePath") or a.get("fileName") for a in artifacts],
        )

        if artifact_name:
            target = next((a for a in artifacts if a.get("relativePath") == artifact_name
                          or a.get("fileName") == artifact_name), None)
        else:
            target = select_artifact(artifacts)

        if not target:
            return _err(f"Artifact {artifact_name!r} not found in build #{final.build_id}")

        artifact_path = target.get("relativePath") or target.get("fileName", "artifact")
        logger.info("build_and_scan: downloading artifact %s", artifact_path)
        artifact_bytes = await provider.download_artifact(job, final.build_id, artifact_path)
        logger.info("build_and_scan: downloaded %d bytes", len(artifact_bytes))

        upload_filename = artifact_path.split("/")[-1]
        uploaded_file_id = await _upload_file(auth_headers, upload_filename, artifact_bytes)
        logger.info(
            "build_and_scan: uploaded to Mythic as %s (file_id=%s)",
            upload_filename, uploaded_file_id,
        )

        # --- Phase 3: Scan the artifact via LitterBox ---
        if not litterbox_url:
            return _ok(
                f"Build SUCCESS: {provider_name} #{final.build_id} "
                f"(duration={final.duration_seconds:.0f}s) | "
                f"Artifact uploaded to Mythic (file_id={uploaded_file_id}) | "
                f"No LITTERBOX_URL set - skipping scan"
            )

        litterbox_url = litterbox_url.rstrip("/")
        logger.info("build_and_scan: uploading artifact to LitterBox at %s", litterbox_url)

        try:
            md5, risk_data = await _litterbox_upload_and_scan(
                litterbox_url, upload_filename, artifact_bytes,
                scan_type, edr_profile,
                poll_delay=min(poll_timeout, 10),
            )
        except RuntimeError as exc:
            return _ok(
                f"Build SUCCESS: {provider_name} #{final.build_id} | "
                f"Artifact uploaded to Mythic (file_id={uploaded_file_id}) | "
                f"{exc}"
            )

        level = (risk_data.get("risk_level") or "unknown").lower()
        score = risk_data.get("risk_score", "N/A")

        # --- Tag the uploaded artifact file with scan verdict ---
        _SCAN_VERDICT_MAP = {
            "low":      ("Daedalus: Scan Clean",         "#4CAF50"),
            "medium":   ("Daedalus: Scan Medium Risk",   "#FF9800"),
            "high":     ("Daedalus: Scan High Risk",     "#f44336"),
            "critical": ("Daedalus: Scan Critical Risk", "#9C27B0"),
        }
        tag_label, tag_color = _SCAN_VERDICT_MAP.get(level, ("Daedalus: Scan Unknown", "#9E9E9E"))

        try:
            fm_data = await _gql(auth_headers, _QUERY_FILEMETA, {"agent_file_id": uploaded_file_id})
            fm_rows = fm_data.get("filemeta", [])
            filemeta_int_id = fm_rows[0]["id"] if fm_rows else None

            if filemeta_int_id:
                data = await _gql(auth_headers, _QUERY_TAGTYPE, {"name": tag_label})
                tagtypes = data.get("tagtype", [])
                if tagtypes:
                    tagtype_id = tagtypes[0]["id"]
                else:
                    result = await _gql(auth_headers, _INSERT_TAGTYPE, {
                        "name": tag_label, "color": tag_color,
                        "description": "Daedalus LitterBox scan verdict",
                    })
                    tagtype_id = result["insert_tagtype_one"]["id"]

                scan_tag_data = {
                    "risk_score": risk_data.get("risk_score"),
                    "risk_level": risk_data.get("risk_level"),
                    "risk_factors": risk_data.get("risk_factors", []),
                    "hash": md5,
                    "scan_type": scan_type,
                    "artifact_file_id": uploaded_file_id,
                    "artifact_name": upload_filename,
                    "results_url": f"{litterbox_url}/results/info/{md5}",
                }
                await _gql(auth_headers, _INSERT_FILE_TAG, {
                    "tagtype_id": tagtype_id,
                    "filemeta_id": filemeta_int_id,
                    "source": "daedalus",
                    "url": f"{litterbox_url}/results/info/{md5}",
                    "data": scan_tag_data,
                })
                logger.info(
                    "build_and_scan: tagged file %s (filemeta_id=%d) with %s",
                    uploaded_file_id, filemeta_int_id, tag_label,
                )

                # Also tag the source payload with the scan verdict
                if payload_int_id:
                    await _gql(auth_headers, _INSERT_TAG, {
                        "tagtype_id": tagtype_id,
                        "payload_id": payload_int_id,
                        "source": "daedalus",
                        "url": f"{litterbox_url}/results/info/{md5}",
                        "data": scan_tag_data,
                    })
                    logger.info(
                        "build_and_scan: tagged payload (id=%d) with %s",
                        payload_int_id, tag_label,
                    )
            else:
                logger.warning("build_and_scan: filemeta not found for %s, skipping tag", uploaded_file_id)
        except Exception as tag_exc:
            logger.warning("build_and_scan: scan tagging failed: %r", tag_exc)

        if risk_data:
            return _ok(
                f"Build SUCCESS: {provider_name} #{final.build_id} "
                f"(duration={final.duration_seconds:.0f}s) | "
                f"Artifact: {upload_filename} ({len(artifact_bytes)} bytes, "
                f"file_id={uploaded_file_id}) | "
                f"LitterBox: {level} risk (score={score}) | "
                f"Results: {litterbox_url}/results/info/{md5}"
            )

        return _ok(
            f"Build SUCCESS: {provider_name} #{final.build_id} "
            f"(duration={final.duration_seconds:.0f}s) | "
            f"Artifact: {upload_filename} ({len(artifact_bytes)} bytes, "
            f"file_id={uploaded_file_id}) | "
            f"Scan triggered (md5={md5}), results may still be processing: "
            f"{litterbox_url}/results/info/{md5}"
        )

    except Exception as exc:
        logger.exception("Daedalus build_and_scan failed")
        return _err(f"Daedalus error: {exc}")


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
        logger.info(
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
    op_name = query.strip().split("(")[0].split("{")[0].split()[-1] if query.strip() else "unknown"
    logger.info("GraphQL %s → %s", op_name, MYTHIC_GRAPHQL)
    async with httpx.AsyncClient(verify=_VERIFY_SSL, timeout=15) as client:
        r = await client.post(
            MYTHIC_GRAPHQL,
            headers=headers,
            json={"query": query, "variables": variables},
        )
        r.raise_for_status()
        body = r.json()
        if "errors" in body:
            logger.error("GraphQL errors: %s", body["errors"])
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
    async with httpx.AsyncClient(verify=_VERIFY_SSL, timeout=60) as client:
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
    "calypso_mode", "packer_preset", "packer_flags", "signing_profile", "pe_sanitise",
    "target_arch", "operator_id", "campaign_tag",
    "litterbox_scan", "shellcode_source", "mythic_payload_uuid",
    "repo_url", "repo_token", "source_path",
}


def _extract_build_params(inputs: dict) -> dict[str, str]:
    params = {}
    for key, val in inputs.items():
        str_val = str(val)
        if not str_val:
            continue
        if key.startswith("param_"):
            params[key[6:].upper()] = str_val
        elif key.lower() in _BUILD_PARAM_KEYS:
            params[key.upper()] = str_val
    return params


_SECRET_KEY_ENV_FALLBACK = {
    "jenkins": "JENKINS_API_KEY",
    "github": "GITHUB_API_KEY",
    "gitlab": "GITLAB_API_KEY",
    "forgejo": "FORGEJO_API_KEY",
    "gitea": "GITEA_API_KEY",
}


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
        if not val and param == "token":
            secret_env = _SECRET_KEY_ENV_FALLBACK.get(provider_name)
            if secret_env:
                val = os.getenv(secret_env, "")
                if val:
                    logger.info("Token resolved from %s env var", secret_env)
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
        logger.info("No workflow YAML files found in %s", WORKFLOWS_DIR)
        return

    max_retries = 12
    retry_delay = 5.0

    async with httpx.AsyncClient(verify=_VERIFY_SSL, timeout=15) as client:
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
                        logger.info(
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
        CustomFunctionDefinition(
            Name="build_and_scan",
            Description=(
                "Unified pipeline: trigger build, poll, download artifact, "
                "upload to Mythic, and scan via LitterBox"
            ),
            Function=build_and_scan,
        ),
    ]

    async def on_container_start(
        self, message: ContainerOnStartMessage,
    ) -> ContainerOnStartMessageResponse:
        global _STARTUP_API_TOKEN, _YAML_ENV_DEFAULTS
        _YAML_ENV_DEFAULTS = _load_yaml_env_defaults()
        logger.info("Loaded YAML env defaults: %s", list(_YAML_ENV_DEFAULTS.keys()))
        if message.APIToken:
            _STARTUP_API_TOKEN = message.APIToken
            await _register_workflows(message.APIToken)
        return ContainerOnStartMessageResponse(ContainerName=self.name)
