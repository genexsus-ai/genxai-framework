# Binary Data — Design

Status: **MVP implemented** (file store, refs through state, download/write/
read tools, studio download endpoint + UI links)

## Problem

Workflow state is JSON end-to-end: node results, SSE events, run records,
checkpoints, template resolution. Files (PDFs, images, CSV exports) don't fit
— embedding bytes would blow persistence caps and break streaming, and raw
filesystem paths are opaque, unportable, and leak machine details into run
history.

## Design: files are content-addressed, references flow through state

### FileRef

A file in a workflow is a small, JSON-safe dict:

```json
{
  "__genxai_file__": true,
  "id": "<sha256 of content>",
  "name": "report.pdf",
  "media_type": "application/pdf",
  "size": 48213
}
```

Refs pass through node results, templates (`{{ fetch.data.file }}` resolves
to the ref dict), persistence, and SSE untouched — they're just data. The
bytes live once in the store.

### FileStore (`genxai/core/files.py`)

- Content-addressed layout: `<base>/ab/<sha256>` plus a `<sha256>.json`
  metadata sidecar (name, media type, size, created_at). Saving the same
  bytes twice stores them once (id is the hash).
- `save_bytes(data, name, media_type) -> ref`, `read_bytes(ref_or_id)`,
  `open_path(ref_or_id)`, `get_metadata(id)`, `is_file_ref(value)`.
- Process-global instance via `get_file_store()`; the studio points it at
  `<data_dir>/files` on startup (`configure_file_store`), standalone use
  defaults to `GENXAI_FILE_STORE_DIR` or `~/.genxai/files`.

### Tools

- `file_download` (web): fetch a URL (size-capped, default 25 MB) → ref.
- `file_write` (file): write text content → ref (e.g. build a CSV, then
  hand it to a connector).
- `file_content` (file): ref → text (encoding + max_chars guarded) so
  agents can read a downloaded document.

Tools that don't know about refs are unaffected; adding ref support to a
tool/connector is: accept the ref dict param, call `read_bytes`/`open_path`.

### Studio

- `GET /api/v1/files/{file_id}` streams the bytes with the stored name and
  media type (404 for unknown ids). Note: when `studio_api_token` is set,
  plain `<a>` downloads can't send the header — the endpoint stays behind
  the token; UI links work in the default (untokened) deployment.
- Run results UI: any ref found in a node's output renders as a
  `📎 name (size)` download link in the inspector.

## Non-goals / later

- **GC/TTL**: content-addressing makes files immortal by default. A
  reaper (delete files unreferenced by any retained run) is future work;
  deployments can clear `<data_dir>/files` safely when run history is
  pruned.
- **Connector attachments** (email attachments, Slack uploads): follow-up —
  the ref plumbing is ready, each connector needs its own param wiring.
- **Streaming through nodes**: refs make this unnecessary for MVP; tools
  hold whole files in memory (bounded by the download cap).
