from __future__ import annotations

import asyncio
import logging
import re

import httpx

from daedalus.providers.base import BuildResult, BuildStatus, CIProvider

logger = logging.getLogger("daedalus.jenkins")

_STATUS_MAP = {
    "SUCCESS": BuildStatus.SUCCESS,
    "FAILURE": BuildStatus.FAILURE,
    "ABORTED": BuildStatus.CANCELLED,
    "UNSTABLE": BuildStatus.SUCCESS,
    None: BuildStatus.RUNNING,
}


class JenkinsProvider(CIProvider):
    name = "jenkins"

    def __init__(
        self,
        url: str = "",
        user: str = "",
        token: str = "",
        verify_ssl: bool = False,
        **_kwargs,
    ):
        self.base_url = url.rstrip("/")
        self.auth = (user, token) if user and token else None
        self.verify_ssl = verify_ssl
        logger.warning(
            "JenkinsProvider init: base_url=%r auth=%s",
            self.base_url, "set" if self.auth else "none",
        )

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def _fetch_crumb(self, client: httpx.AsyncClient) -> None:
        try:
            r = await client.get(f"{self.base_url}/crumbIssuer/api/json")
            if r.status_code == 200:
                data = r.json()
                header = data.get("crumbRequestField", "Jenkins-Crumb")
                value = data.get("crumb", "")
                if value:
                    client.headers[header] = value
                    logger.info("Fetched Jenkins crumb: %s", header)
            elif r.status_code == 404:
                logger.info("Jenkins CSRF protection disabled")
            else:
                logger.warning("Crumb fetch failed: HTTP %d", r.status_code)
        except httpx.HTTPError as exc:
            logger.warning("Crumb fetch error: %r", exc)

    async def _get_job_params(self, client: httpx.AsyncClient, job_path: str) -> set[str] | None:
        url = f"{self.base_url}/{job_path}/api/json?tree=property[parameterDefinitions[name]]"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            for prop in r.json().get("property", []):
                defs = prop.get("parameterDefinitions")
                if defs is not None:
                    names = {d["name"] for d in defs if "name" in d}
                    logger.info("Job accepts parameters: %s", sorted(names))
                    return names
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("Failed to fetch job params: %r", exc)
        return None

    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        job_path = _job_path(job)

        async with self._client() as client:
            await self._fetch_crumb(client)
            if parameters:
                known = await self._get_job_params(client, job_path)
                if known is not None:
                    dropped = {k for k in parameters if k not in known}
                    if dropped:
                        logger.warning("Dropping params unknown to job: %s", sorted(dropped))
                        parameters = {k: v for k, v in parameters.items() if k in known}

            if parameters:
                endpoint = f"{self.base_url}/{job_path}/buildWithParameters"
                logger.warning("POST %s (params: %s)", endpoint, list(parameters.keys()))
                r = await client.post(endpoint, data=parameters)
            else:
                endpoint = f"{self.base_url}/{job_path}/build"
                logger.warning("POST %s (no params)", endpoint)
                r = await client.post(endpoint)

            logger.warning(
                "Jenkins trigger response: HTTP %d, Location=%s",
                r.status_code, r.headers.get("Location", "(none)"),
            )

            if r.status_code == 400 and "not parameterized" in r.text:
                logger.warning(
                    "Job not parameterized yet (first run?), retrying with /build"
                )
                endpoint = f"{self.base_url}/{job_path}/build"
                r = await client.post(endpoint)
                logger.warning(
                    "Jenkins /build fallback: HTTP %d, Location=%s",
                    r.status_code, r.headers.get("Location", "(none)"),
                )

            if r.status_code not in (200, 201, 302):
                return BuildResult(
                    provider=self.name, build_id="", status=BuildStatus.FAILURE,
                    error=f"Trigger failed: HTTP {r.status_code} - {r.text[:500]}",
                )

            queue_url = r.headers.get("Location", "")
            logger.warning("Resolving queue item: %s", queue_url)
            build_id = await self._resolve_queue_item(client, queue_url)
            logger.warning("Resolved build_id: %s", build_id or "(empty)")

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=BuildStatus.QUEUED,
            url=f"{self.base_url}/{job_path}/{build_id}" if build_id else queue_url,
        )

    async def _resolve_queue_item(self, client: httpx.AsyncClient, queue_url: str) -> str:
        if not queue_url:
            logger.warning("No queue URL returned, cannot resolve build ID")
            return ""
        api_url = queue_url.rstrip("/") + "/api/json"
        for attempt in range(12):
            await asyncio.sleep(2)
            try:
                r = await client.get(api_url)
                if r.status_code == 200:
                    data = r.json()
                    exe = data.get("executable")
                    if exe and exe.get("number"):
                        logger.warning("Queue resolved to build #%s", exe["number"])
                        return str(exe["number"])
                    if data.get("cancelled"):
                        logger.warning("Build was cancelled in queue")
                        return ""
                    logger.warning("Queue poll %d/12: waiting (why=%s)", attempt + 1, data.get("why", ""))
                else:
                    logger.warning("Queue poll %d/12: HTTP %d", attempt + 1, r.status_code)
            except httpx.HTTPError as exc:
                logger.warning("Queue poll %d/12: %r", attempt + 1, exc)
        logger.warning("Queue item never resolved after 12 attempts")
        return ""

    async def get_build_status(self, job: str, build_id: str) -> BuildResult:
        job_path = _job_path(job)
        url = f"{self.base_url}/{job_path}/{build_id}/api/json"

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return BuildResult(
                    provider=self.name, build_id=build_id, status=BuildStatus.UNKNOWN,
                    error=f"HTTP {r.status_code}",
                )
            data = r.json()

        result_str = data.get("result")
        status = _STATUS_MAP.get(result_str, BuildStatus.UNKNOWN)
        duration = data.get("duration", 0) / 1000.0

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=status,
            url=data.get("url", ""),
            duration_seconds=duration,
            raw=data,
        )

    async def download_artifact(self, job: str, build_id: str, artifact_name: str) -> bytes:
        job_path = _job_path(job)
        url = f"{self.base_url}/{job_path}/{build_id}/artifact/{artifact_name}"

        async with self._client(timeout=120) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    async def list_artifacts(self, job: str, build_id: str) -> list[dict]:
        job_path = _job_path(job)
        url = f"{self.base_url}/{job_path}/{build_id}/api/json?tree=artifacts[*]"

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            return r.json().get("artifacts", [])

    async def list_jobs(self) -> list[dict]:
        url = f"{self.base_url}/api/json?tree=jobs[name,url,color]"

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            return [
                {"name": j["name"], "url": j.get("url", ""), "status": j.get("color", "")}
                for j in r.json().get("jobs", [])
            ]

    async def get_build_log(self, job: str, build_id: str, tail: int = 100) -> str:
        job_path = _job_path(job)
        url = f"{self.base_url}/{job_path}/{build_id}/consoleText"

        async with self._client(timeout=15) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return f"(log unavailable: HTTP {r.status_code})"
            lines = r.text.splitlines()
            return "\n".join(lines[-tail:])


def _job_path(job: str) -> str:
    if job.startswith("job/"):
        return job
    return "/".join(f"job/{seg}" for seg in job.split("/"))
