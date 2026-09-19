+++
title = "daedalus_check_build"
chapter = false
weight = 104
hidden = false
+++

## Summary
Check the status of a CI/CD build and optionally retrieve the build log. Use after triggering a build with `daedalus_obfuscate_build` or `daedalus_quick_build` to monitor progress or debug failures.

- Needs Admin: False
- Version: 1
- Author: @Lavender-exe

### Arguments

#### job

- Description: Job/workflow name
- Required Value: True
- Default Value: None

#### build_id

- Description: Build number or run ID (e.g. 42, lastBuild)
- Required Value: False
- Default Value: lastBuild

#### provider

- Description: CI/CD provider
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### include_log

- Description: Include the tail of the build log in the output
- Required Value: False
- Default Value: false

#### log_lines

- Description: Number of log lines to include from the end (requires `include_log`)
- Required Value: False
- Default Value: 50

#### Provider Credential Parameters

Each provider has optional credential overrides. These override Mythic Secrets and environment variables for that specific invocation:

- `jenkins_url`, `jenkins_user`, `jenkins_token`
- `github_token`, `github_owner`, `github_repo`
- `gitlab_url`, `gitlab_token`, `gitlab_project_id`
- `forgejo_url`, `forgejo_token`, `forgejo_owner`, `forgejo_repo`
- `gitea_url`, `gitea_token`, `gitea_owner`, `gitea_repo`

## Usage

```
daedalus_check_build -job loader-c-mingw -build_id 42
daedalus_check_build -job loader-c-mingw -include_log true
daedalus_check_build -job loader-c-mingw -build_id 42 -include_log true -log_lines 200
```

## MITRE ATT&CK Mapping

- T1082 - System Information Discovery

## Detailed Summary

Returns the build status (queued, running, success, failure, cancelled, unknown), duration, URL, any error messages, and a list of available artifacts. When `include_log` is true, appends the last N lines of console output.

The display params in the Mythic task output show the build status at a glance, e.g. `SUCCESS -- jenkins/loader-c-mingw #42`.
