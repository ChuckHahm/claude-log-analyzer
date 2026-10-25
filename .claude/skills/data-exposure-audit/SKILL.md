---
name: data-exposure-audit
description: Audit Claude Code session logs to determine what local files, command output, and sensitive content entered the model's context and was therefore sent to Anthropic. Use when the user asks about data exposure, privacy, confidentiality of client or proprietary material, secrets in logs, what Claude has read, or how to restrict what Claude Code can see.
---

# Data Exposure Audit

Requires normalized tables from `session-log-parsing`.

## What the logs can show
Anything Claude read or ran appears in `tool_result` content (file reads, `cat`, grep hits, directory listings, command output). That content was part of the model's context and was sent to the API.

## Procedure
1. **Inventory**: per session, list files read (from tool inputs), directories listed, and commands run.
2. **Pattern scan**: search tool results for a configurable pattern list in `config/patterns.yaml`: client/project names, credential regexes (API keys, tokens, private key headers, connection strings), sensitive path prefixes. Pattern file is gitignored.
3. **Summarize**: sessions and projects with matches, count of matches, file paths involved. **Never print a full secret.** Show a short prefix and length only.
4. **Recommend controls**:
   - permission deny rules for sensitive paths and files
   - ignore patterns for env files, key material, data dumps
   - avoid launching Claude Code from directories holding sensitive material
   - rotate any credential that appeared in a result
   - consider a logging proxy to verify actual traffic

## Required caveat (include in every report)
Logs approximate what was sent but omit the system prompt, tool schemas, auto-loaded context, and telemetry. Retention and training use of the data depend on the account's plan and settings; verify the current policy for the account type in use.

## Handling
Process locally. The audit report itself may contain sensitive paths: store it outside version control.
