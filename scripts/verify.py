#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对 Proteus 网表：谁连了谁、哪里断了、电源对不对。

画完图不验网表等于没画完 —— 图上看着连着，网表里可能是两段。

用法
----
    python verify.py                               # 拉当前图纸网表，报悬空网 + 电源
    python verify.py --refs U1,U2,U3               # 额外打印这些器件每个引脚的归属
    python verify.py --expect U1.A0=U2.Q0,U1.B0=U3.Q0
    python verify.py --json netlist.json           # 用离线网表（不连 Proteus）
    python verify.py --all                         # 打印全部引脚

退出码：0 = 无悬空网且断言全过；1 = 有问题。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpc import McpError, call, ids  # noqa: E402

POWER_PINS = {"VCC", "VDD", "GND", "VSS", "$OE$"}


def load(args):
    if args.json:
        with open(args.json, encoding="utf-8") as f:
            return json.load(f)
    design, sheet = ids()
    print("读取网表 design_id=%s sheet_id=%s ...\n" % (design, sheet))
    return call("schematic_get_netlist", {"id": design, "sheet": sheet}, timeout=180)


def flatten(data):
    nets = data.get("nets") or {}
    out = []
    for group in ("named", "unnamed", "notconnected"):
        for i, net in enumerate(nets.get(group) or []):
            pins, labels = [], []
            for c in net.get("connections") or []:
                ref = c.get("ref")
                if ref:
                    pins.append((ref, c.get("name")))
                elif c.get("label"):
                    labels.append(c["label"])
            out.append({
                "group": group,
                "name": net.get("name") or "%s#%d" % (group, i),
                "pins": pins,
                "labels": labels,
            })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="核对 Proteus 网表")
    ap.add_argument("--json", help="离线网表 JSON 路径")
    ap.add_argument("--refs", help="要展开的位号，逗号分隔，如 U1,U2")
    ap.add_argument("--expect", help="断言两引脚同网，逗号分隔，如 U1.A0=U2.Q0")
    ap.add_argument("--all", action="store_true", help="打印全部引脚")
    args = ap.parse_args(argv)

    try:
        data = load(args)
    except McpError as exc:
        print("FAIL: %s" % exc)
        return 1

    nets = flatten(data)
    pin2net, netpins = {}, {}
    for net in nets:
        for pin in net["pins"]:
            pin2net[pin] = net["name"]
            netpins.setdefault(net["name"], []).append(pin)

    problems = []

    # 1. 悬空：notconnected 组里的引脚，或只有一个引脚又没有标号的网
    dangling = []
    for n in nets:
        if n["group"] == "notconnected":
            dangling.extend(n["pins"])
        elif len(n["pins"]) <= 1 and not n["labels"]:
            dangling.extend(n["pins"] or [("<空网>", "-")])
    print("网表：%d 个网，%d 个引脚有归属" % (len(nets), len(pin2net)))
    if dangling:
        print("\n疑似悬空（没接上任何东西）：")
        for ref, pin in dangling:
            print("  - %s.%s" % (ref, pin))
        problems.append("%d 个引脚悬空" % len(dangling))

    # 2. 电源引脚是否落在同名线上
    pwr = {}
    for net in nets:
        for ref, pin in net["pins"]:
            if pin in POWER_PINS:
                pwr.setdefault(net["name"], []).append("%s.%s" % (ref, pin))
    print("\n电源/使能网：")
    for name, members in sorted(pwr.items()):
        print("  %-12s %d 个引脚" % (name, len(members)))

    # 3. 指定位号的引脚归属
    if args.refs or args.all:
        want = [r.strip() for r in (args.refs or "").split(",") if r.strip()]
        refs = sorted({ref for ref, _ in pin2net})
        if want:
            refs = [r for r in refs if r in want]
        print("\n引脚归属：")
        for ref in refs:
            pins = sorted({p for (r, p) in pin2net if r == ref})
            print("  [%s]" % ref)
            for pin in pins:
                print("    %-6s -> %s" % (pin, pin2net[(ref, pin)]))

    # 4. 断言
    if args.expect:
        print("\n断言：")
        for expr in args.expect.split(","):
            expr = expr.strip()
            if not expr:
                continue
            left, _, right = expr.partition("=")
            def lookup(s):
                ref, _, pin = s.strip().partition(".")
                return (ref, pin)
            a, b = lookup(left), lookup(right)
            na, nb = pin2net.get(a), pin2net.get(b)
            ok = na is not None and na == nb
            print("  %s %s <-> %s  (%s / %s)" % ("OK  " if ok else "FAIL", left.strip(),
                                                 right.strip(), na, nb))
            if not ok:
                problems.append(expr)

    print()
    if problems:
        print("结论：有问题 -> %s" % "; ".join(problems))
        return 1
    print("结论：网表看起来是完整的（无悬空网，电源已接）。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
