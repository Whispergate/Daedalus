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

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        job_path = _job_path(job)

        async with self._client() as client:
            if parameters:
                endpoint = f"{self.base_url}/{job_path}/buildWithParameters"
                r = await client.post(endpoint, data=parameters)
            else:
                endpoint = f"{self.base_url}/{job_path}/build"
                r = await client.post(endpoint)

            if r.status_code not in (200, 201, 302):
                return BuildResult(
                    provider=self.name, build_id="", status=BuildStatus.FAILURE,
                    error=f"Trigger failed: HTTP {r.status_code} — {r.text[:500]}",
                )

            queue_url = r.headers.get("Location", "")
            build_id = await self._resolve_queue_item(client, queue_url)

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=BuildStatus.QUEUED,
            url=f"{self.base_url}/{job_path}/{build_id}" if build_id else queue_url,
        )

    async def _resolve_queue_item(self, client: httpx.AsyncClient, queue_url: str) -> str:
        if not queue_url:
            return ""
        api_url = queue_url.rstrip("/") + "/api/json"
        for _ in range(12):
            await asyncio.sleep(2)
            try:
                r = await client.get(api_url)
                if r.status_code == 200:
                    data = r.json()
                    exe = data.get("executable")
                    if exe and exe.get("number"):
                        return str(exe["number"])
                    if data.get("cancelled"):
                        return ""
            except httpx.HTTPError:
                pass
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
