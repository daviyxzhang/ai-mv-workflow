#!/usr/bin/env python3
"""RunningHub 工作流 API —— 提交 / 轮询 / 捞结果链接 / 下载

用法：
  export RH_API_KEY=你的密钥
  # 提交并等待，最后打印结果链接（视频/图片直链）
  python3 rh.py run --workflow-id 1234567890 \
      --node 6:image=./me.png --node 12:text="跳舞" --download out.mp4

  # 只用 --node 覆盖部分输入；不传的节点用工作流里保存的默认值
  python3 rh.py status <taskId>
  python3 rh.py outputs <taskId>

--node 语法：<nodeId>:<fieldName>=<value>
  值可以是文件路径（自动上传拿 fileName）、URL、纯文本、数字、true/false
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("RH_API_BASE", "https://www.runninghub.cn").rstrip("/")
KEY = os.environ.get("RH_API_KEY", "").strip()
UA = "Mozilla/5.0 (Linux; Android 14) dsh-rh/1.0"


def _post(path, payload, timeout=40):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise SystemExit(f"HTTP {e.code} {path}\n{body[:500]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {path}：{e}")


def upload(path):
    """上传本地文件，返回 fileName（用于填 nodeInfoList）。"""
    import mimetypes, urllib.request as u
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise SystemExit(f"文件不存在：{p}")
    boundary = "----dshRH" + str(int(time.time()))
    ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
    with open(p, "rb") as f:
        blob = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{os.path.basename(p)}\"\r\nContent-Type: {ctype}\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = u.Request(f"{BASE}/openapi/v2/media/upload/binary", data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                             "User-Agent": UA, "Authorization": f"Bearer {KEY}"})
    with u.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    name = d.get("fileName") or d.get("data", {}).get("fileName")
    if not name:
        raise SystemExit(f"上传返回里没有 fileName：{json.dumps(d)[:300]}")
    print(f"  上传 {os.path.basename(p)} -> {name}")
    return name


def coerce(value):
    if os.path.isfile(value) and os.path.getsize(value) > 4096:
        return upload(value)
    v = value.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v) if v.lstrip("-").isdigit() else float(v)
    except ValueError:
        return v


def build_nodes(specs):
    nodes = []
    for spec in specs:
        if "=" not in spec or ":" not in spec.split("=", 1)[0]:
            raise SystemExit(f"--node 格式应为 nodeId:fieldName=value，收到 {spec}")
        left, value = spec.split("=", 1)
        node_id, field = left.split(":", 1)
        nodes.append({"nodeId": node_id.strip(), "fieldName": field.strip(),
                      "fieldValue": coerce(value)})
    return nodes


def submit(workflow_id, nodes, extra):
    payload = {"apiKey": KEY, "workflowId": str(workflow_id), "nodeInfoList": nodes}
    payload.update(extra or {})
    d = _post("/task/openapi/create", payload)
    if d.get("code") != 0:
        raise SystemExit(f"提交失败 code={d.get('code')} {d.get('msg')}")
    tid = (d.get("data") or {}).get("taskId") or d.get("taskId")
    print(f"提交成功 taskId={tid}")
    return tid


def wait(tid, interval, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = _post("/task/openapi/status", {"apiKey": KEY, "taskId": tid})
        st = (d.get("data") or d.get("status") or "").upper()
        print(f"  [{int(time.time()-t0):3d}s] {st}")
        if st in ("SUCCESS", "SUCCEED", "SUCCEEDED"):
            return True
        if st in ("FAILED", "ERROR", "CANCEL", "CANCELED"):
            raise SystemExit(f"任务失败：{st} {d.get('msg') or d.get('errorMessage') or ''}")
        time.sleep(interval)
    raise SystemExit(f"超时 {timeout}s 仍未完成（taskId={tid}，可稍后用 status 再查）")


def outputs(tid):
    d = _post("/task/openapi/outputs", {"apiKey": KEY, "taskId": tid})
    if d.get("code") not in (0, None):
        raise SystemExit(f"取结果失败 code={d.get('code')} {d.get('msg')}")
    items = d.get("data") or d.get("results") or []
    if isinstance(items, dict):
        items = [items]
    links = []
    for it in items:
        url = it.get("fileUrl") or it.get("url") or it.get("outputUrl")
        if url:
            links.append((it.get("fileType") or it.get("type") or "", url))
    return links


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        f.write(r.read())
    print(f"已下载 {path}（{os.path.getsize(path)/1048576:.1f} MB）")


def need_key():
    if not KEY:
        raise SystemExit("先设置环境变量 RH_API_KEY=你在 RunningHub 个人中心生成的 API 密钥")
    return KEY


def main():
    ap = argparse.ArgumentParser(description="RunningHub 工作流 API 客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="提交并等结果")
    r.add_argument("--workflow-id", required=True)
    r.add_argument("--node", action="append", default=[], metavar="NODE:FIELD=VALUE")
    r.add_argument("--extra", default="", help="额外字段的 JSON，如 {\"addMetadata\":true}")
    r.add_argument("--interval", type=int, default=5)
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--download", default="")

    s = sub.add_parser("status", help="查状态")
    s.add_argument("task_id")

    o = sub.add_parser("outputs", help="取结果链接")
    o.add_argument("task_id")

    a = ap.parse_args()
    if a.cmd != "help":
        need_key()
    if a.cmd == "status":
        print(json.dumps(_post("/task/openapi/status", {"apiKey": KEY, "taskId": a.task_id}),
                         ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "outputs":
        for t, u in outputs(a.task_id):
            print(t, u)
        return 0

    extra = json.loads(a.extra) if a.extra else None
    tid = submit(a.workflow_id, build_nodes(a.node), extra)
    wait(tid, a.interval, a.timeout)
    links = outputs(tid)
    if not links:
        print("没有结果链接，原始返回可能为空")
        return 1
    print("\n结果链接：")
    for t, u in links:
        print(f"  [{t}] {u}")
    if a.download:
        download(links[0][1], a.download)
    return 0


if __name__ == "__main__":
    sys.exit(main())
