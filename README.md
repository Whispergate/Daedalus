# Daedalus

Mythic eventing container that bridges Mythic C2 and CI/CD build pipelines.

Daedalus lets operators trigger builds, poll for completion, download artifacts, and tag payloads with build results from the Mythic UI. It supports five CI/CD providers:

- **Jenkins**: REST API (build with parameters, console log, artifact download)
- **Forgejo Actions**: Workflow dispatch API
- **GitHub Actions**: Workflow dispatch API (supports GitHub Enterprise via custom API base)
- **GitLab CI/CD**: Pipeline API (trigger, status, job artifacts, trace logs)
- **Gitea Actions**: Workflow dispatch API (Gitea Acts runner)

## Quick Start

```bash
# Install into Mythic
sudo ./mythic-cli install github https://github.com/Whispergate/Daedalus

# Or from a local path
sudo ./mythic-cli install folder /path/to/Daedalus
```

Set your CI/CD credentials via environment variables or per-workflow in the Mythic Eventing UI. See `documentation-payload/daedalus/installation.md` for details.

## Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| Daedalus Manual Build | `manual` | On-demand build trigger |
| Daedalus Auto Build | `payload_build_finish` | Auto build, upload artifact, and scan on new payload |
| Daedalus Check Build Status | `manual` | Query a running build |
| Daedalus Download Artifact | `manual` | Pull artifact into Mythic |
| Daedalus Build and Scan | `manual` | Unified: build, download artifact, upload to Mythic, scan via LitterBox |
| Daedalus Scan Payload | `manual` | Submit payload to LitterBox |
| Daedalus Get Verdict | `manual` | Retrieve Sphinx + Daedalus tags |

## Pipeline Integration

Daedalus works alongside [Sphinx](https://github.com/Whispergate/Sphinx) and [Erebus](https://github.com/Whispergate/Erebus):

```
Operator → Daedalus (trigger build) → CI/CD pipeline → artifact
                                                           │
           Erebus (package payload) ←──────────────────────┘
                                                           │
           Sphinx (scan payload)    ←──────────────────────┘
               │
               └── LitterBox verdict → Mythic tag
```

### Sphinx Integration

Daedalus workflows that include scanning call Sphinx's `execute_script` function directly as a workflow step. Mythic routes the call to Sphinx over RabbitMQ - the same mechanism Sphinx's own workflows use. No GraphQL proxy is needed.

When Sphinx is not installed, Daedalus's `scan_payload` custom function can call the LitterBox API directly as a fallback.

The **Build and Scan** workflow triggers a CI/CD build, waits for completion, then hands the payload to Sphinx for scanning. **Get Verdict** retrieves the combined Sphinx scan and Daedalus build tags for a payload.

### Example Pipelines

The `pipelines/` directory contains ready-to-use CI/CD configs for each provider. Copy the one matching your CI system into your build repository and fill in the build commands for your toolchain.

| Provider | File | Place in |
|----------|------|----------|
| Jenkins | `pipelines/jenkins/Jenkinsfile` | Pipeline SCM config or repo root |
| GitHub Actions | `pipelines/github/build.yml` | `.github/workflows/build.yml` |
| GitLab CI/CD | `pipelines/gitlab/.gitlab-ci.yml` | Repo root as `.gitlab-ci.yml` |
| Forgejo Actions | `pipelines/forgejo/build.yml` | `.forgejo/workflows/build.yml` |
| Gitea Actions | `pipelines/gitea/build.yml` | `.gitea/workflows/build.yml` |

Each pipeline fetches shellcode from Mythic when `SHELLCODE_SOURCE` is provided, runs your build, and uploads the raw output as a CI artifact. Artifacts are not archived so Sphinx/LitterBox can scan them directly.

## Documentation

See `documentation-payload/daedalus/` for full docs:

- `index.md`: Function reference and input tables
- `installation.md`: Setup and configuration guide
- `architecture.md`: Design overview and integration patterns

## Credits

- [Mythic](https://github.com/its-a-feature/Mythic) by @its-a-feature
- [LitterBox](https://github.com/BlackSnufkin/LitterBox) by @BlackSnufkin
- [Sphinx](https://github.com/Whispergate/Sphinx) by @hunterino-sec
- [MAAS](https://github.com/yoda66/MAAS) by @yoda66