+++
title = "Architecture"
chapter = false
weight = 20
pre = "<b>3. </b>"
+++

## Overview

Daedalus runs two container types in a single Docker image:

- **Command Augment container** (`daedalus_ca`) - injects commands into supported agent callbacks
- **Eventing container** (`daedalus`) - exposes custom functions to Mythic workflows

Both communicate with Mythic via RabbitMQ and share the same provider abstraction.

## Command Augment Flow

```
Operator (Mythic UI)
    │
    │  Runs CA command against a callback (e.g. daedalus_fetch_execute)
    ▼
Mythic Server
    │
    │  RabbitMQ message → daedalus_ca container
    ▼
Daedalus CA (create_go_tasking, server-side)
    │
    ├─→ CI/CD Provider API  (download artifact or trigger+poll build)
    ├─→ MythicRPC           (upload file, search callback, create response)
    │
    │  Sets CommandName + ReprocessAtNewCommandPayloadType
    ▼
Mythic Server
    │
    │  Reprocesses as native agent command (e.g. execute_coff)
    ▼
Target Agent Callback
```

All CA commands run entirely server-side (`script_only = True`). The CA container never ships agent code - it leverages MythicRPC to upload files and delegates execution to the existing agent.

## Eventing Flow

```
Operator (Mythic UI)
    │
    │  Workflow trigger (manual or payload_build_finish)
    ▼
Mythic Server
    │
    │  RabbitMQ message → custom_function call
    ▼
Daedalus Container
    │
    ├─→ Jenkins API     (trigger build, poll status, download artifact)
    ├─→ Forgejo API     (workflow dispatch, run status, artifacts)
    ├─→ GitHub API      (workflow dispatch, run status, artifacts)
    ├─→ GitLab API      (pipeline trigger, status, job artifacts)
    ├─→ Gitea API       (workflow dispatch, run status, artifacts)
    │
    ├─→ Mythic GraphQL  (query payloads, create tags)
    └─→ Mythic REST API (upload files)
```

### Pipeline Parameter Forwarding

Build workflows forward all parameters accepted by Labyrinth's Jenkinsfile, including `LANGUAGE`, `OUTPUT_FORMAT`, `OBFUSCATION`, `CALYPSO_MODE`, `PACKER_PRESET`, `PACKER_FLAGS`, `SIGNING_PROFILE`, `PE_SANITISE`, `TARGET_ARCH`, `LITTERBOX_SCAN`, `OPERATOR_ID`, and `CAMPAIGN_TAG`. Parameters flow from the workflow environment block through `_resolve_inputs` into `_extract_build_params`, which uppercases them before passing to the CI provider's `trigger_build` method. `PACKER_PRESET` is resolved to flags by the Jenkinsfile at build time (not by Daedalus); `PACKER_FLAGS` overrides the preset if both are set.

### Loader Name Resolution

When a `language` input is provided without a `job`, Daedalus auto-resolves the job name. The `language` value can be either the full loader directory name (`loader-c-mingw`) or the short form (`c-mingw`). If the value does not already start with `loader-`, the prefix is added automatically. The normalized name is passed both as the Jenkins job name and as the `LANGUAGE` build parameter, which the Jenkinsfile uses to locate the loader directory.

The CA container's `daedalus_obfuscate_build` command targets Labyrinth's `Jenkinsfile.tooling` pipeline instead, which accepts `REPO_URL`, `REPO_TOKEN`, `REF`, `SOURCE_PATH`, `LANGUAGE`, `OUTPUT_FORMAT`, `OBFUSCATION`, `PACKER_FLAGS`, and `SIGNING_PROFILE`.

## CA vs Eventing

| Aspect | Eventing Container | CA Container |
|--------|-------------------|--------------|
| Trigger | Workflow (manual or event) | Operator command on a callback |
| Scope | Server-wide (any payload) | Per-callback (active sessions) |
| Output | Tags payloads, returns data | Executes tools on targets |
| Use case | Build pipelines, scanning | In-memory tool deployment |

## Provider Abstraction

All CI/CD providers implement a common interface (`CIProvider`):

- `trigger_build(job, parameters)`: start a build with parameters
- `get_build_status(job, build_id)`: poll build status
- `download_artifact(job, build_id, artifact_name)`: fetch build output
- `list_jobs()`: list available jobs/pipelines
- `get_build_log(job, build_id, tail)`: retrieve build logs

The `get_provider(name)` factory selects the right implementation based on the `provider` input or `DAEDALUS_PROVIDER` env var.

## Credential Resolution

CA commands resolve credentials in priority order:

1. **Task argument** - per-invocation override (e.g. `-jenkins_token abc`)
2. **Mythic Secret** - user-level secret from Settings > Secrets (e.g. `JENKINS_API_KEY`)
3. **Environment variable** - container-level fallback (e.g. `JENKINS_TOKEN`)

This follows the pattern established by the Ghostwriter agent.

## Integration with Sphinx and Erebus

Daedalus works in a pipeline with other Mythic eventing containers:

```
Daedalus (trigger build) → CI/CD pipeline → artifact
                                                │
Erebus (package payload) ←──────────────────────┘
                                                │
Sphinx (scan payload)    ←──────────────────────┘
    │
    └── LitterBox verdict → Mythic tag
```

### Sphinx Integration

Daedalus workflows that include scanning call Sphinx's `execute_script` function directly as a workflow step. Mythic routes the call to Sphinx over RabbitMQ.

When Sphinx is not installed, Daedalus's `scan_payload` custom function can call the LitterBox API directly as a fallback.

### Jenkins First-Run Behavior

Jenkins pipeline jobs discover their parameters from the Jenkinsfile's `parameters {}` block only after the first run. On a fresh job, `buildWithParameters` returns HTTP 400. The Jenkins provider automatically falls back to `/build` (no parameters) for the initial run.
