# RHTV Workflow Catalog and Canvas Migration

RHTV canvas URLs have the form:

```text
https://rhtv.runninghub.ai/project/canvas/<canvasId>
```

## Incremental public catalog

The skill can browse all public RHTV common workflows without a login token:

```bash
python3 {baseDir}/scripts/rhtv_catalog.py --sync
python3 {baseDir}/scripts/rhtv_catalog.py --list
python3 {baseDir}/scripts/rhtv_catalog.py --info RHTV_CATALOG_ID
```

`--sync` reads the complete public index, compares a stable content hash, and
writes only new, changed, or reactivated records. `--list` reads the local
SQLite database and bootstraps it only when empty. The default database is
`{baseDir}/data/rhtv_catalog.sqlite3`; override it with
`RHTV_CATALOG_DB_PATH`. Removed remote entries are retained as inactive history.

For each public preview, the normalizer first finds the node whose output URL
matches the catalog thumbnail/video and follows incoming edges back to its
ancestors. It then stores only that preview-producing branch's original
uploaded images, videos, and audio (`/uploads/` assets), creator prompts,
generation settings, and structural starting/output nodes. The creator's
top-level prompt is preferred over an automatically translated copy in
`params.prompt`; if that snapshot is truncated at the platform's approximate
4,000-character limit, the longer model parameter is used instead. This prevents unrelated experiments elsewhere on the same
canvas from being reported as required inputs. When the public graph does not
expose an upload or prompt, say that it was not found rather than inventing it.

This is read-only discovery and never clones a workflow, creates a canvas, or
submits a paid generation task. The public catalog interface is not a
documented execution API, so treat it as best-effort and keep the helper
isolated from generation code.

The public catalog does not expose a permanent canvas URL for each template.
The local browser therefore links only to the official RHTV workflow library;
it must not present a local catalog URL as an RHTV workflow link. RHTV creates
a new personal canvas only after a user selects a template.

The skill does not call RHTV's private browser-session endpoints. A public
catalog ID or `canvasId` is not a `webappId` or `workflowId`, so never pass it
directly to `runninghub_app.py` or `runninghub_workflow.py`.

## Supported migration paths

Ask the user to choose one of the official API forms available from RunningHub:

1. **AI Application** — use the public AI Application URL or `webappId`, then
   follow `ai-application.md` and `runninghub_app.py`.
2. **ComfyUI Workflow API** — export the workflow API from the RunningHub
   workflow editor, obtain its `workflowId`, then follow `workflow-api.md` and
   `runninghub_workflow.py`.

If the user only has an RHTV canvas URL, explain that the canvas cannot be
converted automatically by this skill. They must recreate/export the relevant
generation flow as an AI Application or ComfyUI workflow in RunningHub first.

Both execution paths use `RUNNINGHUB_API_KEY`; browsing the public RHTV catalog
does not require `RHTV_ACCESS_TOKEN`.
