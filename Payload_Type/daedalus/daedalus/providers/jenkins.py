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
        self._crumb_header: str = ""
        self._crumb_value: str = ""
        logger.info(
            "JenkinsProvider init: base_url=%r auth=%s",
            self.base_url, "set" if self.auth else "none",
        )

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._crumb_header and self._crumb_value:
            headers[self._crumb_header] = self._crumb_value
        return httpx.AsyncClient(
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=timeout,
            headers=headers,
        )

    async def _fetch_crumb(self) -> None:
        try:
            async with httpx.AsyncClient(
                auth=self.auth, verify=self.verify_ssl, timeout=10,
            ) as client:
                r = await client.get(
                    f"{self.base_url}/crumbIssuer/api/json",
                )
                if r.status_code == 200:
                    data = r.json()
                    self._crumb_header = data.get("crumbRequestField", "Jenkins-Crumb")
                    self._crumb_value = data.get("crumb", "")
                    logger.info("Fetched Jenkins crumb: %s", self._crumb_header)
                elif r.status_code == 404:
                    logger.info("Jenkins CSRF protection disabled (no crumb issuer)")
                else:
                    logger.warning("Crumb fetch failed: HTTP %d", r.status_code)
        except httpx.HTTPError as exc:
            logger.warning("Crumb fetch error: %r", exc)

    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        job_path = _job_path(job)

        if not self._crumb_value:
            await self._fetch_crumb()

        async with self._client() as client:
            if parameters:
                endpoint = f"{self.base_url}/{job_path}/buildWithParameters"
                logger.info("POST %s (params: %s)", endpoint, list(parameters.keys()))
                r = await client.post(endpoint, data=parameters)
            else:
                endpoint = f"{self.base_url}/{job_path}/build"
                logger.info("POST %s (no params)", endpoint)
                r = await client.post(endpoint)

            logger.info(
                "Jenkins trigger response: HTTP %d, Location=%s",
                r.status_code, r.headers.get("Location", "(none)"),
            )

            if r.status_code == 400 and "not parameterized" in r.text:
                logger.info(
                    "Job not parameterized yet (first run?), retrying with /build"
                )
                endpoint = f"{self.base_url}/{job_path}/build"
                r = await client.post(endpoint)
                logger.info(
                    "Jenkins /build fallback: HTTP %d, Location=%s",
                    r.status_code, r.headers.get("Location", "(none)"),
                )

            if r.status_code not in (200, 201, 302):
                return BuildResult(
                    provider=self.name, build_id="", status=BuildStatus.FAILURE,
                    error=f"Trigger failed: HTTP {r.status_code} - {r.text[:500]}",
                )

            queue_url = r.headers.get("Location", "")
            logger.info("Resolving queue item: %s", queue_url)
            build_id = await self._resolve_queue_item(client, queue_url)
            logger.info("Resolved build_id: %s", build_id or "(empty)")

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=BuildStatus.QUEUED,
            url=f"{self.base_url}/{job_path}/{build_id}" if build_id else queue_url,
        )

    async def _resolve_queue_item(self, client: httpx.AsyncClient, queue_url: str) -> str:
        if not queue_url:
            logger.info("No queue URL returned, cannot resolve build ID")
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
                        logger.info("Queue resolved to build #%s", exe["number"])
                        return str(exe["number"])
                    if data.get("cancelled"):
                        logger.info("Build was cancelled in queue")
                        return ""
                    logger.info("Queue poll %d/12: waiting (why=%s)", attempt + 1, data.get("why", ""))
                else:
                    logger.info("Queue poll %d/12: HTTP %d", attempt + 1, r.status_code)
            except httpx.HTTPError as exc:
                logger.info("Queue poll %d/12: %r", attempt + 1, exc)
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
