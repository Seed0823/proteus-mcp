---
name: proteus-mcp
description: 通过 MCP 直接驱动 Proteus（ISIS/VSM）画原理图、放元件、布网络、读网表、跑仿真，替代脆弱的鼠标键盘自动化。当用户要在 Proteus 里画电路、改电路、检查连线、批量放置器件、验证仿真结果、导出图纸，或抱怨"手动点太慢""鼠标点不准"时使用。关键词：Proteus、MCP、原理图、网表、74LS181、布线、仿真。
---

# Proteus MCP

Proteus 的 ProPilot 在 `http://127.0.0.1:8001/` 暴露 MCP（JSON-RPC over HTTP，98 个工具）。
**不要用鼠标自动化**，直接调接口。

## 开工三步

```bash
python scripts/mcpc.py probe      # 找活端点
python scripts/mcpc.py status     # 取 design_id / sheet_id（不要硬编码）
python scripts/mcpc.py schema schematic_route_nets   # 拿实时参数
```

## 核心套路

```
begin_transaction → place_device ×N（74LS 必带 value）
→ place_terminal(DEFAULT + label + connect:true) 打标号
→ route_nets 逐网调用（一次一个 net）
→ end_transaction → verify.py 核对网表 → runctrl/run 验证功能
```

## 三条最容易踩的

1. 系统代理会把 `127.0.0.1` 转出去报 **502** → 客户端必须禁用代理。
2. 业务错误藏在 `content[0].text` 的 `Error:` 前缀里，**没有 `isError`**。
3. GND/VCC 不能用电源端子走线（自动吸附导致短路）→ 用 `DEFAULT` 标号，
   多引脚电源轨用 `route_nets` 的 `terminals` 字段。

完整规程、坐标约定、11 条踩坑、报错对照表见仓库根目录 **`AGENTS.md`**。
可直接跑的完整案例：`examples/alu4bit.py`（4 位 ALU，74LS181 + 4×74LS373 + LED）。

## 交付前必做

```bash
python scripts/verify.py --refs U1,U5 --expect "U1.A0=U2.Q0,U1.F0=U5.D0"
```

退出码 0 才算画完。要给用户看图：MCP 只回 SVG，位图请系统截屏。
