# dsh-skills

DSH 手机版上自建的技能与脚本。

## skills/feihou-h3-motion
MiniMax H3 / FeiHou Easy H3 动作迁移的分镜流水线：切镜头、抽关键帧、写六段式提示词、
生成 ComfyUI「分镜提示词」节点能直接读的 FEIHOU_SHOT 分镜 md（含 45s 分块、@continue、@split、格式校验）。

## skills/runninghub-api
RunningHub（runninghub.cn）工作流 API 客户端：提交工作流、轮询进度、取结果直链、自动下载。

## scripts
- `asr_sensevoice.py` — 用 SenseVoice ONNX 在本地转写音视频（低内存可用）
- `BV1x7xq62EB1-转写.md` — 一次实际转写的产物，作为示例

## 出处与许可
- `skills/feihou-h3-motion/guide.md` 取自 [FX-FeiHou/ComfyUI-FeiHou-Easy-H3](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Easy-H3)（MIT），
  版权归原作者，此处仅作提示词规范参考。分镜 md 的格式规则来自该仓库的 `shot_prompts.py`。
- 其余代码为本仓库自写，可自由使用。
