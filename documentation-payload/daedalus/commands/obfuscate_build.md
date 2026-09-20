+++
title = "daedalus_obfuscate_build"
chapter = false
weight = 102
hidden = false
+++

## Summary
Pull source from any Git repository (GitHub, Forgejo, GitLab, etc.), trigger the CI/CD tooling pipeline (garble, ConfuserEx, Calypso, Limelighter signing) on any supported provider, then execute in-memory or register as a tool. Uses the `tooling-{language}` pipeline by default, separate from the loader pipelines.

- Needs Admin: False
- Version: 4
- Author: @Lavender-exe

### Arguments

#### provider

- Description: CI/CD provider that runs the obfuscation pipeline
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### repo_url

- Description: Git clone URL for source (e.g. https://github.com/nicocha30/ligolo-ng)
- Required Value: False
- Default Value: None

#### repo_token

- Description: Access token for private repos. Falls back to `REPO_TOKEN` Mythic Secret
- Required Value: False
- Default Value: None

#### job

- Description: CI job/workflow name for the obfuscation pipeline. Auto-resolved as `tooling-{language}` when empty
- Required Value: False
- Default Value: None

#### language

- Description: Source language for the tools being compiled
- Required Value: False
- Default Value: c-mingw
- Choices: c-mingw, csharp, rust, go, nim, cpp-mingw

#### output_format

- Description: Output binary format
- Required Value: False
- Default Value: exe
- Choices: exe, dll, bin, shellcode, svc

#### obfuscation

- Description: Obfuscation level to apply (garble for Go, ConfuserEx for .NET, etc.)
- Required Value: False
- Default Value: full
- Choices: none, basic, full, garble, calypso

#### packer_flags

- Description: Extra Calypso flags (e.g. `--unhook ntdll.dll --sleep 10 --amsi hwbp --etw hwbp`)
- Required Value: False
- Default Value: None

#### signing_profile

- Description: Code signing profile (Limelighter)
- Required Value: False
- Default Value: none
- Choices: none, microsoft, google, intel, custom

#### source_path

- Description: Path within the repo to compile (directory or file, e.g. cmd/agent)
- Required Value: False
- Default Value: None

#### ref

- Description: Git branch or tag to build from
- Required Value: False
- Default Value: main

#### tool_type

- Description: How to handle the compiled output
- Required Value: False
- Default Value: register_only
- Choices: bof, assembly, register_only

#### timeout

- Description: Build timeout in seconds
- Required Value: False
- Default Value: 300

#### Provider Credential Parameters

Each provider has optional credential overrides. These override Mythic Secrets and environment variables for that specific invocation:

- `jenkins_url`, `jenkins_user`, `jenkins_token`
- `github_token`, `github_owner`, `github_repo`
- `gitlab_url`, `gitlab_token`, `gitlab_project_id`
- `forgejo_url`, `forgejo_token`, `forgejo_owner`, `forgejo_repo`
- `gitea_url`, `gitea_token`, `gitea_owner`, `gitea_repo`

## Usage

```
daedalus_obfuscate_build -repo_url https://github.com/nicocha30/ligolo-ng -language go -obfuscation garble -source_path cmd/agent
daedalus_obfuscate_build -repo_url https://github.com/example/tool -provider github -language csharp -obfuscation full -tool_type assembly
daedalus_obfuscate_build -repo_url https://github.com/BeichenDream/GodPotato -language csharp -obfuscation calypso -signing_profile microsoft
daedalus_obfuscate_build -repo_url https://github.com/example/loader -language csharp -obfuscation full -signing_profile google -packer_flags "--unhook ntdll.dll --sleep 10"
```

## MITRE ATT&CK Mapping

- T1027 - Obfuscated Files or Information
- T1059 - Command and Scripting Interpreter
- T1105 - Ingress Tool Transfer

## Detailed Summary

This command is useful for pulling open-source tools (e.g. ligolo-ng, Seatbelt) through an obfuscation pipeline before deploying them to a target.

### Build Parameters Forwarded to CI

The command passes these as CI build parameters:

| CI Parameter | Source |
|-------------|--------|
| `LANGUAGE` | `language` argument |
| `OUTPUT_FORMAT` | `output_format` argument |
| `OBFUSCATION` | `obfuscation` argument |
| `REPO_URL` | `repo_url` argument |
| `REPO_TOKEN` | `repo_token` argument (resolved via Mythic Secrets) |
| `SOURCE_PATH` | `source_path` argument |
| `REF` | `ref` argument |
| `PACKER_FLAGS` | `packer_flags` argument (only when set) |
| `SIGNING_PROFILE` | `signing_profile` argument (only when not `none`) |

### Pipeline Phases

1. Resolve provider credentials (task args -> Mythic Secrets -> env vars)
2. Trigger build on CI/CD provider with obfuscation parameters
3. Poll build status with exponential backoff (5s initial, 1.5x factor, 30s max)
4. Download build artifact (auto-selects binary)
5. Upload to Mythic as file
6. Either complete (`register_only`) or delegate to target agent (`bof`/`assembly`)

When `tool_type` is `register_only`, the file is stored persistently in Mythic (`DeleteAfterFetch=False`). When `bof` or `assembly`, the file is temporary and execution is delegated to the target agent's native command.
