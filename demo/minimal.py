#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小可跑示例：从空白图纸建一个"两级锁存 + LED"电路，并当场自证接线正确。

电路
----
    SW0..3 ─→ U1(74LS373) ─→ U2(74LS373) ─→ LED0..3
                LE ← SW_LOAD      LE ← SW_LOAD
                OE → GND          OE → GND

为什么是这个电路：它短到能一眼看完，又完整覆盖了四件最容易出错的事——
放件带 value、电源用网络标号、逐网布线、以及布线后核对网表。

运行
----
    # Proteus 里新建一个空白工程（不用保存也行），然后：
    python demo/minimal.py

    预期最后一行：  PASS 13/13 个断言全部通过
    退出码 0 = 建图成功且网表核对通过；1 = 中途失败。

坐标单位 thou（100 thou = 1 网格），原点在图纸中心，所以有负坐标。
这套坐标故意留得比实际需要宽，就是为了别人换台机器也能布通。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from mcpc import McpError, call, ids  # noqa: E402
import verify  # noqa: E402

# ---- 布局 ----------------------------------------------------------------
SW_X, U1_X, U2_X = -4200, -1500, 1200
YS = [1600, 1100, 600, 100]          # 4 路输入的纵坐标
LOAD_Y = -2400                      # 拉远：太近会和 U1 的 $OE$/LE 标号抢同一块地方
# LED 竖排时每个要挂 A、K 两个标号，500 thou 的间距会让相邻标号重叠，
# 所以斜着错开：纵向仍差 500，横向每条多让 800。
LED_POS = [("LED0", 4200, 1600), ("LED1", 5000, 1100),
           ("LED2", 5800, 600), ("LED3", 6600, 100)]

# ---- 连线 ----------------------------------------------------------------
# 电源/地/LOAD 走网络标号（place_terminal），不走端子画线——端子会自动吸附
# 邻近导线，是短路的主要来源。
#
# 信号分两种接法，这也是真实工程里的惯常做法：
#   1) 距离近、通道够 → route_nets 实画线（IN 和 Q 这两段）
#   2) 跨得远、通道会被抢 → 两端各打一个同名网络标号，电气上就是同一个网
NETS = []
for i in range(4):
    NETS.append(("IN%d" % i, [("SW%d" % i, "COM"), ("U1", "D%d" % i)]))
    NETS.append(("Q%d" % i, [("U1", "Q%d" % i), ("U2", "D%d" % i)]))

# U2 → LED 这一段跨了 3000 thou，直接拉线会跟前面几条抢通道（实测 OUT1-3 routed:0），
# 改用同名标号连接。
LINK_LABELS = []
for i in range(4):
    LINK_LABELS.append(("U2", "Q%d" % i, "OUT%d" % i))
    LINK_LABELS.append(("LED%d" % i, "A", "OUT%d" % i))

# 建完要验证的断言：左边两脚必须落在同一张网上
EXPECT = []
for i in range(4):
    EXPECT.append("SW%d.COM=U1.D%d" % (i, i))
    EXPECT.append("U1.Q%d=U2.D%d" % (i, i))
    EXPECT.append("U2.Q%d=LED%d.A" % (i, i))
EXPECT.append("U1.LE=U2.LE")


def build():
    design, sheet = ids()

    def place(name, x, y, ref):
        args = {"id": design, "sheet": sheet, "name": name,
                "x": x, "y": y, "reference": ref}
        if name.startswith("74LS"):
            args["value"] = name       # 不带 value 时仿真会报模型加载失败
        call("schematic_place_device", args, timeout=120)
        print("  place %-7s %-9s (%5d,%5d)" % (ref, name, x, y))

    def label(ref, pin, net):
        call("schematic_place_terminal", {
            "id": design, "sheet": sheet, "terminal_type": "DEFAULT",
            "label": net, "anchor_ref": ref, "anchor_pin": pin,
            "connect": True}, timeout=120)
        print("  label %-7s .%-4s = %s" % (ref, pin, net))

    print("[1/4] 开事务（整批一个 undo 步）")
    call("schematic_begin_transaction", {"id": design}, timeout=60)

    print("[2/4] 放件")
    place("74LS373", U1_X, 850, "U1")
    place("74LS373", U2_X, 850, "U2")
    for i, y in enumerate(YS):
        place("SW-SPDT", SW_X, y, "SW%d" % i)
    for ref, x, y in LED_POS:
        place("LED-RED", x, y, ref)
    place("SW-SPDT", U1_X, LOAD_Y, "SW_LOAD")

    print("[3/4] 打标号：电源、地、LOAD、跨远的 OUT（先打标号再布线，否则标号抢不到通道）")
    for u in ("U1", "U2"):
        label(u, "$OE$", "GND")        # 输出常使能
        label(u, "LE", "LOAD")         # 两片 373 共用一路锁存使能
    label("SW_LOAD", "COM", "LOAD")
    for ref, pin, net in LINK_LABELS:
        label(ref, pin, net)
    for i in range(4):
        label("SW%d" % i, "NO", "VCC")  # 拨到 1 = 高电平
        label("SW%d" % i, "NC", "GND")  # 拨到 0 = 地
        label("LED%d" % i, "K", "GND")  # LED 阴极接地

    print("[4/4] 逐网布线（一次只传一个 net，批量会部分失败）")
    bad = 0
    for name, pins in NETS:
        res = call("schematic_route_nets", {
            "id": design, "sheet": sheet, "max_corners": 4,
            "nets": [{"name": name,
                      "pins": [{"ref": r, "name": p} for r, p in pins]}]},
            timeout=240)
        summary = (res.get("data") or {}).get("summary") or {}
        ok = summary.get("routed") == 1
        bad += 0 if ok else 1
        print("  %s %-6s %s" % ("OK  " if ok else "BAD ", name, summary))

    call("schematic_end_transaction", {"id": design}, timeout=300)
    if bad:
        print("\n%d 个网没布通，见上面的 BAD 行" % bad)

    # ---- 自证：读回网表逐条断言，不靠图看着像 --------------------------
    print("\n[自检] 读网表核对 %d 条断言" % len(EXPECT))
    data = call("schematic_get_netlist", {"id": design, "sheet": sheet}, timeout=180)
    nets = verify.flatten(data)
    if not nets:
        # 全新工程第一次提交后网表数据库可能还没编译，保存一次再读
        print("  网表为空，保存工程后重读…")
        try:
            call("file_save", {}, timeout=180)
        except McpError as exc:
            print("  file_save 失败：%s" % exc)
        data = call("schematic_get_netlist", {"id": design, "sheet": sheet}, timeout=180)
        nets = verify.flatten(data)
    pin2net = {}
    for net in nets:
        for pin in net["pins"]:
            pin2net[pin] = net["name"]

    passed = 0
    for expr in EXPECT:
        a, _, b = expr.partition("=")
        ra, _, pa = a.partition(".")
        rb, _, pb = b.partition(".")
        na, nb = pin2net.get((ra, pa)), pin2net.get((rb, pb))
        ok = na is not None and na == nb
        passed += 1 if ok else 0
        print("  %s %-22s (%s / %s)" % ("OK  " if ok else "FAIL", expr, na, nb))

    dangling = [p for n in nets if n["group"] == "notconnected" for p in n["pins"]]
    print("\n  网表 %d 个网 / %d 个引脚有归属 / %d 个悬空"
          % (len(nets), len(pin2net), len(dangling)))
    if passed == len(EXPECT) and not dangling:
        print("\nPASS %d/%d 个断言全部通过" % (passed, len(EXPECT)))
        return 0
    print("\nFAIL %d/%d 通过，%d 个悬空" % (passed, len(EXPECT), len(dangling)))
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(build())
    except McpError as exc:
        print("FAIL: %s" % exc)
        sys.exit(1)
