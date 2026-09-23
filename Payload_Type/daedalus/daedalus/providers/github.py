from __future__ import annotations

import asyncio
import logging

import httpx

from daedalus.providers.base import BuildResult, BuildStatus, CIProvider

logger = logging.getLogger("daedalus.github")

_STATUS_MAP = {
    "completed": {
        "success": BuildStatus.SUCCESS,
        "failure": BuildStatus.FAILURE,
        "cancelled": BuildStatus.CANCELLED,
        "timed_out": BuildStatus.FAILURE,
        "action_required": BuildStatus.RUNNING,
    },
    "queued": BuildStatus.QUEUED,
    "in_progress": BuildStatus.RUNNING,
    "waiting": BuildStatus.QUEUED,
    "requested": BuildStatus.QUEUED,
    "pending": BuildStatus.QUEUED,
}

API_BASE = "https://api.github.com"


class GitHubProvider(CIProvider):
    name = "github"

    def __init__(
        self,
        token: str = "",
        owner: str = "",
        repo: str = "",
        api_base: str = "",
        **_kwargs,
    ):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.api_base = (api_base or API_BASE).rstrip("/")

    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        owner, repo = self._resolve_repo(job)
        workflow_file = parameters.pop("workflow", f"{job}.yml")
        ref = parameters.pop("ref", "main")

        payload = {
            "ref": ref,
            "inputs": parameters,
        }

        async with self._client() as client:
            known_ids = await self._get_recent_run_ids(client, owner, repo)

            url = f"{self.api_base}/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
            r = await client.post(url, json=payload)

            if r.status_code not in (200, 204):
                return BuildResult(
                    provider=self.name, build_id="", status=BuildStatus.FAILURE,
                    error=f"Dispatch failed: HTTP {r.status_code} - {r.text[:500]}",
                )

            run_id = await self._find_new_run(client, owner, repo, known_ids)

        return BuildResult(
            provider=self.name,
            build_id=run_id,
            status=BuildStatus.QUEUED,
            url=f"https://github.com/{owner}/{repo}/actions/runs/{run_id}" if run_id else "",
        )

    async def _get_recent_run_ids(
        self, client: httpx.AsyncClient, owner: str, repo: str,
    ) -> set[str]:
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs"
        try:
            r = await client.get(url, params={"per_page": 10})
            if r.status_code == 200:
                return {str(run["id"]) for run in r.json().get("workflow_runs", [])}
        except httpx.HTTPError:
            pass
        return set()

    async def _find_new_run(
        self, client: httpx.AsyncClient, owner: str, repo: str,
        known_ids: set[str], max_attempts: int = 10,
    ) -> str:
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs"
        for attempt in range(max_attempts):
            await asyncio.sleep(2 + attempt)
            try:
                r = await client.get(url, params={"per_page": 5})
                if r.status_code == 200:
                    for run in r.json().get("workflow_runs", []):
                        rid = str(run["id"])
                        if rid not in known_ids:
                            return rid
            except httpx.HTTPError:
                pass
        logger.warning("Could not find new run after %d attempts", max_attempts)
        return ""

    async def get_build_status(self, job: str, build_id: str) -> BuildResult:
        owner, repo = self._resolve_repo(job)
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs/{build_id}"

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return BuildResult(
                    provider=self.name, build_id=build_id, status=BuildStatus.UNKNOWN,
                    error=f"HTTP {r.status_code}",
                )
            data = r.json()

        run_status = data.get("status", "")
        conclusion = data.get("conclusion", "")

        if run_status == "completed":
            status = _STATUS_MAP["completed"].get(conclusion, BuildStatus.UNKNOWN)
        else:
            status = _STATUS_MAP.get(run_status, BuildStatus.UNKNOWN)

        return BuildResult(
            provider=self.name,
            build_id=build_id,
            status=status,
            url=data.get("html_url", ""),
            raw=data,
        )

    async def download_artifact(self, job: str, build_id: str, artifact_name: str) -> bytes:
        owner, repo = self._resolve_repo(job)

        async with self._client(timeout=120) as client:
            list_url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs/{build_id}/artifacts"
            r = await client.get(list_url)
            r.raise_for_status()
            artifacts = r.json().get("artifacts", [])

            target = None
            for a in artifacts:
                if a.get("name") == artifact_name:
                    target = a
                    break
            if not target and artifacts:
                target = artifacts[0]
            if not target:
                raise FileNotFoundError(f"No artifact {artifact_name!r} in run {build_id}")

            dl_url = f"{self.api_base}/repos/{owner}/{repo}/actions/artifacts/{target['id']}/zip"
            r = await client.get(dl_url, follow_redirects=True)
            r.raise_for_status()
            return r.content

    async def list_artifacts(self, job: str, build_id: str) -> list[dict]:
        owner, repo = self._resolve_repo(job)
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs/{build_id}/artifacts"

        async with self._client() as client:
            r = await client.get(url)
            if r.status_code != 200:
                return []
            artifacts = r.json().get("artifacts", [])
            return [
                {
                    "relativePath": a.get("name", ""),
                    "fileName": a.get("name", ""),
                    "id": a.get("id"),
                }
                for a in artifacts
            ]

    async def list_jobs(self) -> list[dict]:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/actions/runs"

        async with self._client() as client:
            r = await client.get(url, params={"per_page": 20})
            if r.status_code != 200:
                return []
            runs = r.json().get("workflow_runs", [])
            return [
                {
                    "name": run.get("name", f"run-{run['id']}"),
                    "url": run.get("html_url", ""),
                    "status": run.get("status", ""),
                    "conclusion": run.get("conclusion", ""),
                }
                for run in runs
            ]

    async def get_build_log(self, job: str, build_id: str, tail: int = 100) -> str:
        owner, repo = self._resolve_repo(job)
        url = f"{self.api_base}/repos/{owner}/{repo}/actions/runs/{build_id}/logs"

        async with self._client(timeout=15) as client:
            r = await client.get(url, follow_redirects=True)
            if r.status_code != 200:
                return f"(log unavailable: HTTP {r.status_code})"
            # GitHub returns a zip of logs; return raw bytes info
            return f"(log archive: {len(r.content)} bytes - download via artifact)"

    def _resolve_repo(self, job: str) -> tuple[str, str]:
        if "/" in job:
            parts = job.split("/", 1)
            return parts[0], parts[1]
        return self.owner, self.repo
