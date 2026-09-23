# Pipelines

CI/CD pipeline configs for each provider Daedalus supports. Copy the relevant file into your build repository and fill in the build stage with your toolchain commands.

Each pipeline has two stages:

1. **Fetch**: Downloads shellcode from Mythic if `SHELLCODE_SOURCE` is set (`mythic:<uuid>`), otherwise expects the shellcode file to exist in the repo.
2. **Build**: Runs your compilation/linking/obfuscation commands. Output goes to `build/` and is uploaded as a CI artifact.

Build artifacts are uploaded as raw files (not archived) so that Daedalus can download them and Sphinx/LitterBox can scan them directly.

## Parameters

Daedalus forwards these to the pipeline via each provider's parameter mechanism (Jenkins build parameters, workflow_dispatch inputs, GitLab pipeline variables):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LANGUAGE` | `c-mingw` | Build language/toolchain identifier |
| `OUTPUT_FORMAT` | `exe` | Output format (`exe`, `dll`, `bin`) |
| `OBFUSCATION` | `none` | Obfuscation profile name |
| `SHELLCODE_SOURCE` | (empty) | Mythic payload ref (`mythic:<uuid>`), triggers download |
| `SHELLCODE_FILE` | `shellcode/payload.bin` | Path to shellcode file in the repo |

Additional parameters can be passed from Daedalus using the `param_` prefix (e.g. `param_target_arch=arm64` forwards `TARGET_ARCH=arm64`).

## Provider files

| Provider | File | Where to place it |
|----------|------|--------------------|
| Jenkins | `jenkins/Jenkinsfile` | Pipeline SCM config or repo root |
| GitHub Actions | `github/build.yml` | `.github/workflows/build.yml` |
| GitLab CI/CD | `gitlab/.gitlab-ci.yml` | Repo root as `.gitlab-ci.yml` |
| Forgejo Actions | `forgejo/build.yml` | `.forgejo/workflows/build.yml` |
| Gitea Actions | `gitea/build.yml` | `.gitea/workflows/build.yml` |

## Secrets

For Mythic payload fetching, add these as secrets in your CI system:

| Secret | Description |
|--------|-------------|
| `MYTHIC_URL` | Mythic server base URL (e.g. `https://mythic.internal:7443`) |
| `MYTHIC_TOKEN` | Mythic API token with file download permissions |

Jenkins passes these as build parameters. GitHub/Forgejo/Gitea read them from repository secrets. GitLab uses CI/CD variables (mark as protected and masked).

---

## Tooling Pipeline

The `daedalus_obfuscate_build` command triggers the `daedalus-tooling-build` Jenkins job, which is deployed from the Labyrinth repo's `pipeline/Jenkinsfile.tooling`. That single pipeline handles cloning, compilation, obfuscation (ConfuserEx, Garble, Calypso), signing, and artifact archiving for all languages. No separate per-provider tooling templates are needed.
