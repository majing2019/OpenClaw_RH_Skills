# RHClaw — RunningHub Skill for OpenClaw / DeepSeek Harness

[中文](./README.md)

> ## Now supports [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
>
> Same standard `SKILL.md` as [OpenClaw](https://github.com/openclaw/openclaw). Install and update the same way.

An OpenClaw and DeepSeek Harness skill that brings multimedia generation capabilities — including image, video, audio, 3D, and text — to conversational AI, powered by 420 [RunningHub](https://www.runninghub.cn) API endpoints. Built with zero external dependencies (pure Python 3 + curl), it supports standard model APIs, custom ComfyUI workflows published as AI Applications, and experimental RHTV Canvas node execution.

## Capabilities

| Category | Endpoints | Tasks |
|----------|-----------|-------|
| **Image** | 102 | text-to-image, image-to-image, image upscale, Midjourney-style |
| **Video** | 230 | text-to-video, image-to-video, start-end frames, video extend/edit, motion control, multimodal video |
| **Audio** | 20 | text-to-speech, music generation, voice clone |
| **3D** | 16 | text-to-3D, image-to-3D, multi-image-to-3D |
| **Text** | 52 | image-to-text, video-to-text, text-to-text |
| **AI Apps** | Unlimited | Run any RunningHub AI Application (custom ComfyUI workflow) |
| **RHTV Canvas** | Experimental | Inspect a canvas and run individual nodes |

## Quick Start

### Install

In your OpenClaw or DeepSeek Harness chat, say:

> Install the RunningHub skill from https://github.com/HM-RunningHub/OpenClaw_RH_Skills

The assistant will clone the repo, copy files to the workspace, and guide you through API key setup.

### Update

When a new version is available, say in your OpenClaw or DeepSeek Harness chat:

> Update from https://github.com/HM-RunningHub/OpenClaw_RH_Skills and re-read @runninghub/SKILL.md

The assistant will pull the latest code and reload the skill config. No need to re-enter your API key.

### Prerequisites

- **API Key** — Get one from [RunningHub API Management](https://www.runninghub.cn/enterprise-api/sharedApi) (click "新建")
- **Wallet balance** — [Recharge here](https://www.runninghub.cn/vip-rights/4) — API calls require funds
- **RHTV only:** `RHTV_ACCESS_TOKEN` from a signed-in web session. It is not the standard API key and must stay out of chat, logs, and command-line arguments.

## Usage

Once installed, just talk to your OpenClaw assistant in natural language:

- *"Generate a picture of a dog playing in the park"*
- *"Turn this photo into a video"*
- *"Create background music for my video"*
- *"Upscale this image to 4K"*
- *"Convert this image to a 3D model"*
- *"Run this AI app: https://www.runninghub.cn/ai-detail/1877265245566922800"*
- *"Inspect this RHTV canvas: https://rhtv.runninghub.ai/project/canvas/2103485063252774914"*
- *"What are the hottest AI apps?"*
- *"Show me the newest AI apps"*

The assistant automatically selects the best RunningHub endpoint based on your request. For AI Apps, it fetches the app's node info, guides you through parameter setup, and runs the workflow. You can also browse recommended, hottest, and newest AI apps.

### Video Model Selection

When generating video, the assistant presents 7 curated models to choose from:

> 1. 🚀 **Google Veo 3.1 Fast** — Fast with great quality, best value
> 2. 🔥 **Grok Video** — Grok-powered, incredible imagination
> 3. 🎯 **Kling v3.0 Pro** — Natural motion, best for people
> 4. 🎬 **Google Veo 3.1 Pro** — Cinematic quality
> 5. ✨ **Vidu Q3 Pro** — Unique stylized look
> 6. 🌊 **MiniMax H3** — Up to 2K and 15s, fine details
> 7. 🌱 **Seedance 2.5** — Top quality, up to 30s + auto audio + real people, up to 4K

Pick a number to start, or the default (Google Veo 3.1 Fast) is used automatically. All models are discounted, typically 20–70% off.

### Image Model Selection

When generating images, the assistant presents 5 curated models to choose from:

> 1. 🎨 **Nano Banana Pro** — Best overall quality, recommended default
> 2. ⚡ **Nano Banana 2** — Fastest and most affordable
> 3. 🎭 **Midjourney v8** — Cinematic look
> 4. 🤖 **GPT Image 2** — Strong prompt understanding and reliable image editing
> 5. 📷 **Seedream v5 Pro** — ByteDance, strong photorealistic feel

Pick a number to start, or the default (Nano Banana Pro) is used automatically. All models are discounted, typically 20–70% off.

## Architecture

```
runninghub/
├── SKILL.md                        # Skill definition for OpenClaw / DeepSeek Harness (routing table + examples)
├── scripts/
│   ├── runninghub.py               # Standard model API client (420 endpoints)
│   ├── runninghub_app.py           # AI Application client (custom ComfyUI workflows)
│   ├── rhtv.py                     # Experimental RHTV Canvas client
│   ├── catalog_server.py           # Local capabilities and AI App browser
│   └── build_capabilities.py       # Generates capabilities.json from models_registry.json
├── web/
│   └── index.html                  # Capability catalog interface
├── references/
│   └── rhtv-canvas.md              # RHTV routing, auth, and safety boundaries
└── data/
    └── capabilities.json           # Full endpoint catalog (auto-generated)
```

## Script Modes

### Standard Model API (runninghub.py)

| Mode | Command | Purpose |
|------|---------|---------|
| **Check** | `--check` | Verify API key + check wallet balance |
| **List** | `--list [--type T] [--task T]` | Browse available endpoints |
| **Info** | `--info ENDPOINT` | View endpoint parameters |
| **Execute** | `--endpoint EP --prompt "..." -o /tmp/out` | Run with specific endpoint |
| **Auto** | `--task TASK --prompt "..." -o /tmp/out` | Auto-select best endpoint |

### AI Application (runninghub_app.py)

| Mode | Command | Purpose |
|------|---------|---------|
| **Check** | `--check` | Verify API key + check wallet balance |
| **Browse** | `--list [--sort S] [--size N] [--page N]` | Browse recommended/hottest/newest AI apps |
| **Nodes** | `--info WEBAPP_ID` | Show modifiable nodes for an AI app |
| **Execute** | `--run WEBAPP_ID --node ... --file ... -o /tmp/out` | Run an AI application |

### RHTV Canvas (rhtv.py, experimental)

| Mode | Command | Purpose |
|------|---------|---------|
| **Canvas info** | `--info CANVAS_URL_OR_ID` | Read canvas and node summaries |
| **Run node** | `--run-node CANVAS_URL_OR_ID NODE_ID --set ...` | Override fields and run one node |
| **Status** | `--status TASK_ID` / `--wait TASK_ID` | Query or wait for a task |
| **Cancel** | `--cancel TASK_ID` | Cancel a task |

### Local capability browser

Run `python3 runninghub/scripts/catalog_server.py`, then open the local address it prints (normally `http://127.0.0.1:8765`; a nearby free port is selected automatically if needed). The standard catalog works without credentials. Live AI Apps use `RUNNINGHUB_API_KEY`; RHTV readiness uses `RHTV_ACCESS_TOKEN`. Secrets stay in the local server process and are never returned to the page.

## Updating Capabilities

When RunningHub adds new API endpoints, regenerate the catalog:

```bash
python3 scripts/build_capabilities.py \
  --registry /path/to/ComfyUI_RH_OpenAPI/models_registry.json \
  --output data/capabilities.json
```

## License

[Apache-2.0](./LICENSE)
