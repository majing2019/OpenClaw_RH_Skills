# ComfyUI Workflow API

Use `runninghub/scripts/runninghub_workflow.py` for an exported RunningHub
ComfyUI workflow. It uses the documented OpenAPI and `RUNNINGHUB_API_KEY`.

## Identify the workflow

In the RunningHub workflow editor, use the download/export control and choose
**Export Workflow API**. Use the resulting numeric `workflowId`. Do not use an
RHTV `canvasId` or AI Application `webappId` as a workflow ID.

## Inspect nodes

```bash
python3 {baseDir}/scripts/runninghub_workflow.py --info WORKFLOW_ID
```

The result lists each node and input field. Linked inputs are shown for context
but should not normally be overridden. Use scalar or media-loader fields for
parameter changes.

## Run the workflow

Before submitting a paid task, notify the user that generation is starting.

```bash
python3 {baseDir}/scripts/runninghub_workflow.py --run WORKFLOW_ID \
  --node '6:text=雨夜里的未来城市' \
  --node '3:seed=42' \
  -o /tmp/openclaw/rh-output/workflow_$(date +%s).png
```

For a local media input:

```bash
python3 {baseDir}/scripts/runninghub_workflow.py --run WORKFLOW_ID \
  --file '12:image=/tmp/openclaw/rh-output/input.png' \
  -o /tmp/openclaw/rh-output/workflow_$(date +%s).png
```

Options:

- `--instance-type default|plus|ultra`
- `--access-password PASSWORD` for protected workflows
- `--retain-seconds 10..180` only when the user explicitly wants paid instance
  retention; it can add charges
- `--node` and `--file` are repeatable

Do not automatically retry a submission with an uncertain response because a
duplicate workflow run may incur another charge.
