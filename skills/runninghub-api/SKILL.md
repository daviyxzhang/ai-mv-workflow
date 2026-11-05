---
name: runninghub-api
description: 调 RunningHub（runninghub.cn）工作流 API：提交工作流、轮询进度、把结果视频/图片的下载直链捞回来、可选自动下载。用户要在 RunningHub 上跑云端 ComfyUI 工作流（MiniMax H3、图生视频、动作迁移等），或要拿到生成结果的链接时用它。Submit a RunningHub cloud workflow, poll it, and retrieve the output download links.
whenToUse: 用户要跑 RunningHub 上的工作流、查任务进度、取生成结果链接、或用云端 GPU 跑 ComfyUI 而不想本地装模型时。也用于 RunningHub API 报错排查（code 301/380/806 之类）。
---

# RunningHub 工作流 API

脚本：`rh.py`（本目录，只用 Python 标准库）

## 准备

```bash
export RH_API_KEY=你的密钥      # 个人中心 → API 密钥
```

**workflowId 必须是「你自己账号里」的工作流 ID**，不是网页 `post/<id>` 的 ID。
看到别人分享的工作流，要先在 RunningHub 上「复制 / 一键同款」到自己账号，再从自己那份里拿 ID。

## 用法

```bash
S=~/.dsh/skills/runninghub-api/rh.py

# 提交 + 等结果 + 打印直链（--node 覆盖输入，不传的用工作流里存的默认值）
python3 $S run --workflow-id 1234567890123456789 \
    --node 6:image=./me.png \
    --node 12:text="跳舞" \
    --download out.mp4

# 只查状态 / 只取链接（任务提交后进程断了也能捞回来）
python3 $S status <taskId>
python3 $S outputs <taskId>
```

`--node` 语法：`<nodeId>:<fieldName>=<value>`
- 值是**已存在的文件** → 自动上传，拿回 `fileName`
- 值是 URL / 文本 / 数字 / `true` / `false` → 原样传（会自动转类型）

nodeId 和 fieldName 来自工作流的 node 定义（在 RunningHub 工作流编辑器里点开节点看，或导出工作流 JSON 看）。

## 接口契约（2026-11 实测）

| 用途 | 端点 | 请求体 |
|---|---|---|
| 提交 | `POST /task/openapi/create` | `{apiKey, workflowId, nodeInfoList:[{nodeId,fieldName,fieldValue}]}` |
| 状态 | `POST /task/openapi/status` | `{apiKey, taskId}` |
| 结果 | `POST /task/openapi/outputs` | `{apiKey, taskId}` → `data[].fileUrl` |
| 上传 | `POST /openapi/v2/media/upload/binary` | multipart，`Authorization: Bearer <key>` → `fileName` |

提交返回 `data.taskId`。状态取值：`SUCCESS` 终态成功；`FAILED` / `CANCEL` 终态失败；`QUEUED` / `RUNNING` 继续等。

## 错误码

| code | 含义 | 怎么办 |
|---|---|---|
| 301 | must not be null（缺必填参数） | 检查 workflowId / nodeInfoList |
| 380 | workflow not exists | workflowId 不是你账号的，先复制工作流 |
| 806 | APIKEY_USER_NOT_FOUND | 密钥错或没有这个账号，重新生成 |
| 1001 | Invalid URL | 端点路径写错了 |
| 1007 | must not be null（v2 接口） | 缺参数 |

## 注意

- 按次扣费，余额不足会提交失败，不是网络问题。
- `post/<id>` 页面里的 `webappId` **不是** API 用的 workflowId。
- 结果链接是云端临时地址，尽快下载。
- 这是「工作流 API」；另有一套「标准模型 API」（484 个现成模型，`/openapi/v2/<endpoint>`，Bearer 鉴权，提交后 `POST /openapi/v2/query` 轮询），不用自己搭工作流，但跑不了自定义节点的工作流。
