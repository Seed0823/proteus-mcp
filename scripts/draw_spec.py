#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""照一份电路清单（spec JSON）在 Proteus 里摆件、连线。

只管电气对不对，不管排版好不好看：元件按列自动排，走线交给 route_nets，
布不通就退化成两端同名网络标号（电气等价）。

用法
----
    python draw_spec.py --spec spec/demo.json --dry-run     # 只打印计划
    python draw_spec.py --spec spec/demo.json               # 真画（当前图纸要空白）
    python draw_spec.py --spec spec/demo.json --check       # 画完顺手跑 netlist_diff

清单格式见 spec/README.md，两个关键字段：
    components[].col     摆在第几列（0 是最左）
    components[].pins    固定电平脚，如 {"NO": "VCC"}，会直接打标号
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpc import McpError, call, ids  # noqa: E402
import netlist_diff  # noqa: E402

POWER = {"VCC", "VDD", "GND", "VSS"}
BASE_X, STEP_X = -4200, 2700      # 列的横坐标
BASE_Y, STEP_Y = 1600, 500        # 行距；同列器件从上往下排


def layout(spec):
    """把清单里的元件算成坐标；清单自己给了 x/y 就用清单的，给了 row 就占那一行。"""
    used = {}          # 列 -> 已占的行号
    placed = []
    for comp in spec.get("components") or []:
        if "x" in comp and "y" in comp:
            placed.append((comp, comp["x"], comp["y"]))
            continue
        col = int(comp.get("col", 0))
        taken = used.setdefault(col, set())
        if "row" in comp:
            row = int(comp["row"])
        else:
            row = 0
            while row in taken:
                row += 1
        taken.add(row)
        x = BASE_X + col * STEP_X
        y = BASE_Y - row * STEP_Y
        placed.append((comp, x, y))
    return placed


def net_pins(net):
    out = []
    for entry in net.get("pins") or []:
        if isinstance(entry, (list, tuple)):
            out.append((entry[0], entry[1]))
        else:
            ref, _, pin = str(entry).partition(".")
            out.append((ref, pin))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="照清单在 Proteus 里画电路")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连 Proteus")
    ap.add_argument("--check", action="store_true", help="画完跑 netlist_diff")
    args = ap.parse_args(argv)

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    placed = layout(spec)
    comps = spec.get("components") or []
    nets = spec.get("nets") or []

    print("清单：%d 个元件、%d 个网" % (len(comps), len(nets)))
    if args.dry_run:
        for comp, x, y in placed:
            print("  %-8s %-10s (%5d, %5d)" % (comp["ref"], comp["part"], x, y))
        for net in nets:
            style = "标号" if net["name"].upper() in POWER or net.get("style") == "label" else "走线"
            print("  %-10s %-4s %s" % (net["name"], style,
                                       " ".join("%s.%s" % p for p in net_pins(net))))
        return 0

    try:
        design, sheet = ids()
    except McpError as exc:
        print("FAIL: 连不上 Proteus（%s）" % exc)
        return 1
    print("design_id=%s sheet_id=%s" % (design, sheet))

    stats = {"placed": 0, "labelled": 0, "wired": 0, "fellback": 0, "failed": 0}
    call("schematic_begin_transaction", {"id": design}, timeout=60)

    for comp, x, y in placed:
        kwargs = {"id": design, "sheet": sheet, "name": comp["part"],
                  "x": x, "y": y, "reference": comp["ref"]}
        if comp["part"].startswith("74LS"):
            kwargs["value"] = comp.get("value") or comp["part"]
        try:
            call("schematic_place_device", kwargs, timeout=120)
            stats["placed"] += 1
            print("  放件 %-8s %-10s (%d,%d)" % (comp["ref"], comp["part"], x, y))
        except McpError as exc:
            stats["failed"] += 1
            print("  放件失败 %s %s: %s" % (comp["ref"], comp["part"], exc))

    def label(ref, pin, text):
        call("schematic_place_terminal",
             {"id": design, "sheet": sheet, "terminal_type": "DEFAULT",
              "label": text, "anchor_ref": ref, "anchor_pin": pin, "connect": True},
             timeout=120)
        stats["labelled"] += 1

    for comp in comps:
        for pin, text in (comp.get("pins") or {}).items():
            try:
                label(comp["ref"], pin, text)
            except McpError as exc:
                stats["failed"] += 1
                print("  标号失败 %s.%s: %s" % (comp["ref"], pin, exc))

    for net in nets:
        pins = net_pins(net)
        if net["name"].upper() in POWER or net.get("style") == "label":
            for ref, pin in pins:
                try:
                    label(ref, pin, net["name"])
                except McpError as exc:
                    stats["failed"] += 1
                    print("  标号失败 %s.%s: %s" % (ref, pin, exc))
            continue

        body = {"id": design, "sheet": sheet, "max_corners": 4,
                "nets": [{"name": net["name"],
                          "pins": [{"ref": r, "name": p} for r, p in pins]}]}
        summary = {}
        try:
            res = call("schematic_route_nets", body, timeout=240)
            # call() 已经解出 dict，别再 json.loads 一遍——之前就是这里踩的坑，
            # 解析抛异常被当成"布不通"，于是每条网都白打了一对标号。
            if isinstance(res, dict):
                summary = (res.get("data") or {}).get("summary") or {}
        except McpError as exc:
            print("  走线 %-10s 报错：%s" % (net["name"], str(exc)[:120]))
        if summary.get("routed") == 1:
            stats["wired"] += 1
            print("  走线 %-10s OK" % net["name"])
            continue

        print("  走线 %-10s 布不通，改用两端标号" % net["name"])
        stats["fellback"] += 1
        for ref, pin in pins:
            try:
                label(ref, pin, net["name"])
            except McpError as exc:
                stats["failed"] += 1
                print("  标号失败 %s.%s: %s" % (ref, pin, exc))

    res = call("schematic_end_transaction", {"id": design}, timeout=300)
    depth = (res.get("data") or {}).get("depth") if isinstance(res, dict) else None
    print("提交完成（剩余未提交层数 depth=%s）" % depth)
    print("放件 %d，走线 %d，标号 %d，走线降级 %d，失败 %d"
          % (stats["placed"], stats["wired"], stats["labelled"],
             stats["fellback"], stats["failed"]))

    if args.check:
        # 刚提交时网表还在重编译，读早了会报 "not recompiled until you commit"
        for attempt in range(5):
            try:
                call("schematic_get_netlist", {"id": design, "sheet": sheet}, timeout=120)
                break
            except McpError:
                print("网表还没编译好，等 2 秒（第 %d 次）" % (attempt + 1))
                time.sleep(2)
        print("\n开始核对：")
        return netlist_diff.main(["--spec", args.spec, "--verbose"])
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
