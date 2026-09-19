+++
title = "daedalus_scan_artifact"
chapter = false
weight = 105
hidden = false
+++

## Summary
Fetch a CI/CD artifact and submit it to LitterBox for malware scanning. Returns risk assessment, detection counts, and per-engine results. Use to vet payloads before deploying them to a target.

- Needs Admin: False
- Version: 1
- Author: @Lavender-exe

### Arguments

#### job

- Description: Job/workflow name to fetch the artifact from
- Required Value: True
- Default Value: None

#### provider

- Description: CI/CD provider
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### build_id

- Description: Build number or run ID
- Required Value: False
- Default Value: lastSuccessfulBuild

#### artifact_name

- Description: Specific artifact filename. If empty, auto-selects the first binary artifact
- Required Value: False
- Default Value: None

#### litterbox_url

- Description: LitterBox API URL. Falls back to the `LITTERBOX_URL` environment variable
- Required Value: False
- Default Value: None

#### scan_type

- Description: Type of scan to run
- Required Value: False
- Default Value: both
- Choices: static, dynamic, both, edr, all

#### edr_profile

- Description: EDR profile name for edr/all scan types (e.g. defender, crowdstrike)
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
daedalus_scan_artifact -job loader-c-mingw -scan_type both
daedalus_scan_artifact -job loader-c-mingw -build_id 42 -scan_type all -edr_profile defender
daedalus_scan_artifact -provider forgejo -job loader-c-ollvm -scan_type static
```

## MITRE ATT&CK Mapping

- T1027.005 - Obfuscated Files or Information: Indicator Removal from Tools

## Detailed Summary

The command downloads the artifact from the CI/CD provider, uploads it to LitterBox, triggers the requested scan types (static, dynamic, EDR, or combinations), and polls for results with up to 6 attempts at 5-second intervals.

The output includes:
- File metadata (size, MD5 hash)
- Risk level (LOW, MEDIUM, HIGH, CRITICAL) with color coding
- Per-scan-type results (static score/verdict, dynamic score/verdict, EDR detection status)
- Individual engine detections (up to 10 listed, with a count of any remaining)

If results are not ready after polling, the output shows PENDING and suggests using the `get_verdict` eventing workflow to check later.

The display params show the risk level at a glance, e.g. `[LOW] loader.exe from jenkins/loader-c-mingw #42`.
