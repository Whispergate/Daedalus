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

The CA commands (`fetch_execute`, `register_tool`, `obfuscate_build`) resolve CI/CD credentials from **Mythic Secrets** - user-level settings that persist across sessions.

1. In the Mythic UI, go to **Settings > Secrets** (user icon in the top-right)
2. Add the secrets for your CI/CD provider(s):

| Secret Name | Description |
|-------------|-------------|
| `JENKINS_API_KEY` | Jenkins API token |
| `GITHUB_API_KEY` | GitHub personal access token |
| `GITLAB_API_KEY` | GitLab private token |
| `FORGEJO_API_KEY` | Forgejo personal access token |
| `GITEA_API_KEY` | Gitea personal access token |
| `REPO_TOKEN` | Access token for private source repos (used by `obfuscate_build`) |

Each operator sets their own secrets. The CA commands check Mythic Secrets before falling back to environment variables, so operators on the same Mythic instance can use different credentials.

### Eventing Workflows

As of v3.3.36, the `eventingImportContainerWorkflow` mutation has a bug (`filemeta` null constraint), so workflows must be uploaded manually. After every container rebuild that changes workflow YAML files, re-upload them through the Mythic Eventing UI to update the definitions in Mythic's database.

Upload each file from `Payload_Type/daedalus/daedalus/workflows/*.yaml`:

![Eventing Upload](/agents/daedalus/installation/image.png)

**Note:** The `build_and_scan.yaml` workflow calls Daedalus's unified `build_and_scan` function which handles the full pipeline (build, artifact download, Mythic upload, LitterBox scan) in a single step. The `scan_payload.yaml` workflow calls Sphinx's `execute_script` function directly. If Sphinx is not available, use Daedalus's `scan_payload` custom function with `method=direct` and a `LITTERBOX_URL` instead.

### Environment Variables (Eventing)

Set these in Mythic's Eventing UI for the workflow environment variables:

![Editing Environment Variables](/agents/daedalus/installation/image-1.png)
![Environment Variables](/agents/daedalus/installation/image-2.png)

The workflows which need to have their environment variables set are:

- Daedalus Scan Payload (`LITTERBOX_URL`, `PAYLOAD_UUID`)
- Daedalus Build and Scan (`LITTERBOX_URL`, `PAYLOAD_UUID`, `LANGUAGE`)

#### General

| Variable | Default | Description |
|----------|---------|-------------|
| `DAEDALUS_PROVIDER` | `jenkins` | Default CI provider |
| `DAEDALUS_JOB` | (auto-resolved) | Default job/pipeline name. When empty, auto-resolved from the `LANGUAGE` variable as `loader-{language}` |

#### Build Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGUAGE` | `c-mingw` | Build language/toolchain identifier. Also used for job auto-resolution (`loader-{language}`) |
| `OUTPUT_FORMAT` | `exe` | Output format passed to the pipeline: `exe`, `dll`, `bin`, `shellcode`, `svc` |
| `OBFUSCATION` | `none` | Obfuscation profile: `none`, `basic`, `full`, or toolchain-specific name |

#### Jenkins

| Variable | Description |
|----------|-------------|
| `JENKINS_URL` | Jenkins base URL (e.g. `https://jenkins.internal:8443`) |
| `JENKINS_USER` | Jenkins API username |
| `JENKINS_TOKEN` | Jenkins API token |

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

1. Navigate to any active callback from a supported agent (Apollo, Athena, Merlin, Poseidon, Starburst)
2. You should see `fetch_execute`, `register_tool`, and `obfuscate_build` in the command menu
3. Configure your Mythic Secrets (Settings > Secrets) with CI/CD API tokens
4. Run `register_tool` against a known CI job to verify connectivity

### Eventing Container

1. Navigate to the **Eventing** page in the Mythic UI
2. You should see the seven Daedalus workflows registered
3. Edit a workflow's environment variables to set your CI/CD provider details
4. Run the **Daedalus Manual Build** workflow to test connectivity
