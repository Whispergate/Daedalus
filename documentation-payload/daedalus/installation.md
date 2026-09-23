+++
title = "Installation"
chapter = false
weight = 10
pre = "<b>1. </b>"
+++

## Prerequisites

- A running Mythic C2 instance (v3.3+)
- At least one CI/CD system accessible from the Mythic host (Jenkins, Forgejo, GitHub, GitLab, or Gitea)
- API credentials for the CI/CD system

## Install with Mythic CLI

From the Mythic install directory:

```bash
sudo ./mythic-cli install github https://github.com/Whispergate/Daedalus
```

Or install from a local path:

```bash
sudo ./mythic-cli install folder /path/to/Daedalus
```

## Configuration

### Mythic Secrets (Command Augment)

The CA commands (`daedalus_fetch_execute`, `daedalus_register_tool`, `daedalus_obfuscate_build`) resolve CI/CD credentials from **Mythic Secrets** - user-level settings that persist across sessions.

1. In the Mythic UI, go to **Settings > Secrets** (user icon in the top-right)
2. Add the secrets for your CI/CD provider(s):

| Secret Name | Description |
|-------------|-------------|
| `JENKINS_API_KEY` | Jenkins API token |
| `GITHUB_API_KEY` | GitHub personal access token |
| `GITLAB_API_KEY` | GitLab private token |
| `FORGEJO_API_KEY` | Forgejo personal access token |
| `GITEA_API_KEY` | Gitea personal access token |
| `REPO_TOKEN` | Access token for private source repos (used by `daedalus_obfuscate_build`) |

Each operator sets their own secrets. The CA commands check Mythic Secrets before falling back to environment variables, so operators on the same Mythic instance can use different credentials.

### Eventing Workflows

As of v3.3.36, the `eventingImportContainerWorkflow` mutation has a bug (`filemeta` null constraint), so workflows must be uploaded manually. After every container rebuild that changes workflow YAML files, re-upload them through the Mythic Eventing UI to update the definitions in Mythic's database.

Upload each file from `Payload_Type/daedalus/daedalus/workflows/*.yaml`:

![Eventing Upload](/agents/daedalus/image.png)

**Note:** The `build_and_scan.yaml` workflow calls Daedalus's unified `build_and_scan` function which handles the full pipeline (build, artifact download, Mythic upload, LitterBox scan) in a single step. The `scan_payload.yaml` workflow calls Daedalus's `scan_payload` function, which delegates to Sphinx by default (`SCAN_METHOD=sphinx`). If Sphinx is not available, set `SCAN_METHOD=direct` and provide a `LITTERBOX_URL` to call LitterBox directly.

### Environment Variables (Eventing)

Set these in Mythic's Eventing UI for the workflow environment variables:

![Editing Environment Variables](/agents/daedalus/image-1.png)
![Environment Variables](/agents/daedalus/image-2.png)

The workflows which need to have their environment variables set are:

- Daedalus Scan Payload (`LITTERBOX_URL`, `PAYLOAD_UUID`, `SCAN_METHOD`, `EDR_PROFILE`)
- Daedalus Build and Scan (`LITTERBOX_URL`, `PAYLOAD_UUID`, `LANGUAGE`, `SIGNING_PROFILE`, `TARGET_ARCH`)
- Daedalus Manual Build (`LANGUAGE`, `SIGNING_PROFILE`, `TARGET_ARCH`)
- Daedalus Auto Build (`LANGUAGE`, `SIGNING_PROFILE`, `TARGET_ARCH`)

#### General

| Variable | Default | Description |
|----------|---------|-------------|
| `DAEDALUS_PROVIDER` | `jenkins` | Default CI provider |
| `DAEDALUS_JOB` | (auto-resolved) | Default job/pipeline name. When empty, auto-resolved from the `LANGUAGE` variable as `loader-{language}` |

#### Build Parameters

These match the parameters accepted by Labyrinth's main Jenkinsfile:

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGUAGE` | `c-mingw` | Build language/toolchain: `c-mingw`, `c-ollvm`, `go-garble`, `rust`, `csharp`. Also used for job auto-resolution (`loader-{language}`) |
| `OUTPUT_FORMAT` | `exe` | Output format: `exe`, `dll`, `shellcode` |
| `OBFUSCATION` | `none` | Obfuscation profile: `none`, `ollvm-cff`, `ollvm-bcf-cff`, `ollvm-full`, `ollvm-heavy`, `garble-literals`, `string-encrypt`, `calypso` |
| `PACKER_FLAGS` | (empty) | Extra Calypso flags (e.g. `--unhook ntdll.dll --sleep 10 --amsi hwbp --etw hwbp`) |
| `SIGNING_PROFILE` | `none` | Limelighter code signing profile: `none`, `microsoft`, `google`, `intel`, `custom` |
| `PE_SANITISE` | `true` | Strip Rich header, PDB path, debug directory, and version info |
| `TARGET_ARCH` | `amd64` | Target architecture: `amd64`, `arm64` |
| `LITTERBOX_SCAN` | `true` | Whether to scan the built artifact via LitterBox |
| `OPERATOR_ID` | (empty) | Operator callsign for deconfliction tagging |
| `CAMPAIGN_TAG` | (empty) | Campaign identifier for deconfliction tagging |

#### Scan Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `SCAN_TYPE` | `all` | Scan types to run: `all`, `static`, `dynamic`, `edr` |
| `SCAN_METHOD` | `sphinx` | How to submit scans: `sphinx` (via Sphinx container) or `direct` (LitterBox API) |
| `EDR_PROFILE` | (empty) | EDR profile name for EDR-type scans |
| `LITTERBOX_URL` | `http://10.5.99.12:1337` | LitterBox sandbox URL |

#### Jenkins

| Variable | Description |
|----------|-------------|
| `JENKINS_URL` | Jenkins base URL (e.g. `https://jenkins.internal:8443`) |
| `JENKINS_USER` | Jenkins API username |

> **Note:** Mythic Secrets (Settings > Secrets) are only available to CA commands, not to eventing workflows. For eventing workflows, upload a `.env` file to the Daedalus container via the Mythic UI (**Installed Services > daedalus > Upload File**). The `.env` file is loaded at container startup and makes variables available to both eventing and CA code.
>
> Example `.env`:
> ```
> JENKINS_API_KEY=your_jenkins_api_token
> JENKINS_URL=http://10.5.99.3:8080
> JENKINS_USER=admin
> ```
>
> The eventing container checks: workflow inputs → `JENKINS_TOKEN` env var → `JENKINS_API_KEY` env var (from `.env`). CA commands additionally check Mythic Secrets between task arguments and env vars.

#### Forgejo

| Variable | Description |
|----------|-------------|
| `FORGEJO_URL` | Forgejo base URL |
| `FORGEJO_TOKEN` | Forgejo personal access token |
| `FORGEJO_OWNER` | Default repository owner/org |
| `FORGEJO_REPO` | Default repository name |

#### GitHub

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub personal access token or App token |
| `GITHUB_OWNER` | Default repository owner/org |
| `GITHUB_REPO` | Default repository name |
| `GITHUB_API_BASE` | API base URL (defaults to `https://api.github.com`; set for GitHub Enterprise) |

#### GitLab

| Variable | Description |
|----------|-------------|
| `GITLAB_URL` | GitLab base URL |
| `GITLAB_TOKEN` | GitLab private token |
| `GITLAB_PROJECT_ID` | Default project ID (numeric) |

#### Gitea

| Variable | Description |
|----------|-------------|
| `GITEA_URL` | Gitea base URL |
| `GITEA_TOKEN` | Gitea personal access token |
| `GITEA_OWNER` | Default repository owner/org |
| `GITEA_REPO` | Default repository name |

## Verify Installation

After starting Mythic with Daedalus installed:

### Command Augment Container

1. Navigate to any active callback from a supported agent (Apollo, Athena, Merlin, Starburst)
2. You should see `daedalus_fetch_execute`, `daedalus_register_tool`, and `daedalus_obfuscate_build` in the command menu
3. Configure your Mythic Secrets (Settings > Secrets) with CI/CD API tokens
4. Run `daedalus_register_tool` against a known CI job to verify connectivity

### Eventing Container

1. Navigate to the **Eventing** page in the Mythic UI
2. You should see the seven Daedalus workflows registered
3. Edit a workflow's environment variables to set your CI/CD provider details
4. Run the **Daedalus Manual Build** workflow to test connectivity

## Troubleshooting

### GraphQL Authentication Error

```
Daedalus error: GraphQL error: [{'message': 'Authentication hook unauthorized this request', 'extensions': {'path': '$', 'code': 'access-denied'}}]
```

This error occurs when the Daedalus container's authentication token becomes stale or out of sync with Mythic's GraphQL API. This can happen after a Mythic restart, database migration, or if the container has been running for an extended period.

**Fix:** Restart the Daedalus container:

```bash
sudo ./mythic-cli restart daedalus
```

The container will re-register with Mythic and obtain a fresh API token on startup.

### Workflow Not Appearing in Eventing UI

If workflows are missing after a container rebuild:

1. Re-upload each YAML file from `Payload_Type/daedalus/daedalus/workflows/*.yaml` through the Mythic Eventing UI
2. This is required due to a known Mythic bug in the `eventingImportContainerWorkflow` mutation (see [Configuration](#eventing-workflows) above)
