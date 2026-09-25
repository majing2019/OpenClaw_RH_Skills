# RHClaw — RunningHub Skill for OpenClaw / DeepSeek Harness

[English](./README_en.md)

> ## 现已支持 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
>
> 与 [OpenClaw](https://github.com/openclaw/openclaw) 共用同一套标准 `SKILL.md`，安装和更新方式相同。

为 OpenClaw 和 DeepSeek Harness 打造的通用多媒体生成技能，由 [RunningHub](https://www.runninghub.ai) API 驱动。

**420 个标准 API 端点 + 无限 AI 应用 + RHTV Canvas（实验性）**，覆盖图片、视频、音频、3D 模型生成、多模态文本理解、用户创建的 AI 应用，以及 RHTV 画布节点执行。

> RHTV Canvas 使用网页账户访问令牌，不使用标准 RunningHub API Key。相关接口并非公开 OpenAPI，兼容性按实验性能力提供。

## 能力一览

| 类别 | 端点数 | 支持任务 |
|------|--------|----------|
| **图片** | 102 | 文生图、图生图、图片放大、Midjourney 风格 |
| **视频** | 230 | 文生视频、图生视频、首尾帧生成、视频续写/编辑、运动控制、多模态视频 |
| **音频** | 20 | 文字转语音、音乐生成、声音克隆 |
| **3D** | 16 | 文字转 3D、图片转 3D、多图转 3D |
| **文本** | 52 | 图片理解、视频理解、文本处理 |
| **AI 应用** | 无限 | 运行任意 RunningHub AI 应用（自定义 ComfyUI 工作流） |

## 快速开始

### 安装

在 OpenClaw 或 DeepSeek Harness 对话中发送：

> 从 https://github.com/HM-RunningHub/OpenClaw_RH_Skills 安装 RunningHub 技能

助手会自动克隆仓库、复制文件到工作区，并引导你完成 API Key 配置。

### 更新

当技能有新版本时，在 OpenClaw 或 DeepSeek Harness 对话中发送：

> 从 https://github.com/HM-RunningHub/OpenClaw_RH_Skills 更新 并重新读取@runninghub/SKILL.md

助手会拉取最新代码并重新加载技能配置，无需重新输入 API Key。

### 前置条件

- **API Key** — 在 [RunningHub API 管理页面](https://www.runninghub.ai/enterprise-api/sharedApi) 创建（点击"新建"）
- **账户余额** — [前往充值](https://www.runninghub.ai/vip-rights/4)，API 调用需要余额

## 使用方式

安装完成后，直接用自然语言跟助手对话即可：

- *"帮我画一只在公园里玩耍的小狗"*
- *"把这张照片做成视频"*
- *"给我的视频配个背景音乐"*
- *"把这张图放大到 4K"*
- *"把这张图转成 3D 模型"*
- *"帮我跑这个 AI 应用 https://www.runninghub.ai/ai-detail/1877265245566922800"*
- *"查看这个 RHTV 画布 https://rhtv.runninghub.ai/project/canvas/2103485063252774914"*
- *"最热门的 AI 应用有哪些？"*
- *"推荐一些最新的 AI 应用"*

助手会自动选择最合适的 RunningHub 端点来完成你的请求；如果是 AI 应用，则获取应用节点信息、引导你设置参数并运行；还可以浏览推荐、最热、最新的 AI 应用。

### 视频生成交互

生成视频时，助手会展示 7 个精选模型让你选择：

> 1. 🚀 **Google Veo 3.1 Fast** — 又快效果又好，性价比之王
> 2. 🔥 **Grok Video** — Grok 驱动，画面想象力超强
> 3. 🎯 **Kling v3.0 Pro** — 运动自然，拍人物首选
> 4. 🎬 **Google Veo 3.1 Pro** — 电影感拉满
> 5. ✨ **Vidu Q3 Pro** — 风格化独特
> 6. 🌊 **MiniMax H3** — 最高2K、最长15秒，画面细腻
> 7. 🌱 **Seedance 2.5** — 效果超赞，最长30秒+自动配音+支持真人，最高4K

选个数字就能开始生成，不选默认用 Google Veo 3.1 Fast。所有模型均有折扣，大约 2–7 折。

### 图片生成交互

生成图片时，助手会展示 5 个精选模型让你选择：

> 1. 🎨 **Nano Banana Pro** — 默认推荐，综合效果最好
> 2. ⚡ **Nano Banana 2** — 最快最便宜
> 3. 🎭 **Midjourney v8** — 欧美大片质感
> 4. 🤖 **GPT Image 2** — GPT image2 同款，语义理解强，改图也很稳
> 5. 📷 **Seedream v5 Pro** — 字节跳动出品，写实照片感超强

选个数字就能开始生成，不选默认用 Nano Banana Pro。所有模型均有折扣，大约 2–7 折。

## 项目结构

```
runninghub/
├── SKILL.md                        # 技能定义（OpenClaw / DeepSeek Harness，路由表 + 示例 + 交互规则）
├── scripts/
│   ├── runninghub.py               # 标准模型 API 客户端（420 端点）
│   ├── runninghub_app.py           # AI 应用客户端（自定义 ComfyUI 工作流）
│   ├── rhtv.py                     # RHTV Canvas 客户端（实验性）
│   ├── catalog_server.py           # 本地能力与 AI 应用浏览器
│   └── build_capabilities.py       # 从 models_registry.json 生成 capabilities.json
├── web/
│   └── index.html                  # 能力浏览器界面
├── references/
│   └── rhtv-canvas.md              # RHTV 路由、鉴权和安全边界
└── data/
    └── capabilities.json           # 完整端点目录（自动生成）
```

## 脚本模式

### 标准模型 API（runninghub.py）

| 模式 | 命令 | 用途 |
|------|------|------|
| **检查** | `--check` | 验证 API Key + 查询余额 |
| **列表** | `--list [--type T] [--task T]` | 浏览可用端点 |
| **详情** | `--info ENDPOINT` | 查看端点参数 |
| **执行** | `--endpoint EP --prompt "..." -o /tmp/out` | 使用指定端点执行 |
| **自动** | `--task TASK --prompt "..." -o /tmp/out` | 自动选择最佳端点 |

### AI 应用（runninghub_app.py）

| 模式 | 命令 | 用途 |
|------|------|------|
| **检查** | `--check` | 验证 API Key + 查询余额 |
| **浏览** | `--list [--sort S] [--size N] [--page N]` | 浏览推荐/最热/最新 AI 应用 |
| **节点** | `--info WEBAPP_ID` | 查看 AI 应用的可修改节点 |
| **执行** | `--run WEBAPP_ID --node ... --file ... -o /tmp/out` | 运行 AI 应用 |

### RHTV Canvas（rhtv.py，实验性）

| 模式 | 命令 | 用途 |
|------|------|------|
| **画布信息** | `--info CANVAS_URL_OR_ID` | 读取画布和节点摘要 |
| **运行节点** | `--run-node CANVAS_URL_OR_ID NODE_ID --set ...` | 覆盖参数并运行一个节点 |
| **状态** | `--status TASK_ID` / `--wait TASK_ID` | 查询或等待任务 |
| **取消** | `--cancel TASK_ID` | 取消任务 |

RHTV 使用 `RHTV_ACCESS_TOKEN` 环境变量；不要把访问令牌写入命令行、日志或聊天消息。

### 本地能力浏览器

```bash
python3 runninghub/scripts/catalog_server.py
```

然后打开终端打印的本地地址（通常是 `http://127.0.0.1:8765`；若端口被占用会自动选择相邻空闲端口）。标准能力目录无需密钥；实时 AI 应用需要 `RUNNINGHUB_API_KEY`，RHTV 状态使用 `RHTV_ACCESS_TOKEN`。密钥只保留在本地服务进程中，不会发送给网页。

## 更新能力目录

当 RunningHub 上线新的 API 端点时，重新生成目录：

```bash
python3 scripts/build_capabilities.py \
  --registry /path/to/ComfyUI_RH_OpenAPI/models_registry.json \
  --output data/capabilities.json
```

## 许可证

[Apache-2.0](./LICENSE)
