# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.0] - 2026-07-28
### Added
- **Connectors:** HubSpot (CRM v3), WhatsApp (Meta Business Cloud), AWS S3, PostgreSQL, and Email (SMTP).
- **Google Workspace connector** expanded to a full Sheets/Gmail/Calendar surface: `get_sheet_values`, `update_sheet_values`, n8n-style `upsert_sheet_row` (append-or-update by key column), `send_gmail` (send via the Gmail API), and `create_calendar_event`.
- **Data tools:** `data_filter` (keep list items matching a condition), `data_set_fields` (build/reshape an output object, n8n's Edit Fields), and `date_time` (format/add/subtract/diff).
- **File tools:** Excel read/write (`.xlsx`), a content-addressed file store for binary data in workflows, and an RSS reader.
- **Graph engine:** parallel `for_each` (run items N at a time), relational operators (`>`, `<`, `>=`, `<=`) plus `contains`/`==`/`!=`/`not` in edge and decision conditions, per-item execution, human-in-the-loop nodes, template-expression data passing (with filters) between nodes, subworkflow (subgraph) nodes, per-node execution policies (retry/timeout/continue-on-error), and resume replay with failed-run result preservation.
- **FLOW graph node type** — embed multi-agent flow patterns (critic review, ensemble voting, map-reduce, coordinator/worker, auction, p2p, round robin, parallel) as a single workflow node via the `FLOW_TYPES` registry.
- **Agents:** a curated library of twelve reusable role agents plus a delegator role; persistent agent memory across runs.
- **MCP client** for calling tools on external MCP servers, with server tools exposed as agent-usable proxy tools.
- **Builder:** natural-language workflow generation via a planner/delegator/worker crew, with request-relevance filtering of catalog prompt context.
- **Durable datasets** that accumulate rows across runs, and `ExecutionStore.delete()` to remove run records from memory, SQL, and JSON persistence.
- Bundled `openai` and `anthropic` so agent nodes run out of the box when a provider key is present.

### Fixed
- Agent nodes leaked live service objects (notably the `human_input_provider` callable) into their echoed context, which could not be JSON-serialized — breaking run-record serialization. Service keys are now excluded and the run API sanitizes defensively.
- Google Sheets calls targeted `www.googleapis.com`, which does not route the Sheets API (404); they now use `sheets.googleapis.com`.
- LLM providers lazily re-create a closed client (`_ensure_client`), so flows that reuse runtimes across iterations (critic review round 2, p2p rounds, batch execution) no longer fail with "client not initialized".
- Runtime-orchestrated flows (Auction/CoordinatorWorker/CriticReview/EnsembleVoting/MapReduce/P2P) were uninstantiable (abstract `build_graph`).
- `__version__` resolved the wrong distribution name and reported 0.0.0; CLI `--version` now reports the real version.
- Normalized numeric crontab weekdays for APScheduler; audit fixes for critic acceptance, voting, and delegation tags.

### Documentation
- Documented availability of production-grade runtime features (connectors, triggers, observability, security, CLI extensions, worker queue engine).
- Updated GenXBot docs to reflect full recipe-template integration: `recipe_id`/`recipe_inputs` rendering, blending recipe + agent-generated actions, deduplication, and fallback action guarantees.
- Added implementation/test references for recipe blending behavior in GenXBot docs (`orchestrator.py`, `routes_runs.py`, and orchestrator blend tests).
- Expanded GenXBot observability docs with structured hooks for plan latency, tool execution attempts, safety decisions, and retry/failure events.
- Added usage/setup examples for recipe-templated runs and observability verification endpoints.

## [0.1.6] - 2026-02-13
### Added
- LLM-based ranking utility with safe JSON parsing, repair logic, and heuristic fallback scoring.
- Opt-in agent config flag (`enable_llm_ranking`) and runtime tool ranking output (`tool_rankings`).
- Ranking utility tests covering valid JSON, repaired JSON, and heuristic fallback paths.
- Documentation updates and examples for LLM ranking usage and heuristic defaults.
