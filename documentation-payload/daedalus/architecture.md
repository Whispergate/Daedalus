# Architecture

## Overview

Daedalus is a Mythic eventing container. It runs alongside Mythic as a Docker container, communicates via RabbitMQ, and exposes custom functions that Mythic workflows can invoke.

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
    └─→ Mythic GraphQL  (query payloads, upload files, create tags)
```

## Component Design

### Provider Abstraction

All CI/CD providers implement a common interface (`CIProvider`):

- `trigger_build(job, parameters)`: start a build with parameters
- `get_build_status(job, build_id)`: poll build status
- `download_artifact(job, build_id, artifact_name)`: fetch build output
- `list_jobs()`: list available jobs/pipelines
- `get_build_log(job, build_id, tail)`: retrieve build logs

The `get_provider(name)` factory selects the right implementation based on the `provider` input or `DAEDALUS_PROVIDER` env var.

### Job Resolution

When the `job` input is empty, Daedalus auto-resolves the Jenkins job name from the `language` input using the pattern `loader-{language}`. This matches the per-language job naming convention used by the Labyrinth setup script (e.g. `c-mingw` → `loader-c-mingw`, `csharp` → `loader-csharp`).

### Build Parameters

Parameters reach the CI/CD system in two ways:

1. **Named inputs**: `language`, `output_format`, `obfuscation`, `ref`, `workflow` are recognized and forwarded directly
2. **Prefixed inputs**: Any input starting with `param_` has the prefix stripped, the key uppercased, and is forwarded (e.g. `param_target_arch=arm64` becomes `TARGET_ARCH=arm64`)

### Payload Integration

When `payload_uuid` is provided:

1. Daedalus queries Mythic's GraphQL API for the payload metadata
2. Optionally downloads the payload file (for size metadata)
3. Passes `SHELLCODE_SOURCE=mythic:<uuid>` as a build parameter so the CI pipeline can fetch it
4. On build completion, tags the payload with the build result

### Workflow Registration

On container startup, Daedalus reads all `.yaml` files from its `workflows/` directory and registers them with Mythic via the `eventingImportContainerWorkflow` GraphQL mutation. Existing workflows with the same filename are replaced (`delete_old_version: true`).

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

The `auto_build.yaml` workflow triggers on `payload_build_finish`, so Daedalus automatically starts a CI/CD rebuild when Erebus or another agent produces a new payload. Sphinx then picks up the output via its own `payload_build_finish` trigger to run LitterBox scans.

### Sphinx Integration (scan_payload)

Daedalus can invoke Sphinx to scan payloads via LitterBox. Two methods are supported:

**Method 1: Via Sphinx (`method=sphinx`, default)**

```
Daedalus                    Mythic Server                 Sphinx
   │                             │                           │
   │  eventingInvokeCustom       │                           │
   │  Function(sphinx,           │  RabbitMQ call             │
   │  execute_script)  ────────► │ ─────────────────────────► │
   │                             │                           │
   │                             │   Sphinx uploads to       │
   │                             │   LitterBox, scans,       │
   │                             │   tags payload             │
   │                             │                           │
   │  ◄──────────────────────────│ ◄───────────────────────── │
   │  response with scan results │                           │
```

Sphinx downloads the payload from Mythic, uploads it to LitterBox, triggers scans, polls for results, and tags the payload. Daedalus receives the final result.

**Method 2: Direct LitterBox API (`method=direct`)**

```
Daedalus                    Mythic GraphQL              LitterBox
   │                             │                           │
   │  query payload file ──────► │                           │
   │  ◄──────────────────────────│                           │
   │                             │                           │
   │  upload file  ──────────────────────────────────────────►│
   │  trigger scans ─────────────────────────────────────────►│
   │  fetch risk  ◄──────────────────────────────────────────│
```

Daedalus calls the LitterBox API directly. Useful when Sphinx is not installed. Does not create Sphinx-style tags; returns raw risk data.

### Verdict Querying (get_verdict)

The `get_verdict` function queries Mythic's tag system for both Sphinx and Daedalus tags on a payload:

- **Sphinx tags**: risk level, risk score, risk factors, EDR alerts
- **Daedalus tags**: provider, job, build ID, status, duration, URL

Operators can check both build and scan history for a payload in one call.

### Chained Build and Scan (build_and_scan)

The `build_and_scan` workflow chains `trigger_build` and `scan_payload` into a single operation: trigger a CI/CD build, poll for completion, then submit the result to LitterBox for scanning.
