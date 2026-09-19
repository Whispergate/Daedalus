+++
title = "daedalus_quick_build"
chapter = false
weight = 106
hidden = false
+++

## Summary
Trigger any CI/CD job with arbitrary parameters, wait for completion, download the artifact, and register it in Mythic or execute it in-memory. A generic build command that works with any CI job. Use `daedalus_obfuscate_build` instead when you need repo-cloning and obfuscation-specific parameters.

- Needs Admin: False
- Version: 1
- Author: @Lavender-exe

### Arguments

#### job

- Description: CI job/workflow name to trigger (e.g. loader-c-mingw)
- Required Value: True
- Default Value: None

#### provider

- Description: CI/CD provider
- Required Value: False
- Default Value: jenkins
- Choices: jenkins, github, gitlab, forgejo, gitea

#### build_params

- Description: Build parameters as a JSON object (e.g. `{"LANGUAGE":"c-mingw","OBFUSCATION":"full"}`)
- Required Value: False
- Default Value: {}

#### tool_type

- Description: How to handle the compiled output
- Required Value: False
- Default Value: register_only
- Choices: register_only, bof, assembly

#### timeout

- Description: Build timeout in seconds
- Required Value: False
- Default Value: 300

#### bof_args

- Description: Arguments to pass to the BOF (if `tool_type` is bof)
- Required Value: False
- Default Value: None

#### assembly_args

- Description: Arguments to pass to the .NET assembly (if `tool_type` is assembly)
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
daedalus_quick_build -job loader-c-mingw
daedalus_quick_build -job loader-c-mingw -build_params '{"OBFUSCATION":"ollvm-full","PE_SANITISE":"true"}'
daedalus_quick_build -job loader-c-mingw -tool_type bof -bof_args "whoami"
daedalus_quick_build -provider forgejo -job loader-c-ollvm -build_params '{"LANGUAGE":"c-ollvm"}' -timeout 600
```

## MITRE ATT&CK Mapping

- T1027 - Obfuscated Files or Information
- T1105 - Ingress Tool Transfer

## Detailed Summary

Unlike `daedalus_obfuscate_build`, this command does not manage repository cloning, language selection, or obfuscation-specific parameters. It passes `build_params` directly as CI build parameters, making it suitable for re-triggering loader builds, running custom pipelines, or any CI job that does not need repo-cloning logic.

### Pipeline Phases

1. Trigger build on the CI/CD provider with the supplied parameters
2. Poll build status with exponential backoff (5s initial, 1.5x factor, 30s max)
3. On failure, include the last 30 lines of build log in the error message
4. Download the build artifact (auto-selects binary)
5. Upload to Mythic
6. Either complete (`register_only`) or delegate to the target agent's native command (`bof`/`assembly`)

### Difference from daedalus_obfuscate_build

| Feature | quick_build | obfuscate_build |
|---------|------------|----------------|
| Repo cloning | No | Yes (via `REPO_URL`) |
| Language/obfuscation params | Pass manually in `build_params` | Dedicated typed arguments |
| Job auto-resolution | No (must specify) | Yes (`daedalus-tooling-build`) |
| Error diagnostics | Includes log tail on failure | Status only |
| Use case | Re-run loader builds, generic CI jobs | Compile third-party tools from source |
