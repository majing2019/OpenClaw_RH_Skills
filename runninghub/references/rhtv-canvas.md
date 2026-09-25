# RHTV Canvas

Use `runninghub/scripts/rhtv.py` when the user provides an RHTV URL shaped like:

```text
https://rhtv.runninghub.ai/project/canvas/<canvasId>
```

RHTV Canvas is separate from RunningHub AI Applications. Never treat a `canvasId` as a `webappId`.

## Authentication

RHTV uses the signed-in web account's short-lived bearer token, not `RUNNINGHUB_API_KEY`.

- Read it only from `RHTV_ACCESS_TOKEN`.
- Never ask the user to paste the token into chat.
- Never print, log, or place it on the command line.
- If it is missing or expired, explain that the user must refresh their local environment configuration.

These canvas endpoints are used by the RHTV web client and are not part of the documented public RunningHub OpenAPI. Treat compatibility as best-effort and do not automatically retry paid generation requests.

## Inspect a canvas

```bash
python3 {baseDir}/scripts/rhtv.py \
  --info "https://rhtv.runninghub.ai/project/canvas/2103485063252774914"
```

Present node labels and types without exposing the access token or raw internal responses. Ask which node the user wants to run when the intent is unclear.

## Run one node

Before a paid generation, tell the user that the RHTV node is starting. Do not retry automatically if submission returns an uncertain response.

```bash
python3 {baseDir}/scripts/rhtv.py \
  --run-node CANVAS_ID NODE_ID \
  --set 'NODE_ID:params.prompt=雨夜里的未来城市'
```

`--set` paths are relative to the node's `data` object. Values that are valid JSON become JSON values; other values remain strings. Repeat `--set` to change multiple fields.

## Status and cancellation

```bash
python3 {baseDir}/scripts/rhtv.py --status TASK_ID
python3 {baseDir}/scripts/rhtv.py --wait TASK_ID --timeout 1200
python3 {baseDir}/scripts/rhtv.py --cancel TASK_ID
```

Only cancel when the user explicitly asks. A successful `--wait` response may include result URLs inside the returned task object; download and deliver them using the environment's normal media-delivery mechanism rather than exposing signed URLs.

## Boundaries

- Supported: identify canvas URLs, inspect graph nodes, override node data, run one node, query status, wait, cancel.
- Not supported: editing or saving the RHTV canvas, browser login, extracting browser cookies/tokens, publishing community compositions, or deleting canvas/history/assets.
- Do not call `runninghub_app.py` for an RHTV URL.
