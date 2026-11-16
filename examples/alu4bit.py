#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例：用 MCP 从零构建一个 4 位 ALU（74LS181 + 4×74LS373 + LED）。

电路
----
    SW_A0..A3 ─→ U2(373) ─→ 74LS181.A      操作数 A 锁存
    SW_B0..B3 ─→ U3(373) ─→ 74LS181.B      操作数 B 锁存
    SW_S0..S3 ─→ U4(373) ─→ 74LS181.S3..S0 功能选择锁存
    SW_M      ─→ U4.D4   ─→ 74LS181.M      方式控制（M=1 逻辑，M=0 算术）
    SW_CN     ─────────────────→ 74LS181.CN 进位输入（直连）
    74LS181.F ─→ U5(373) ─→ LED0..3        结果锁存 + 显示
    SW_LOAD   ─→ 四片 373 的 LE（LE=1 透明，LE=0 保持）

运行
----
    # 先在 Proteus 里新建一个空白工程并保存，然后：
    python examples/alu4bit.py            # 构建
    python scripts/verify.py --refs U1,U2,U3,U4,U5

坐标单位是 thou（100 thou = 0.1 英寸 = 1 个网格），原点在图纸中心，
所以会有负坐标。这是我第一次成功布通这一版图时用的位置，直接沿用。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from mcpc import McpError, call, ids  # noqa: E402

# 元件：(库名称, x, y, 位号)
ICS = [
    ("74LS181", -600, 2200, "U1"),
    ("74LS373", -4200, 2900, "U2"),   # A 锁存
    ("74LS373", -4200, -600, "U3"),   # B 锁存
    ("74LS373", -4200, 1000, "U4"),   # S/M 锁存
    ("74LS373", 1500, 2100, "U5"),    # 结果锁存
]
SWX = -6200                          # 开关列
SW = [
    ("SW_A0", SWX, 3000), ("SW_A1", SWX, 2500), ("SW_A2", SWX, 2000), ("SW_A3", SWX, 1500),
    ("SW_B0", SWX, -1500), ("SW_B1", SWX, -2000), ("SW_B2", SWX, -2500), ("SW_B3", SWX, -3000),
    ("SW_S0", SWX, 1000), ("SW_S1", SWX, 500), ("SW_S2", SWX, 0), ("SW_S3", SWX, -500),
    ("SW_M", SWX, -1000), ("SW_CN", -600, -100), ("SW_LOAD", 2400, -600),
]
LED = [("LED0", 3200, 1900), ("LED1", 3900, 1900), ("LED2", 4600, 1900), ("LED3", 5300, 1900)]
LATCHES = ["U2", "U3", "U4", "U5"]

# 需要连的网：(网名, [(位号, 引脚), ...])
# 电源/地/LOAD 不在这里，它们用网络标号（见 label()）—— 用端子走线会被自动吸附到
# 邻近线造成短路，这是踩过的坑。
NETS = [("LOAD", [("SW_LOAD", "COM")] + [(u, "LE") for u in LATCHES]),
        ("Q_M", [("U4", "Q4"), ("U1", "M")]),
        ("S_M", [("SW_M", "COM"), ("U4", "D4")]),
        ("CN_IN", [("SW_CN", "COM"), ("U1", "CN")])]
for i in range(4):
    NETS.append(("A%d" % i, [("SW_A%d" % i, "COM"), ("U2", "D%d" % i)]))
    NETS.append(("B%d" % i, [("SW_B%d" % i, "COM"), ("U3", "D%d" % i)]))
    NETS.append(("QA%d" % i, [("U2", "Q%d" % i), ("U1", "A%d" % i)]))
    NETS.append(("QB%d" % i, [("U3", "Q%d" % i), ("U1", "B%d" % i)]))
    NETS.append(("QS%d" % i, [("U4", "Q%d" % i), ("U1", "S%d" % i)]))
    NETS.append(("F%d" % i, [("U1", "F%d" % i), ("U5", "D%d" % i)]))
    NETS.append(("L%d" % i, [("U5", "Q%d" % i), ("LED%d" % i, "A")]))
LABELLED = {"LOAD", "B1"}   # B1 走标号 B1_N，避免与其他线抢通道


def build():
    design, sheet = ids()

    def place(name, x, y, ref):
        args = {"id": design, "sheet": sheet, "name": name, "x": x, "y": y, "reference": ref}
        if name.startswith("74LS"):
            # 关键：不传 value，器件 VALUE 为空，仿真时报模型加载失败
            args["value"] = name
        call("schematic_place_device", args, timeout=120)
        print("  place %-8s %-9s (%s,%s)" % (ref, name, x, y))

    def label(ref, pin, net):
        # connect=True：一个调用同时打网络标号并连到该引脚
        call("schematic_place_terminal", {
            "id": design, "sheet": sheet, "terminal_type": "DEFAULT", "label": net,
            "anchor_ref": ref, "anchor_pin": pin, "connect": True}, timeout=120)
        print("  label %s.%-5s = %s" % (ref, pin, net))

    print("[1/4] 打开事务")
    call("schematic_begin_transaction", {"id": design}, timeout=60)

    print("[2/4] 放置器件")
    for name, x, y, ref in ICS:
        place(name, x, y, ref)
    for ref, x, y in SW:
        place("SW-SPDT", x, y, ref)
    for ref, x, y in LED:
        place("LED-RED", x, y, ref)

    print("[3/4] 打电源/使能标号（先打标号，后布线）")
    for u in LATCHES:
        label(u, "$OE$", "GND")
    for sw, _, _ in SW:
        label(sw, "NO", "VCC")
        label(sw, "NC", "GND")
    for i in range(4):
        label("LED%d" % i, "K", "GND")
    label("SW_B1", "COM", "B1_N")
    label("U3", "D1", "B1_N")

    print("[4/4] 逐网布线（一次一个网，批量会部分失败）")
    bad = 0
    for name, pins in NETS:
        if name in LABELLED:
            continue
        res = call("schematic_route_nets", {
            "id": design, "sheet": sheet, "max_corners": 4,
            "nets": [{"name": name, "pins": [{"ref": r, "name": p} for r, p in pins]}]},
            timeout=240)
        summary = (res.get("data") or {}).get("summary") or {}
        ok = summary.get("routed") == 1
        bad += 0 if ok else 1
        print("  %s %-6s %s" % ("OK  " if ok else "BAD ", name, summary))

    call("schematic_end_transaction", {"id": design}, timeout=300)
    print("\n完成，%d 个网布线异常。接着跑 verify.py 核对网表。" % bad)
    return bad


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(1 if build() else 0)
    except McpError as exc:
        print("FAIL: %s" % exc)
        sys.exit(1)
