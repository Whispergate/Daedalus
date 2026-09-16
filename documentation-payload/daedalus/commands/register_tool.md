+++
title = "register_tool"
chapter = false
weight = 101
hidden = false
+++

## Summary
Fetch a BOF or .NET assembly from any CI/CD provider and register it as a persistent file in Mythic for later use by any callback.

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

- Description: Job/workflow name to fetch the tool from
- Required Value: True
- Default Value: None

#### build_id

- Description: Build number or run ID
- Required Value: False
- Default Value: lastSuccessfulBuild

#### artifact_name

- Description: Specific artifact filename. If empty, auto-selects the first binary artifact
- Required Value: False
- Default Value: None

#### tool_name

- Description: Name to register the tool as in Mythic (defaults to artifact filename)
- Required Value: False
- Default Value: None

#### comment

- Description: Comment to attach to the registered file
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
register_tool -provider jenkins -job loader-c-mingw -tool_name whoami.o
register_tool -provider github -job build.yml -build_id latest -tool_name seatbelt.exe
```

## MITRE ATT&CK Mapping

- T1105 - Ingress Tool Transfer

## Detailed Summary

Unlike `fetch_execute`, this command does not delegate to the target agent. It downloads the artifact, uploads it to Mythic with `DeleteAfterFetch=False`, and completes. The registered file remains available in Mythic's file browser for any callback to use later.

The same artifact auto-selection logic applies as in `fetch_execute`.
