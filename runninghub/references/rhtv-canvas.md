# RHTV Workflow Catalog and Canvas Migration

RHTV canvas URLs have the form:

```text
https://rhtv.runninghub.ai/project/canvas/<canvasId>
```

## Live public catalog

The skill can browse all public RHTV common workflows without a login token:

```bash
python3 {baseDir}/scripts/rhtv_catalog.py --list
python3 {baseDir}/scripts/rhtv_catalog.py --info RHTV_CATALOG_ID
```

This is read-only discovery. It uses the public catalog currently consumed by
the RHTV website, returns compact metadata and node summaries, and never clones
a workflow, creates a canvas, or submits a paid generation task. The catalog
interface is not a documented execution API, so treat it as best-effort and
keep the helper isolated from generation code.

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
