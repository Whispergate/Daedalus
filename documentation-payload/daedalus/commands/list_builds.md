+++
title = "daedalus_list_builds"
chapter = false
weight = 103
hidden = false
+++

## Summary
List CI/CD jobs, recent builds for a specific job, or artifacts for a specific build. A discovery command -- run it before `daedalus_fetch_execute` or `daedalus_quick_build` to see what is available.

- Needs Admin: False
- Version: 1
- Author: @Lavender-exe

### Arguments

#### provider

- Description: CI/CD provider to query
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### job

- Description: Job name to list builds for. Leave empty to list all jobs on the provider
- Required Value: False
- Default Value: None

#### build_id

- Description: Build ID to list artifacts for. Requires `job`
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
daedalus_list_builds
daedalus_list_builds -job loader-c-mingw
daedalus_list_builds -job loader-c-mingw -build_id 42
daedalus_list_builds -provider forgejo
```

## MITRE ATT&CK Mapping

- T1082 - System Information Discovery

## Detailed Summary

Three modes of operation depending on which arguments are provided:

**No arguments (list jobs):** Queries the provider's API for all jobs/workflows/pipelines and lists their names and status indicators (e.g. Jenkins build colors).

**Job only (list builds):** Shows the latest build status, build ID, duration, URL, last successful build ID, and a summary of available artifacts (up to 5 listed, with a count of any remaining).

**Job + build_id (list artifacts):** Lists every artifact for that specific build, including relative paths and file sizes where available.
