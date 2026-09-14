# Daedalus

Daedalus is a Mythic eventing container that connects Mythic C2 to CI/CD build pipelines. Operators can trigger builds, poll for completion, download artifacts, scan payloads via Sphinx/LitterBox, and tag payloads with build results from Mythic.

## Supported CI/CD Providers

| Provider | Name | Auth | Job identifier |
|----------|------|------|----------------|
| Jenkins | `jenkins` | User + API token | Job name or path (e.g. `loader-c-mingw`, `folder/job-name`). Auto-resolved from `language` as `loader-{language}` when not set |
| Forgejo Actions | `forgejo` | Personal access token | `owner/repo` or defaults to env vars |
| GitHub Actions | `github` | Personal access token or GitHub App token | `owner/repo` or defaults to env vars |
| GitLab CI/CD | `gitlab` | Private token | Project ID (numeric) |
| Gitea Actions | `gitea` | Personal access token | `owner/repo` or defaults to env vars |

## Custom Functions

Daedalus exposes six custom functions to Mythic's eventing system:

### trigger_build

Triggers a CI/CD build on the specified provider, optionally polls for completion, and tags the source payload with the build result.

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `provider` | No | `DAEDALUS_PROVIDER` env or `jenkins` | CI provider name |
| `job` | No | auto-resolved from `language` | Job/pipeline/workflow name. If empty, derived as `loader-{language}` |
| `mythic_api_token` | Yes | none | Mythic API token for GraphQL |
| `payload_uuid` | No | none | Mythic payload UUID to associate with the build |
| `poll` | No | `true` | Whether to poll for build completion |
| `timeout` | No | `300` | Max seconds to poll |
| `language` | No | `c-mingw` | Build language/toolchain. Also used to auto-resolve the job name as `loader-{language}` |
| `output_format` | No | none | Forwarded as `OUTPUT_FORMAT` build parameter |
| `obfuscation` | No | none | Forwarded as `OBFUSCATION` build parameter |
| `param_*` | No | none | Any `param_`-prefixed input is forwarded as a build parameter (prefix stripped, key uppercased) |

**Provider-specific inputs** (override env vars per-invocation):

| Input | Env var fallback |
|-------|-----------------|
| `jenkins_url`, `jenkins_user`, `jenkins_token` | `JENKINS_URL`, `JENKINS_USER`, `JENKINS_TOKEN` |
| `forgejo_url`, `forgejo_token`, `forgejo_owner`, `forgejo_repo` | `FORGEJO_URL`, `FORGEJO_TOKEN`, `FORGEJO_OWNER`, `FORGEJO_REPO` |
| `github_token`, `github_owner`, `github_repo`, `github_api_base` | `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_API_BASE` |
| `gitlab_url`, `gitlab_token`, `gitlab_project_id` | `GITLAB_URL`, `GITLAB_TOKEN`, `GITLAB_PROJECT_ID` |
| `gitea_url`, `gitea_token`, `gitea_owner`, `gitea_repo` | `GITEA_URL`, `GITEA_TOKEN`, `GITEA_OWNER`, `GITEA_REPO` |

### check_status

Checks the current status of a CI/CD build.

**Inputs:**

| Input | Required | Description |
|-------|----------|-------------|
| `provider` | No | CI provider name |
| `job` | No | Job/pipeline name. Auto-resolved from `language` as `loader-{language}` when not set |
| `language` | No | Used to auto-resolve `job` when `job` is empty |
| `build_id` | Yes | Build number or run ID |
| `include_log` | No | `true` to include the last 50 lines of build log |

### list_configs

Lists available jobs or recent pipeline runs on the configured provider.

**Inputs:**

| Input | Required | Description |
|-------|----------|-------------|
| `provider` | No | CI provider name |

### download_artifact

Downloads a build artifact from CI/CD and uploads it to Mythic.

**Inputs:**

| Input | Required | Description |
|-------|----------|-------------|
| `provider` | No | CI provider name |
| `job` | No | Job/pipeline name. Auto-resolved from `language` as `loader-{language}` when not set |
| `language` | No | Used to auto-resolve `job` when `job` is empty |
| `build_id` | Yes | Build number or run ID |
| `artifact_name` | No | Specific artifact name (defaults to first available) |
| `mythic_api_token` | Yes | Mythic API token |
| `payload_uuid` | No | Payload UUID to tag with the artifact |

### scan_payload

Submits a payload to LitterBox for scanning, either via the Sphinx eventing container or by calling the LitterBox API directly.

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `payload_uuid` | Yes | none | Mythic payload UUID to scan |
| `mythic_api_token` | Yes | none | Mythic API token |
| `litterbox_url` | No | `LITTERBOX_URL` env | LitterBox server URL |
| `scan_type` | No | `all` | Scan type: `static`, `dynamic`, `both`, `edr`, `all` |
| `edr_profile` | No | none | EDR profile name (required when scan_type is `edr`) |
| `method` | No | `sphinx` | `sphinx` to invoke Sphinx's execute_script, `direct` to call LitterBox API directly |
| `timeout` | No | `120` | Max seconds to wait for scan results |

**Method `sphinx`** (default): Calls Sphinx's `execute_script` custom function through Mythic's eventing system. Sphinx handles upload, scan triggering, polling, and payload tagging. Requires Sphinx to be installed.

**Method `direct`**: Calls the LitterBox API directly. Downloads the payload from Mythic, uploads to LitterBox, triggers scans, and returns risk results. Does not create Sphinx tags. Use this when Sphinx is not installed.

### get_verdict

Retrieves existing Sphinx scan verdicts and Daedalus build tags for a payload.

**Inputs:**

| Input | Required | Description |
|-------|----------|-------------|
| `payload_uuid` | Yes | Mythic payload UUID |
| `mythic_api_token` | Yes | Mythic API token |

Returns the most recent Sphinx tag (risk level, risk score, risk factors, EDR alerts) and Daedalus build tag (provider, job, build ID, status) for the payload.

## Workflows

Daedalus registers seven workflows with Mythic on startup:

| Workflow | Trigger | Description |
|----------|---------|-------------|
| **Daedalus Manual Build** | `manual` | Trigger a build on demand from the Mythic UI |
| **Daedalus Auto Build** | `payload_build_finish` | Automatically trigger a CI/CD rebuild when any payload is built |
| **Daedalus Check Build Status** | `manual` | Query the status of a running build |
| **Daedalus Download Artifact** | `manual` | Pull a build artifact into Mythic |
| **Daedalus Build and Scan** | `manual` | Trigger a build, then submit the result to Sphinx/LitterBox |
| **Daedalus Scan Payload** | `manual` | Submit a payload to LitterBox via Sphinx or direct API |
| **Daedalus Get Verdict** | `manual` | Retrieve Sphinx and Daedalus tags for a payload |

Each workflow's environment variables can be edited in Mythic's Eventing UI to set default provider, job, and build parameters.

## Payload Tagging

When a build completes and a `payload_uuid` was provided, Daedalus tags the payload in Mythic with:

- **Daedalus: Build OK** (green): build succeeded
- **Daedalus: Build Failed** (red): build failed
- **Daedalus: UNKNOWN** (orange): polling timed out or status unclear

Tag data includes: provider, job, build ID, status, duration, URL, and any errors.

When scanning via Sphinx, Sphinx adds its own tags:

- **Sphinx: Clean** (green): low risk
- **Sphinx: Medium Risk** (orange)
- **Sphinx: High Risk** (red)
- **Sphinx: Critical Risk** (purple)

Use the `get_verdict` function to retrieve both tag sets for a payload.

## Example Pipelines

The `pipelines/` directory at the repo root contains CI/CD configs for each supported provider. Each pipeline:

1. Fetches shellcode from Mythic when `SHELLCODE_SOURCE` is set (`mythic:<uuid>`)
2. Runs a build stage (replace with your toolchain commands)
3. Uploads the raw build output as a CI artifact

Artifacts are not archived or compressed so that Sphinx/LitterBox can scan them directly after Daedalus downloads them.

| Provider | File | Place in your build repo |
|----------|------|--------------------------| 
| Jenkins | `pipelines/jenkins/Jenkinsfile` | Pipeline SCM config or repo root |
| GitHub Actions | `pipelines/github/build.yml` | `.github/workflows/build.yml` |
| GitLab CI/CD | `pipelines/gitlab/.gitlab-ci.yml` | Repo root as `.gitlab-ci.yml` |
| Forgejo Actions | `pipelines/forgejo/build.yml` | `.forgejo/workflows/build.yml` |
| Gitea Actions | `pipelines/gitea/build.yml` | `.gitea/workflows/build.yml` |

Mythic credentials (`MYTHIC_URL`, `MYTHIC_TOKEN`) should be stored as secrets in your CI system. See `pipelines/README.md` for details.
