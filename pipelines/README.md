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

## Tooling Pipelines

Separate pipeline configs for the `daedalus_obfuscate_build` command. These clone a third-party tool repo, compile it, optionally obfuscate the output, and upload the artifact. They do not fetch shellcode from Mythic.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `REPO_URL` | (required) | Git clone URL of the tool repository |
| `REPO_TOKEN` | (empty) | Access token for private repos (HTTP basic auth) |
| `REF` | `main` | Branch or tag to checkout |
| `SOURCE_PATH` | (empty) | Subdirectory to compile (empty = repo root) |
| `LANGUAGE` | `csharp` | Build language: `csharp`, `go`, `rust`, `c-mingw`, `cpp-mingw`, `nim` |
| `OUTPUT_FORMAT` | `exe` | Output format: `exe`, `dll`, `bin`, `shellcode`, `svc` |
| `OBFUSCATION` | `none` | Obfuscation level: `none`, `basic`, `full`, `garble`, `calypso` |
| `PACKER_FLAGS` | (empty) | Calypso flags: `--inject local\|remote --execute thread\|direct\|apc\|callback\|fiber --syscall indirect\|hellsgate\|halosgate --cipher aes-cbc\|aes-ecb\|xor\|rc4 --compress none\|zlib\|lz4\|rle --encode none\|base64\|hex\|mac\|uuid --amsi --etw --sleep --unhook --sandbox --ppid --block-dlls --module-stomp --drip --entropy-reduce --self-delete --obfuscate --no-antidebug` |

### Stages

1. **Clone** - shallow clone at the specified ref, with optional token auth.
2. **Build** - language-specific compilation. Auto-detects `.sln`/`.csproj` for C#, uses `Makefile` when present for C/C++.
3. **Obfuscate** (skipped when `none`) - ConfuserEx for C# (`basic`/`full`), Garble for Go (`garble`), Calypso for any language (`calypso`).
4. **Archive** - uploads build output as a CI artifact.

### Provider Files

| Provider | File | Where to place it |
|----------|------|--------------------|
| Jenkins | `jenkins/Jenkinsfile.tooling` | Pipeline SCM config or repo root |
| GitHub Actions | `github/tooling.yml` | `.github/workflows/tooling.yml` |
| GitLab CI/CD | `gitlab/.gitlab-ci-tooling.yml` | Repo root as `.gitlab-ci-tooling.yml` |
| Forgejo Actions | `forgejo/tooling.yml` | `.forgejo/workflows/tooling.yml` |
| Gitea Actions | `gitea/tooling.yml` | `.gitea/workflows/tooling.yml` |

### Obfuscation Setup

C# obfuscation requires ConfuserEx on the runner. Install `Confuser.CLI.exe` on PATH or place the .NET build at `/opt/confuserex/Confuser.CLI.dll`. Go obfuscation requires [garble](https://github.com/burrowers/garble) on PATH.

[Calypso] is a PE packer that works on any language's compiled output. It encrypts the binary and wraps it in a new executable with configurable injection methods (`--inject local|remote`), execution primitives (`--execute thread|direct|apc|callback|fiber`), syscall strategies (`--syscall indirect|hellsgate|halosgate`), ciphers (`--cipher aes-cbc|aes-ecb|xor|rc4`), compression (`--compress none|zlib|lz4|rle`), and encoding (`--encode none|base64|hex|mac|uuid`). Additional evasion flags include `--amsi`, `--etw`, `--sleep`, `--unhook`, `--sandbox`, `--ppid`, `--block-dlls`, `--module-stomp`, `--drip`, `--entropy-reduce`, `--self-delete`, `--obfuscate`, and `--no-antidebug`. For C# binaries it uses `--type csharp` (loads .NET assemblies); all other languages use `--peinject`. The `maas-builder-calypso` Docker image ships with the compiled `calypso` binary and OLLVM cross-compilation wrappers. When `PACKER_FLAGS` is empty, sensible defaults are applied (`--unhook ntdll.dll --inject local --execute thread --syscall indirect --cipher aes-cbc --hide`).
