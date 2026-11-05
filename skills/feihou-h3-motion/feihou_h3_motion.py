#!/usr/bin/env python3
"""FeiHou H3 Motion —— 本地分镜流水线（无外部依赖，只要 ffmpeg/ffprobe）

子命令：
  detect   切镜头，输出 shots.json
  frames   每个分镜抽 3 张关键帧，给视觉模型看
  skeleton 由 shots.json 生成待填写的 prompts.json
  vlm      调 OpenAI 兼容的多模态接口，自动写六段式提示词
  build    由 shots.json + prompts.json 生成 FEIHOU_SHOT 分镜 md（ComfyUI 节点直接读）
  validate 按节点源码的规则校验 md，报错精确到第几行

分镜 md 的格式来自 ComfyUI-FeiHou-Easy-H3/shot_prompts.py（MIT，作者 nkxx188 / FX-FeiHou）。
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import subprocess
import sys

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

MAX_BLOCK_SECONDS = 45.0     # 一个分镜块上限
MAX_SEGMENT_SECONDS = 15.0   # 块内每段上限
MIN_BLOCK_SECONDS = 0.2
SPLIT_STEP = 5.0             # 超过 15 秒的块，每 5 秒一个 @split

SECTIONS = ("subject_definitions", "summary", "retention_analysis",
            "detailed_description", "overall_soundscape", "non_diegetic_music")

SENTINEL = re.compile(r"^=+\s*FEIHOU_SHOT\s+(\d+)\s*=+\s*$", re.IGNORECASE)
DIRECTIVE = re.compile(r"^@([A-Za-z_一-鿿]+)(?:\s+(.*?))?\s*$")
TIME_SPLIT = re.compile(r"\s*[-–—~～至]\s*")
TAG_REST = re.compile(r"@\s*(?:图片|图像|Picture|Image)\s*(\d+)", re.IGNORECASE)


# ── 工具 ──────────────────────────────────────────────────────────────
def run(cmd, capture=True):
    p = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise SystemExit(f"命令失败（{p.returncode}）：{' '.join(cmd[:4])}…\n{p.stderr[-800:]}")
    return (p.stdout or "") + (p.stderr or "")


def probe(path):
    out = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate",
               "-show_entries", "format=duration", "-of", "json", path])
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, den = (st.get("r_frame_rate") or "24/1").split("/")
    return {"width": st.get("width"), "height": st.get("height"),
            "fps": float(num) / float(den or 1),
            "duration": float((d.get("format") or {}).get("duration") or 0)}


def mmss(t):
    return f"{int(t)//60:02d}:{t % 60:05.2f}"


def parse_time(text):
    parts = text.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"时间格式不对：{text}（要 00:03.08 这样）")


# ── detect ───────────────────────────────────────────────────────────
def cmd_detect(a):
    info = probe(a.video)
    out = run([FFMPEG, "-hide_banner", "-i", a.video, "-an",
               "-vf", f"select='gt(scene,{a.threshold})',showinfo",
               "-f", "null", "-"])
    cuts = []
    for m in re.finditer(r"pts_time:([0-9.]+)", out):
        t = float(m.group(1))
        if t > 0.15:
            cuts.append(t)
    cuts = sorted(set(round(c, 3) for c in cuts))
    bounds = [0.0] + cuts + [info["duration"]]
    shots, idx = [], 1
    for s, e in zip(bounds, bounds[1:]):
        if e - s < a.min_shot:      # 太碎的并入上一镜
            if shots:
                shots[-1]["end"] = round(e, 3)
            continue
        shots.append({"index": idx, "start": round(s, 3), "end": round(e, 3)})
        idx += 1
    if shots:
        shots[0]["start"] = 0.0
        shots[-1]["end"] = round(info["duration"], 3)
    data = {"video": os.path.abspath(a.video), "duration": info["duration"],
            "fps": info["fps"], "size": [info["width"], info["height"]],
            "threshold": a.threshold, "shots": shots}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"视频 {mmss(info['duration'])}  {info['width']}x{info['height']} @{info['fps']:.0f}fps")
    print(f"切出 {len(shots)} 个分镜：")
    for s in shots:
        print(f"  #{s['index']:02d}  {mmss(s['start'])}-{mmss(s['end'])}  {s['end']-s['start']:.2f}s")
    if not shots:
        print("没切出镜头，试试降低 --threshold（默认 0.32）")
    return 0


# ── frames ───────────────────────────────────────────────────────────
def cmd_frames(a):
    shots = json.load(open(a.shots, encoding="utf-8"))["shots"]
    os.makedirs(a.out, exist_ok=True)
    made = []
    for s in shots:
        dur = s["end"] - s["start"]
        for k, frac in enumerate((0.12, 0.5, 0.88)[: a.per_shot]):
            t = s["start"] + dur * frac
            path = os.path.join(a.out, f"shot{s['index']:02d}_{k+1}.jpg")
            run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
                 "-i", a.video, "-frames:v", "1", "-q:v", "3", "-y", path])
            made.append(path)
    print(f"抽了 {len(made)} 张 -> {a.out}")
    return 0


# ── skeleton / vlm ───────────────────────────────────────────────────
def cmd_skeleton(a):
    shots = json.load(open(a.shots, encoding="utf-8"))["shots"]
    data = {"video": a.video, "shots": [
        {"index": s["index"], "start": s["start"], "end": s["end"],
         "pictures": a.pictures, "continue": False, "passthrough": False, "prompt": ""}
        for s in shots]}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"模板 -> {a.out}，把每个 shot 的 prompt 填上六段式正文再 build")
    return 0


def _vlm_prompt(shot, guide, extra):
    return f"""{guide}

【本次任务】
这是一个 {shot['end']-shot['start']:.2f} 秒的参考视频片段（第 {shot['index']} 块）。
人物参考图：<Picture 1>。动作参考：<Video 1> 的这一段。
{extra}
请只输出六段式提示词正文（subject_definitions: / summary: / retention_analysis: / detailed_description: / overall_soundscape: / non_diegetic_music:），全部英文，不要 markdown、标题或解释。"""


def cmd_vlm(a):
    base = os.environ.get("FEIHOU_VLM_BASE", "").rstrip("/")
    key = os.environ.get("FEIHOU_VLM_KEY", "")
    model = os.environ.get("FEIHOU_VLM_MODEL", "")
    if not (base and key and model):
        raise SystemExit("先设三个环境变量：FEIHOU_VLM_BASE（如 https://api.xxx.com/v1）、"
                         "FEIHOU_VLM_KEY、FEIHOU_VLM_MODEL（要能看图的模型）")
    import urllib.request
    shots = json.load(open(a.shots, encoding="utf-8"))["shots"]
    guide = open(a.guide, encoding="utf-8").read()
    out = json.load(open(a.prompts, encoding="utf-8"))
    by_idx = {s["index"]: s for s in out["shots"]}
    for s in shots:
        imgs = sorted(p for p in os.listdir(a.frames) if p.startswith(f"shot{s['index']:02d}_"))
        content = [{"type": "text", "text": _vlm_prompt(s, guide, a.extra or "")}]
        for name in imgs:
            with open(os.path.join(a.frames, name), "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        body = json.dumps({"model": model, "temperature": 0.3,
                           "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=180))
            text = resp["choices"][0]["message"]["content"].strip()
            text = re.sub(r"^```[a-z]*\n|```$", "", text).strip()
            by_idx[s["index"]]["prompt"] = text
            print(f"  ✓ 分镜 {s['index']:02d} 写好（{len(text)} 字符）")
        except Exception as e:
            print(f"  ✗ 分镜 {s['index']:02d} 失败：{type(e).__name__} {e}")
    with open(a.prompts, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"写回 {a.prompts}")
    return 0


# ── build ────────────────────────────────────────────────────────────
def _blocks(shot):
    """一个镜头可能超过 45 秒，拆成多个块，后面的标 @continue。"""
    dur = shot["end"] - shot["start"]
    n = max(1, math.ceil(dur / MAX_BLOCK_SECONDS))
    step = dur / n
    return [{"start": shot["start"] + step * i,
             "end": shot["start"] + step * (i + 1),
             "continue": bool(shot.get("continue")) or i > 0} for i in range(n)]


def _splits(block):
    dur = block["end"] - block["start"]
    if dur <= MAX_SEGMENT_SECONDS:
        return []
    marks, t = [], SPLIT_STEP
    while t < dur - 0.5:
        marks.append(round(block["start"] + t, 3))
        t += SPLIT_STEP
    return [m for m in marks if block["end"] - m >= 0.5]


def cmd_build(a):
    meta = json.load(open(a.shots, encoding="utf-8"))
    prompts = {s["index"]: s for s in json.load(open(a.prompts, encoding="utf-8"))["shots"]}
    lines, n, errors = [], 0, []
    for shot in meta["shots"]:
        p = prompts.get(shot["index"])
        if not p:
            errors.append(f"分镜 {shot['index']} 没有 prompt")
            continue
        merged = {**shot, "continue": p.get("continue", False),
                  "passthrough": p.get("passthrough", False)}
        for b in _blocks(merged):
            n += 1
            body = (p.get("prompt") or "").strip()
            pics = p.get("pictures") or [1]
            lines.append(f"===== FEIHOU_SHOT {n:02d} =====")
            lines.append(f"@range {mmss(b['start'])}-{mmss(b['end'])}")
            lines.append("@pictures " + ",".join(str(x) for x in pics))
            if b["continue"]:
                lines.append("@continue")
            if p.get("passthrough"):
                lines.append("@passthrough")
            sp = _splits(b)
            if sp:
                lines.append("@split " + ", ".join(mmss(x) for x in sp))
            lines.append(TAG_REST.sub(r"<Picture \1>", body))
            lines.append("")
    md = "\n".join(lines).rstrip() + "\n"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出 {n} 个分镜块 -> {a.out}")
    if errors:
        print("警告：", "；".join(errors))
    return cmd_validate(argparse.Namespace(md=a.out, duration=meta.get("duration"), zh_warn=False))


# ── validate ─────────────────────────────────────────────────────────
def cmd_validate(a):
    text = open(a.md, encoding="utf-8").read().replace("\r\n", "\n")
    lines = text.split("\n")
    problems, blocks, cur = [], [], None
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        m = SENTINEL.match(line)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"line": i, "index": int(m.group(1)), "dirs": {}, "body": []}
            continue
        if cur is None:
            continue
        d = DIRECTIVE.match(line)
        if d and not cur["body"]:
            cur["dirs"][d.group(1).lower()] = (d.group(2) or "").strip()
        else:
            cur["body"].append(raw)
    if cur:
        blocks.append(cur)
    if not blocks:
        problems.append("没有找到「===== FEIHOU_SHOT 01 =====」分隔行")
    for k, b in enumerate(blocks, 1):
        if b["index"] != k:
            problems.append(f"第 {b['line']} 行：序号应为 {k}，实际 {b['index']}（必须从 1 连续）")
        if "range" not in b["dirs"]:
            problems.append(f"第 {b['line']} 行：分镜 {b['index']} 缺 @range")
            continue
        try:
            s, e = (parse_time(x) for x in TIME_SPLIT.split(b["dirs"]["range"]))
        except Exception as ex:
            problems.append(f"第 {b['line']} 行：@range 解析失败（{ex}）")
            continue
        dur = e - s
        if not (0 <= s < e):
            problems.append(f"第 {b['line']} 行：@range 结束必须大于开始")
        if dur < MIN_BLOCK_SECONDS:
            problems.append(f"第 {b['line']} 行：分镜 {b['index']} 只有 {dur:.2f}s，太短")
        if dur > MAX_BLOCK_SECONDS and "passthrough" not in b["dirs"]:
            problems.append(f"第 {b['line']} 行：分镜 {b['index']} {dur:.1f}s 超过 {MAX_BLOCK_SECONDS:.0f}s 上限")
        if getattr(a, "duration", 0) and e > a.duration + 0.05:
            problems.append(f"第 {b['line']} 行：@{mmss(e)} 超出视频长度 {mmss(a.duration)}")
        body = "\n".join(b["body"]).strip()
        if not body and "passthrough" not in b["dirs"]:
            problems.append(f"第 {b['line']} 行：分镜 {b['index']} 没有提示词正文")
        if body:
            found = [sec for sec in SECTIONS if re.search(rf"^{sec}\s*:", body, re.M)]
            if len(found) < len(SECTIONS):
                missing = [x for x in SECTIONS if x not in found]
                problems.append(f"第 {b['line']} 行：分镜 {b['index']} 六段式缺字段 {missing}")
            if re.search(r"@(图片|图像|Picture|Image)\s*\d", body, re.I):
                problems.append(f"第 {b['line']} 行：分镜 {b['index']} 里还有 @图片n 没换成 <Picture n>")
            if getattr(a, "zh_warn", False) and re.search(r"[一-鿿]", re.sub(r"<[^>]+>", "", body)):
                problems.append(f"第 {b['line']} 行：分镜 {b['index']} 正文夹了中文（H3 要求全英文）")
        if "split" in b["dirs"]:
            try:
                marks = [parse_time(x) for x in re.split(r"[,，;；\s]+", b["dirs"]["split"]) if x]
            except Exception as ex:
                problems.append(f"第 {b['line']} 行：@split 解析失败（{ex}）")
                continue
            prev = s
            for m in marks:
                if not (s < m < e):
                    problems.append(f"第 {b['line']} 行：@split 的 {mmss(m)} 不在 @range 内")
                if m - prev < 0.5:
                    problems.append(f"第 {b['line']} 行：@split 分出的段不足 0.5 秒")
                prev = m
            if e - prev < 0.5:
                problems.append(f"第 {b['line']} 行：最后一段不足 0.5 秒")
    if problems:
        print(f"✗ {len(problems)} 处问题：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"✓ {len(blocks)} 个分镜块，格式校验通过")
    return 0


# ── main ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="FeiHou H3 Motion 本地分镜流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="切镜头")
    d.add_argument("video")
    d.add_argument("--json", default="shots.json")
    d.add_argument("--threshold", type=float, default=0.32)
    d.add_argument("--min-shot", type=float, default=0.5)
    d.set_defaults(func=cmd_detect)

    f = sub.add_parser("frames", help="抽关键帧")
    f.add_argument("video")
    f.add_argument("--shots", default="shots.json")
    f.add_argument("--out", default="frames")
    f.add_argument("--per-shot", type=int, default=3)
    f.set_defaults(func=cmd_frames)

    s = sub.add_parser("skeleton", help="生成待填 prompts.json")
    s.add_argument("--shots", default="shots.json")
    s.add_argument("--out", default="prompts.json")
    s.add_argument("--video", default="")
    s.add_argument("--pictures", type=int, nargs="+", default=[1])
    s.set_defaults(func=cmd_skeleton)

    v = sub.add_parser("vlm", help="调多模态模型写提示词")
    v.add_argument("--shots", default="shots.json")
    v.add_argument("--prompts", default="prompts.json")
    v.add_argument("--frames", default="frames")
    v.add_argument("--guide", required=True)
    v.add_argument("--extra", default="")
    v.set_defaults(func=cmd_vlm)

    b = sub.add_parser("build", help="生成分镜 md")
    b.add_argument("--shots", default="shots.json")
    b.add_argument("--prompts", default="prompts.json")
    b.add_argument("--out", default="shotlist.md")
    b.set_defaults(func=cmd_build)

    val = sub.add_parser("validate", help="校验分镜 md")
    val.add_argument("md")
    val.add_argument("--duration", type=float, default=0)
    val.add_argument("--zh-warn", action="store_true", help="正文夹中文也报错")
    val.set_defaults(func=cmd_validate)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
