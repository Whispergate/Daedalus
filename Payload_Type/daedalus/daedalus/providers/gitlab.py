from __future__ import annotations

import asyncio
import logging

import httpx

from daedalus.providers.base import BuildResult, BuildStatus, CIProvider

logger = logging.getLogger("daedalus.gitlab")

_STATUS_MAP = {
    "created": BuildStatus.QUEUED,
    "waiting_for_resource": BuildStatus.QUEUED,
    "preparing": BuildStatus.QUEUED,
    "pending": BuildStatus.QUEUED,
    "running": BuildStatus.RUNNING,
    "success": BuildStatus.SUCCESS,
    "failed": BuildStatus.FAILURE,
    "canceled": BuildStatus.CANCELLED,
    "skipped": BuildStatus.CANCELLED,
    "manual": BuildStatus.QUEUED,
    "scheduled": BuildStatus.QUEUED,
}


class GitLabProvider(CIProvider):
    name = "gitlab"

    def __init__(
        self,
        url: str = "",
        token: str = "",
        project_id: str = "",
        verify_ssl: bool = False,
        **_kwargs,
    ):
        self.base_url = url.rstrip("/")
        self.token = token
        self.project_id = project_id
        self.verify_ssl = verify_ssl

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=timeout,
            headers={
                "PRIVATE-TOKEN": self.token,
                "Accept": "application/json",
            },
        )

    def _api(self, path: str) -> str:
        return f"{self.base_url}/api/v4{path}"

    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        pid = self._resolve_project(job)
        ref = parameters.pop("ref", "main")

        variables = [
            {"key": k, "value": v}
            for k, v in parameters.items()
        ]

        async with self._client() as client:
            url = self._api(f"/projects/{pid}/pipeline")
            payload = {"ref": ref}
            if variables:
                payload["variables"] = variables

            r = await client.post(url, json=payload)

            if r.status_code not in (200, 201):
                return BuildResult(
                    provider=self.name, build_id="", status=BuildStatus.FAILURE,
                    error=f"Pipeline trigger failed: HTTP {r.status_code} - {r.text[:500]}",
                )

            data = r.json()
            pipeline_id = str(data.get("id", ""))

        return BuildResult(
            provider=self.name,
            build_id=pipeline_id,
            status=BuildStatus.QUEUED,
            url=data.get("web_url", ""),
        )

    async def get_build_status(self, job: str, build_id: str) -> BuildResult:
        pid = self._resolve_project(job)
        url = self._api(f"/projects/{pid}/pipelines/{build_id}")

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return BuildResult(
                    provider=self.name, build_id=build_id, status=BuildStatus.UNKNOWN,
                    error=f"HTTP {r.status_code}",
                )
            data = r.json()

        status = _STATUS_MAP.get(data.get("status", ""), BuildStatus.UNKNOWN)
        duration = data.get("duration") or 0

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=status,
            url=data.get("web_url", ""),
            duration_seconds=float(duration),
            raw=data,
        )

    async def download_artifact(self, job: str, build_id: str, artifact_name: str) -> bytes:
        pid = self._resolve_project(job)

        async with self._client(timeout=120) as client:
            if artifact_name:
                url = self._api(
                    f"/projects/{pid}/pipelines/{build_id}/jobs"
                )
                r = await client.get(url)
                r.raise_for_status()
                jobs = r.json()

                target_job = None
                for j in jobs:
                    if j.get("name") == artifact_name:
                        target_job = j
                        break
                if not target_job and jobs:
                    for j in jobs:
                        if j.get("artifacts_file", {}).get("filename"):
                            target_job = j
                            break
                if not target_job:
                    raise FileNotFoundError(
                        f"No job {artifact_name!r} with artifacts in pipeline {build_id}"
                    )

                dl_url = self._api(
                    f"/projects/{pid}/jobs/{target_job['id']}/artifacts"
                )
            else:
                dl_url = self._api(
                    f"/projects/{pid}/pipelines/{build_id}/jobs"
                )
                r = await client.get(dl_url)
                r.raise_for_status()
                jobs = r.json()
                artifact_job = None
                for j in jobs:
                    if j.get("artifacts_file", {}).get("filename"):
                        artifact_job = j
                        break
                if not artifact_job:
                    raise FileNotFoundError(f"No artifacts in pipeline {build_id}")
                dl_url = self._api(
                    f"/projects/{pid}/jobs/{artifact_job['id']}/artifacts"
                )

            r = await client.get(dl_url)
            r.raise_for_status()
            return r.content

    async def list_artifacts(self, job: str, build_id: str) -> list[dict]:
        pid = self._resolve_project(job)
        url = self._api(f"/projects/{pid}/pipelines/{build_id}/jobs")

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            jobs = r.json()
            return [
                {
                    "relativePath": j.get("name", ""),
                    "fileName": j.get("artifacts_file", {}).get("filename", j.get("name", "")),
                    "id": j.get("id"),
                }
                for j in jobs
                if j.get("artifacts_file", {}).get("filename")
            ]

    async def list_jobs(self) -> list[dict]:
        pid = self.project_id
        url = self._api(f"/projects/{pid}/pipelines")

        async with self._client() as client:
            r = await client.get(url, params={"per_page": 20, "order_by": "id", "sort": "desc"})
            if r.status_code != 200:
                return []
            pipelines = r.json()
            return [
                {
                    "name": f"pipeline-{p['id']} ({p.get('ref', '')})",
                    "url": p.get("web_url", ""),
                    "status": p.get("status", ""),
                }
                for p in pipelines
            ]

    async def get_build_log(self, job: str, build_id: str, tail: int = 100) -> str:
        pid = self._resolve_project(job)

        async with self._client(timeout=15) as client:
            jobs_url = self._api(f"/projects/{pid}/pipelines/{build_id}/jobs")
            r = await client.get(jobs_url)
            if r.status_code != 200:
                return f"(jobs unavailable: HTTP {r.status_code})"

            jobs = r.json()
            if not jobs:
                return "(no jobs in pipeline)"

            last_job = jobs[-1]
            log_url = self._api(f"/projects/{pid}/jobs/{last_job['id']}/trace")
            r = await client.get(log_url)
            if r.status_code != 200:
                return f"(log unavailable: HTTP {r.status_code})"

            lines = r.text.splitlines()
            return "\n".join(lines[-tail:])

    def _resolve_project(self, job: str) -> str:
        if job and job != self.project_id:
            return job
        return self.project_id
