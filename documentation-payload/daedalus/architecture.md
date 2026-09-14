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
    ├─→ Mythic GraphQL  (query payloads, create tags)
    └─→ Mythic REST API (upload files)
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

The `auto_build.yaml` workflow triggers on `payload_build_finish` and calls the unified `build_and_scan` function, so Daedalus automatically runs the full pipeline (build, artifact download, Mythic upload, LitterBox scan) when Erebus or another agent produces a new payload.

### Sphinx Integration

Daedalus workflows that include scanning (Build and Scan, Scan Payload) call Sphinx's `execute_script` custom function directly as a workflow step. Mythic routes the call to Sphinx over RabbitMQ - the same mechanism Sphinx's own workflows use.

```
Mythic Server
   │
   │  Step 1: custom_function → Daedalus (trigger_build)
   │  Step 2: custom_function → Sphinx  (execute_script)
   │
   ├─→ Daedalus Container (via RabbitMQ)
   │       └─→ Jenkins/Forgejo/etc.
   │
   └─→ Sphinx Container (via RabbitMQ)
           └─→ LitterBox API → scan → tag payload
```

This approach works because Mythic natively routes `custom_function` actions to the container named in `container_name` via RabbitMQ. There is no need for Daedalus to proxy the call through GraphQL.

**Fallback: Direct LitterBox API**

When Sphinx is not installed, Daedalus's `scan_payload` custom function can call the LitterBox API directly:

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

This does not create Sphinx-style tags; it returns raw risk data.

### Verdict Querying (get_verdict)

The `get_verdict` function queries Mythic's tag system for both Sphinx and Daedalus tags on a payload:

- **Sphinx tags**: risk level, risk score, risk factors, EDR alerts
- **Daedalus tags**: provider, job, build ID, status, duration, URL

Operators can check both build and scan history for a payload in one call.

### Unified Build and Scan (build_and_scan)

The `build_and_scan` custom function runs the full pipeline in a single function call - no multi-step workflow coordination needed. It triggers a CI/CD build, polls for completion, downloads the built artifact from CI, uploads it to Mythic, and scans it via the LitterBox API directly. This avoids the Mythic limitation where step outputs cannot flow between workflow steps and templates are not resolved.

```
Daedalus Container (single function call)
   │
   ├─ 1. Trigger build on CI/CD (Jenkins, etc.)
   ├─ 2. Poll until build completes
   ├─ 3. Tag source payload with build result
   ├─ 4. Download artifact from CI/CD
   ├─ 5. Upload artifact to Mythic (REST API)
   └─ 6. Upload to LitterBox → trigger scans → return risk
```

### Jenkins First-Run Behavior

Jenkins pipeline jobs discover their parameters from the Jenkinsfile's `parameters {}` block only after the first run. On a fresh job, `buildWithParameters` returns HTTP 400 ("not parameterized"). The Jenkins provider automatically falls back to `/build` (no parameters) for the initial run. Subsequent triggers use `buildWithParameters` normally.
