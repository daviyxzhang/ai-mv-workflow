---
name: feihou-h3-motion
description: 肥猴 MiniMax H3 动作迁移/人物替换的分镜流水线：把原视频切成镜头、抽关键帧、写出六段式提示词，生成 ComfyUI「分镜提示词」节点能直接读的 FEIHOU_SHOT 分镜 md。用户要做 H3 人物替换长视频、或拿到一个分镜 md 要修，先用它。Local reimplementation of the paid FeiHou H3 Motion skill — shot detection, keyframes, six-section prompts, md build and validation.
whenToUse: 用户要做 MiniMax H3 / FeiHou Easy H3 的人物替换或动作迁移长视频，需要切分镜、写分镜提示词 md；或已有分镜 md 需要校验/修格式。不用于图片生成或别的工作流。
---

# FeiHou H3 Motion（本地版）

把「原视频 → 分镜 md」这一步在本地跑完，产出的 md 直接喂给 ComfyUI 的
`FeiHou Easy H3 · 分镜提示词` 节点（`shot_prompts.py`）。

## 为什么是本地版

原版 Skill 是 UP 主付费包里的，网盘要登录且属充电会员专享。这里是按
`ComfyUI-FeiHou-Easy-H3` 自带的 `prompt_guides/feihou_h3_motion/guide.md`
（六段式规范）和 `shot_prompts.py`（解析规则）重建的，行为对齐节点要求。

## 依赖

只要 `ffmpeg` / `ffprobe`。没有 Python 第三方包。

写提示词那一步需要**能看图的模型**：

- **当前对话模型支持视觉**（如 qwen3.8-flash、mimo-v2.6-pro）：不用 `vlm` 子命令——
  我直接 `read_image` 看 `frames` 里的关键帧，按 `guide.md` 手写六段式，填进 prompts.json 后 `build`。这是首选路径。
- 当前模型看不了图时，才用 `vlm` 调外部 OpenAI 兼容多模态接口。

## 用法

脚本：`feihou_h3_motion.py`（本目录）

```bash
S=~/.dsh/skills/feihou-h3-motion/feihou_h3_motion.py

# 1. 切镜头（阈值默认 0.32，切太碎就调高，漏切就调低）
python3 $S detect 原视频.mp4 --json shots.json

# 2. 每个镜头抽 3 张关键帧
python3 $S frames 原视频.mp4 --shots shots.json --out frames

# 3a. 生成待填模板（自己写提示词时用）
python3 $S skeleton --shots shots.json --out prompts.json --pictures 1

# 3b. 或让多模态模型写（推荐）
export FEIHOU_VLM_BASE=https://api.example.com/v1
export FEIHOU_VLM_KEY=sk-xxx
export FEIHOU_VLM_MODEL=某个能看图的模型
python3 $S vlm --shots shots.json --prompts prompts.json \
    --frames frames --guide ./guide.md \
    --extra "主人物用吴孟达；原片里的女人换成赵薇"

# 4. 生成 md（自动处理 45 秒分块、@continue、@split）
python3 $S build --shots shots.json --prompts prompts.json --out shotlist.md

# 5. 单独校验（改完 md 之后跑一遍）
python3 $S validate shotlist.md --duration 1700
```

## 节点要求的硬规则（脚本已自动处理，人工改 md 时别破坏）

- 每个分镜块以 `===== FEIHOU_SHOT 01 =====` 开头，**序号从 1 连续递增**
- `@range 00:03.08-00:07.21`：参考视频的时间轴，**必须起小于止**
- `@pictures 1` / `@pictures 1,3`：这一块用哪几张参考图
- **单块 ≤ 45 秒**，超过要拆块，后续块加 `@continue`
- 块内超过 15 秒要加 `@split 00:05.00, 00:10.00`，**每 5 秒一个切点**，每段 ≥ 0.5 秒
- `@passthrough`（原片直出）的单块 ≤ 20 秒
- 可选：`@sound source|generated`、`@first N`、`@video none`；文件头可写 `@hold 26.5`、`@audio 分块`

## 提示词正文（六段式，全英文）

字段顺序固定，各出现一次：

```
subject_definitions:
summary:            ← 以 [reference generation + audio reuse] 开头
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

要点（详见 `guide.md`）：

- **不写时间戳、不写具体切点**，只写「任何片段都成立」的规则
- 动作只写一句「<Subject 1> 照着 <Video 1> 表演」，不列动作清单
- 不要写「首帧与 <Video 1> 第一帧相同」——会把原片画面复制进开头
- 正文里不能出现 `@图片1` 这类引用，一律用 `<Picture 1>`
- 写实素材要在 detailed_description 第一句写真实画质
- 跳舞/表演要写「会变化的表情」并给 4–6 种，不标时间点
- 有场景图或文字背景时，`subject_definitions` 末尾必须有一行点名所有人物图
  「只提供长相和服装，底色不进画面」，`[Shot 1]` 第一句写「从第一帧起画面就是场景」

## 交付前自检

`validate` 过了只说明格式对；内容层面请再对照 `guide.md` 的【八、交付前自行核对】12 条。
