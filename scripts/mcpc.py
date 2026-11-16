#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proteus MCP 最小客户端 —— 纯标准库，零依赖。

Proteus 9 的 ProPilot 会在本机开一个 JSON-RPC over HTTP 的 MCP 端点
（默认 http://127.0.0.1:8001/），暴露约 98 个工具：放元件、放端子、
整网布线、读网表、导出图纸、跑仿真、读波形、写固件。

本脚本不需要 MCP SDK，不需要配置 mcp.json，任何能执行 shell 的 agent
都可以直接驱动 Proteus 画图。

子命令
------
    probe                 扫描 8001/8000/8002，报告哪个端口是活的 MCP
    status                打印当前工程 + design_id / sheet_id
    tools [过滤词]         列出工具（可按关键字过滤）
    schema <tool>         打印某个工具的完整参数说明
    call <tool> [json]    调用工具，打印结果
    image <out.svg>       导出当前图纸（SVG）

示例
----
    python mcpc.py probe
    python mcpc.py call get_current_status
    python mcpc.py call schematic_get_netlist '{"id":"1","sheet":"1"}'
    python mcpc.py image sheet.svg

环境变量
--------
    PROTEUS_MCP_URL       端点，默认 http://127.0.0.1:8001/
    PROTEUS_MCP_TIMEOUT   单次调用超时秒数，默认 120
"""

import json
import os
import sys
import textwrap
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("PROTEUS_MCP_URL", "http://127.0.0.1:8001/")
DEFAULT_TIMEOUT = float(os.environ.get("PROTEUS_MCP_TIMEOUT", "120"))

# 本机回环地址绝不能走系统代理：环境里的 HTTP_PROXY 会把 127.0.0.1 的请求
# 转给代理端口，表现是莫名其妙的 502 而不是连接失败。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class McpError(RuntimeError):
    pass


def _decode(raw):
    """兼容两种响应体：裸 JSON，或 SSE 的 data: {...} 流。"""
    raw = raw.strip()
    if not raw:
        raise McpError("empty response from MCP endpoint")
    if raw.startswith("{"):
        return json.loads(raw)
    chunks = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunks.append(line[5:].strip())
    if not chunks:
        raise McpError("cannot parse response: " + raw[:300])
    return json.loads("\n".join(chunks))


def rpc(method, params=None, url=None, timeout=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(url or DEFAULT_URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    try:
        with _OPENER.open(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            return _decode(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise McpError("HTTP %s: %s" % (exc.code, exc.read()[:200]))
    except urllib.error.URLError as exc:
        raise McpError("cannot reach %s (%s)"
                       % (url or DEFAULT_URL, getattr(exc, "reason", exc)))


def call(tool, args=None, url=None, timeout=None):
    """调用一个 MCP 工具。成功返回解析后的 dict，失败抛 McpError。"""
    res = rpc("tools/call", {"name": tool, "arguments": args or {}}, url, timeout)
    if "error" in res:
        raise McpError("%s: %s" % (tool, json.dumps(res["error"], ensure_ascii=False)[:400]))
    result = res.get("result") or {}
    content = result.get("content") or []
    if not content:
        return result
    text = content[0].get("text", "")
    # 业务错误不带 isError 标志，只体现在这个前缀上
    if text.startswith("Error"):
        raise McpError(text)
    try:
        return json.loads(text)
    except ValueError:
        return {"_text": text}


def ids(url=None):
    """取当前 design_id / sheet_id。不要硬编码，重开工程可能变。"""
    st = call("get_current_status", {}, url)
    try:
        sch = st["data"]["status"]["schematic"]
        return str(sch["current_design_id"]), str(sch["current_sheet_id"])
    except (KeyError, TypeError):
        raise McpError("get_current_status 未返回 design/sheet id: %r" % (st,))


def list_tools(url=None):
    res = rpc("tools/list", url=url)
    return (res.get("result") or {}).get("tools", [])


# ---------------------------------------------------------------- CLI

def cmd_probe(argv):
    live = []
    for port in (8001, 8000, 8002):
        url = "http://127.0.0.1:%d/" % port
        try:
            tools = list_tools(url)
            print("  %-24s OK   %d tools" % (url, len(tools)))
            live.append(url)
        except McpError as exc:
            print("  %-24s --   %s" % (url, str(exc).splitlines()[0][:80]))
    print()
    if live:
        print("可用端点: %s" % live[0])
        print("如果不在 8001，设 PROTEUS_MCP_URL=%s" % live[0])
        return 0
    print("没有找到 MCP 端点。逐条排查：")
    print("  1. Proteus 是否已经启动并加载了工程")
    print("  2. ProPilot（AI 助手）面板是否启用过")
    print("  3. 是否被防火墙/杀软拦了本地回环监听")
    return 1


def cmd_status(argv):
    st = call("get_current_status", {})
    print(json.dumps(st, ensure_ascii=False, indent=2))
    try:
        d, s = ids()
        print("\ndesign_id=%s  sheet_id=%s" % (d, s))
    except McpError as exc:
        print("\n" + str(exc))
    return 0


def cmd_tools(argv):
    kw = argv[0].lower() if argv else ""
    tools = list_tools()
    hit = [t for t in tools if kw in t["name"].lower()]
    for t in hit:
        print("%-34s %s" % (t["name"], (t.get("description") or "").splitlines()[0][:90]))
    print("\n%d / %d tools" % (len(hit), len(tools)))
    return 0


def cmd_schema(argv):
    if not argv:
        print("用法: schema <tool>")
        return 2
    name = argv[0]
    tools = list_tools()
    hit = [t for t in tools if t["name"] == name]
    if not hit:
        near = [t["name"] for t in tools if name in t["name"]][:10]
        print("没有 %s。相近的: %s" % (name, ", ".join(near) or "无"))
        return 1
    t = hit[0]
    print("### %s" % t["name"])
    print(t.get("description") or "")
    schema = t.get("inputSchema") or {}
    print("\nrequired: %s" % (schema.get("required") or []))
    for key, spec in (schema.get("properties") or {}).items():
        kind = spec.get("type") or ("enum" if spec.get("enum") else "?")
        print("\n  %s (%s)" % (key, kind))
        if spec.get("enum"):
            print("    enum: %s" % ", ".join(map(str, spec["enum"])))
        desc = (spec.get("description") or "").replace("\n", " ")
        if desc:
            for line in textwrap.wrap(desc, 96):
                print("    " + line)
    return 0


def cmd_call(argv):
    if not argv:
        print("用法: call <tool> [json-args]")
        return 2
    tool = argv[0]
    args = json.loads(argv[1]) if len(argv) > 1 else {}
    print(json.dumps(call(tool, args), ensure_ascii=False, indent=2))
    return 0


def cmd_image(argv):
    out = argv[0] if argv else "sheet.svg"
    d, s = ids()
    res = call("schematic_get_image", {"id": d, "sheet": s})
    svg = res.get("_text") or res.get("svg") or ""
    if not svg.lstrip().startswith("<"):
        print("没有拿到 SVG，返回: %s" % json.dumps(res, ensure_ascii=False)[:300])
        return 1
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print("写入 %s (%d bytes)" % (out, len(svg)))
    print("注意：这是矢量 XML，不是位图；要 PNG 请用系统截屏，见 AGENTS.md")
    return 0


COMMANDS = {
    "probe": cmd_probe,
    "status": cmd_status,
    "tools": cmd_tools,
    "schema": cmd_schema,
    "call": cmd_call,
    "image": cmd_image,
}


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = COMMANDS.get(argv[1])
    if not cmd:
        print("未知子命令 %s，可用: %s" % (argv[1], ", ".join(COMMANDS)))
        return 2
    try:
        return cmd(argv[2:])
    except McpError as exc:
        print("FAIL: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
