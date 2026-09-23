+++
title = "daedalus"
chapter = false
weight = 5
+++
## Summary

Daedalus is a "Command Augmentation" payload type that extends supported Mythic agent callbacks with CI/CD pipeline commands. Daedalus itself can't be "built"; instead, it offers Mythic-side commands that fetch artifacts from CI/CD providers and pass them down to callbacks for execution or registration.

These commands are automatically injected into all callbacks based on payload types listed in the [agent_support.json](#agent_supportjson) file (more on that further down).

Daedalus also includes a Mythic **eventing container** that exposes custom functions to workflows for triggering builds, polling status, downloading artifacts, scanning payloads via Sphinx/LitterBox, and tagging payloads with build results.

Daedalus offers three execution/registration modes for supported payload types:
* BOF (Beacon Object File)
* Execute Assembly (.NET)
* Register Only (store in Mythic for later use)

### Supported CI/CD Providers

| Provider | Name | Auth | Job Identifier |
|----------|------|------|----------------|
| Jenkins | `jenkins` | User + API token | Job name or path (e.g. `loader-c-mingw`) |
| GitHub Actions | `github` | Personal access token | `owner/repo` or defaults to env vars |
| GitLab CI/CD | `gitlab` | Private token | Project ID (numeric) |
| Forgejo Actions | `forgejo` | Personal access token | `owner/repo` or defaults to env vars |
| Gitea Actions | `gitea` | Personal access token | `owner/repo` or defaults to env vars |

### Credential Resolution

CA commands resolve credentials in priority order:

1. **Task argument** - per-invocation override (e.g. `-jenkins_token abc`)
2. **Mythic Secret** - user-level secret from Settings > Secrets (e.g. `JENKINS_API_KEY`)
3. **Environment variable** - container-level fallback (e.g. `JENKINS_TOKEN`)

Configure secrets in your Mythic user settings (**Settings > Secrets**):

| Secret Name | Provider | Description |
|-------------|----------|-------------|
| `JENKINS_API_KEY` | Jenkins | Jenkins API token |
| `GITHUB_API_KEY` | GitHub | Personal access token |
| `GITLAB_API_KEY` | GitLab | Private token |
| `FORGEJO_API_KEY` | Forgejo | Personal access token |
| `GITEA_API_KEY` | Gitea | Personal access token |
| `REPO_TOKEN` | Any | Access token for private source repos (used by `daedalus_obfuscate_build`) |

## Supporting Files

### agent_support.json

This file identifies which payload types are supported and maps Daedalus actions to each agent's native command names and parameter names. The format is an array of objects (one per agent), mirroring the pattern used by [forge](https://github.com/MythicAgents/forge):

```json
[
    {
        "agent": "apollo",
        "bof_command": "execute_coff",
        "bof_file_parameter_name": "bof_file",
        "bof_argument_array_parameter_name": "coff_arguments",
        "bof_entrypoint_parameter_name": "function_name",
        "inline_assembly_command": "inline_assembly",
        "inline_assembly_file_parameter_name": "assembly_file",
        "inline_assembly_argument_parameter_name": "assembly_arguments",
        "execute_assembly_command": "execute_assembly",
        "execute_assembly_file_parameter_name": "assembly_file",
        "execute_assembly_argument_parameter_name": "assembly_arguments",
        "assembly_default_execution_method": "inline_assembly",
        "upload_command": "upload"
    }
]
```

Each entry describes:

* `agent` - the payload type name
* `bof_command` - native command that executes a BOF/COFF
* `bof_file_parameter_name` - parameter name for the BOF file
* `bof_argument_array_parameter_name` - parameter name for BOF arguments
* `bof_entrypoint_parameter_name` - parameter name for the BOF entry point function
* `inline_assembly_command` - native command for inline assembly execution (empty if unsupported)
* `inline_assembly_file_parameter_name` - parameter name for the inline assembly file
* `inline_assembly_argument_parameter_name` - parameter name for inline assembly arguments
* `execute_assembly_command` - native command for execute-assembly
* `execute_assembly_file_parameter_name` - parameter name for the assembly file
* `execute_assembly_argument_parameter_name` - parameter name for assembly arguments
* `assembly_default_execution_method` - preferred assembly method (`inline_assembly` or `execute_assembly`)
* `upload_command` - native command for file upload

To add your own agent, add an entry to this file and add the agent name to `command_augment_supported_agents` in `agent_definition.py`, then rebuild:
```bash
sudo ./mythic-cli build daedalus
```

### Eventing Container

Daedalus also registers an eventing container that exposes seven custom functions to Mythic's workflow system:

| Function | Description |
|----------|-------------|
| `trigger_build` | Trigger a CI/CD build, optionally poll for completion, tag source payload |
| `check_status` | Check the current status of a CI/CD build |
| `list_configs` | List available jobs or recent pipeline runs |
| `download_artifact` | Download a build artifact from CI/CD and upload to Mythic |
| `scan_payload` | Submit a payload to LitterBox for scanning via Sphinx or direct API |
| `build_and_scan` | Unified pipeline: build, download artifact, upload to Mythic, scan via LitterBox |
| `get_verdict` | Retrieve Sphinx scan verdicts and Daedalus build tags for a payload |

These are invoked via Mythic workflows, not through agent callbacks. Configure workflow environment variables in the Mythic Eventing UI.

### Workflows

Seven workflows are registered on container startup:

| Workflow | Trigger | Description |
|----------|---------|-------------|
| Daedalus Manual Build | `manual` | On-demand build trigger with full Labyrinth pipeline params |
| Daedalus Auto Build | `payload_build_finish` | Auto build + scan on new payload |
| Daedalus Check Build Status | `manual` | Query a running build |
| Daedalus Download Artifact | `manual` | Pull artifact into Mythic |
| Daedalus Build and Scan | `manual` | Unified: build, download, upload, scan |
| Daedalus Scan Payload | `manual` | Scan payload via Sphinx or direct LitterBox API |
| Daedalus Get Verdict | `manual` | Retrieve Sphinx + Daedalus tags |

### Workflow Build Parameters (Labyrinth Alignment)

The build workflows (`Manual Build`, `Auto Build`, `Build and Scan`) forward all parameters accepted by Labyrinth's main Jenkinsfile:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LANGUAGE` | `c-mingw` | Build language: `c-mingw`, `c-ollvm`, `go-garble`, `rust`, `csharp` |
| `OUTPUT_FORMAT` | `exe` | Output format: `exe`, `dll`, `shellcode` |
| `OBFUSCATION` | `none` | Obfuscation profile: `none`, `ollvm-cff`, `ollvm-bcf-cff`, `ollvm-full`, `ollvm-heavy`, `garble-literals`, `string-encrypt`, `calypso` |
| `PACKER_FLAGS` | (empty) | Extra Calypso flags (e.g. `--unhook ntdll.dll --sleep 10 --amsi hwbp --etw hwbp`) |
| `SIGNING_PROFILE` | `none` | Limelighter code signing: `none`, `microsoft`, `google`, `intel`, `custom` |
| `PE_SANITISE` | `true` | Strip Rich header, PDB path, debug info, version info |
| `TARGET_ARCH` | `amd64` | Target architecture: `amd64`, `arm64` |
| `LITTERBOX_SCAN` | `true` | Whether to scan the built artifact via LitterBox |
| `OPERATOR_ID` | (empty) | Operator callsign for deconfliction tagging |
| `CAMPAIGN_TAG` | (empty) | Campaign identifier for deconfliction tagging |

The `Scan Payload` workflow supports scan method selection (`SCAN_METHOD`: `sphinx` or `direct`) and EDR profile targeting (`EDR_PROFILE`).

### Payload Tagging

When a build completes with a `payload_uuid`, Daedalus tags the payload:

- **Daedalus: Build OK** (green): build succeeded
- **Daedalus: Build Failed** (red): build failed
- **Daedalus: UNKNOWN** (orange): polling timed out or status unclear

## Authors
- @Lavender-exe
