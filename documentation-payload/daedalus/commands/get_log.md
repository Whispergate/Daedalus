+++
title = "daedalus_get_log"
chapter = false
weight = 107
hidden = false
+++

## Summary
Retrieve the console output from a CI/CD build. Use to debug build failures, inspect compilation warnings, or review pipeline output without leaving the Mythic UI.

- Needs Admin: False
- Version: 1
- Author: @Lavender-exe

### Arguments

#### job

- Description: Job/workflow name
- Required Value: True
- Default Value: None

#### build_id

- Description: Build number or run ID
- Required Value: False
- Default Value: lastBuild

#### provider

- Description: CI/CD provider
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### lines

- Description: Number of log lines to retrieve from the end of the output
- Required Value: False
- Default Value: 100

#### Provider Credential Parameters

Each provider has optional credential overrides. These override Mythic Secrets and environment variables for that specific invocation:

- `jenkins_url`, `jenkins_user`, `jenkins_token`
- `github_token`, `github_owner`, `github_repo`
- `gitlab_url`, `gitlab_token`, `gitlab_project_id`
- `forgejo_url`, `forgejo_token`, `forgejo_owner`, `forgejo_repo`
- `gitea_url`, `gitea_token`, `gitea_owner`, `gitea_repo`

## Usage

```
daedalus_get_log -job loader-c-mingw
daedalus_get_log -job loader-c-mingw -build_id 42
daedalus_get_log -job loader-c-mingw -build_id 42 -lines 200
```

## MITRE ATT&CK Mapping

- T1082 - System Information Discovery

## Detailed Summary

Retrieves the build status first (to include it in the output header), then fetches the last N lines of console output from the CI/CD provider. The output is formatted with a header showing the provider, job, build ID, and status, followed by the raw log text.

This is a read-only command. For a shorter log tail alongside status and artifact information, use `daedalus_check_build` with `-include_log true` instead.
