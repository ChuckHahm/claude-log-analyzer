# Schema notes (M0)

Probed 2026-10-25: 6 sessions, 1,691 records, 17 project dirs under `~/.claude/projects/` (only 4 have logs), dates 2026-10-22 to 2026-10-25.
Claude Code versions seen: 2.1.294, 2.1.308, 2.1.309, 2.1.310. Model in all assistant records: `claude-sonnet-5-5`.
`deleteSessionsAfterDays` is not set in `~/.claude/settings.json`, so the 30-day default applies.
Observed on these versions only; re-probe after upgrades.

## Record types (`.type`)
| type | notes |
|---|---|
| `assistant` | One record **per content block** (`thinking`, `text`, `tool_use`). Blocks of one API response share `message.id` and carry `apiBlockIndex`. |
| `user` | `message.content` is a string, or a list of `text` / `tool_result` blocks. Tool results also carry top-level `toolUseResult`, `sourceToolAssistantUUID`, `permissionDecision`. |
| `attachment` | Harness-injected context. `attachment.type` includes `total_tokens_reminder`, `deferred_tools_record`, `environment`, `skill_listing`, `session_context`, `instructions`, `mcp_instructions_delta`, `nested_memory`, `opened_file_in_ide`, `queued_command`, `date`, `model`, and others. Relevant to the exposure audit. |
| `system` | `subtype`: `turn_duration`, `away_summary`, `local_command`. No compaction records seen yet. |
| `api-request`, `api-request-blob`, `api-request-shape` | Request digests and params. Keys: `requestDigest`, `shapeHash`, `params`, `base`, `tail`. Newer versions only (2.1.308+). May be the closest thing to wire-level data. **Inspect in M5.** |
| `cost-state` | Session totals: `totalCostUSD` and `modelUsage` per model (input, output, thinking, cache read/create tokens). **Use as the M1 validation source.** |
| `file-history-snapshot`, `file-history-delta` | File backup state. |
| `queue-operation`, `permission-mode`, `mode`, `atis-latch`, `last-prompt`, `ai-title` | Session metadata. No `uuid`/`parentUuid`. |

## Common envelope (assistant, user, attachment, system)
`uuid`, `parentUuid`, `sessionId` (also `session_id` in some), `timestamp` (ISO 8601 Z), `cwd`, `gitBranch`, `version`, `entrypoint`, `userType`, `isSidechain`.
- `isSidechain` is `false` on all message records and absent (`null`) on the rest. **No sidechain/subagent records exist in this sample**, so subagent handling is unverified.
- Both `sessionId` and `session_id` appear; read with fallback.

## Assistant records
- `message`: `id`, `model`, `role`, `content`, `stop_reason`, `usage`, plus `container`, `context_management`, `diagnostics`, `stop_details`, `input_transformations`.
- `usage`: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, plus `cache_creation`, `iterations`, `output_tokens_details`, `server_tool_use`, `service_tier`, `speed`, `inference_geo`.
- Top-level extras: `requestId`, `requestRef`, `requestedModel`, `effort`, `perTurnEffort`, `thinkingDurationMs`, `apiBlockIndex`.

### Dedupe finding (critical)
Across 162 distinct message IDs, 111 appear in 2 to 5 records. `usage` is **identical across all records of one ID** (checked on the 4 token fields). Summing per record would overcount up to 5x. Rule: one usage row per `message.id`, take the first record.

## Tool calls
- `tool_use` block: `id`, `name`, `input`, `caller`, `type`. Names seen: `Bash` (121), `Read` (22), `Skill` (3), `Write` (2), `bash` (1, lowercase; normalize names).
- `tool_result` block (in a `user` record): `tool_use_id`, `content`, optional `is_error` (absent on 25 of 149). `content` is a string (132) or a list of `image` blocks (17).
- Join on `tool_use.id == tool_result.tool_use_id`: all 149 calls matched.
- Top-level `toolUseResult` shapes: Bash `{stdout, stderr, interrupted, isImage, noOutputExpected}`; Read `{type, file}`; Write/Edit `{content, filePath, originalFile, structuredPatch, ...}`; sometimes a plain string. Useful for exact stdout/stderr sizes and file paths.
- Image results have no text length, so they need a separate size estimate.

## Parsing rules derived
1. Dedupe assistant usage by `message.id`.
2. Treat `content` as string-or-list everywhere.
3. Read `sessionId` with a `session_id` fallback; treat `isSidechain` null as false.
4. Count and report unknown record types. Never crash on them.
5. Validate summed usage per session against `cost-state.modelUsage` and `ccusage`. Beware that `cost-state` may be cumulative across resumed sessions or include other models (it showed `claude-haiku-5-5` usage with no matching assistant records).

## Open items
- No compaction events, subagent sidechains, or Edit calls in this sample; get a longer session or find one to capture those schemas.
- Confirm whether `cost-state` is per-session or cumulative.
- `ccusage` is not yet installed for cross-checking.

## M1 validation results (2026-10-25)
Parser (`src/parse.py`) on all 6 real sessions: 1,764 records, 0 skipped lines, 0 unknown types, 169 usage rows, 154 tool calls (all matched to a result).

Deduped usage compared with the last `cost-state` per session (`parse.validate_usage`):
| field | parsed vs cost-state |
|---|---|
| output_tokens (sonnet) | 1% to 4% lower |
| cache_creation (sonnet) | 2% to 4% lower |
| cache_read (sonnet) | 15% to 25% lower |
| input_tokens (sonnet) | 97% lower (e.g. 114 vs 4,210) |
| haiku (all fields) | 0 parsed; ~1,170 input / ~15 output per session in cost-state |

Diagnosis:
- Not a partial-stream artifact: no usage row has near-zero `output_tokens`, and `requestId` maps 1:1 to `message.id`.
- The logs omit some API calls that `cost-state` counts (haiku helper calls, and other unlogged calls). Several sessions have more `api-request` records than logged assistant messages (e.g. 77 vs 70).
- **Consequence:** log-derived totals are a lower bound on billed usage. Per-action attribution is still sound; absolute totals should be labelled "logged usage" and shown beside `cost-state` where available.
- One `message.id` appears in two sessions (170 distinct IDs, 169 rows). Dedupe is global across files, so it counts once.
- `ccusage` is not installed, so the independent check is still outstanding.
