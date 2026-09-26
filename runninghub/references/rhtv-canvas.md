# RHTV Canvas Migration

RHTV canvas URLs have the form:

```text
https://rhtv.runninghub.ai/project/canvas/<canvasId>
```

The skill does not call RHTV's private browser-session endpoints. A `canvasId`
is not a `webappId` or `workflowId`, so never pass it directly to
`runninghub_app.py` or `runninghub_workflow.py`.

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

Both supported paths use `RUNNINGHUB_API_KEY`; `RHTV_ACCESS_TOKEN` is not used.
