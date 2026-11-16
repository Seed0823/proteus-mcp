#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「清单」和「画出来的网表」当两张图比：该连的连上了没，不该连的有没有粘一起。

清单（spec JSON）是目标，网表是实际结果。两者都表示成「哪些引脚属于同一组」，
然后逐组比对。比字符串相等宽松，比看图严格。

用法
----
    python netlist_diff.py --spec spec/alu4bit.json                  # 连当前图纸
    python netlist_diff.py --spec spec/x.json --json netlist.json    # 用离线网表
    python netlist_diff.py --spec spec/x.json --json n.json --verbose

退出码：0 = 完全一致；1 = 有分家、短接或引脚缺失。
"""

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpc import McpError, call, ids  # noqa: E402
import verify  # noqa: E402

POWER = {"VCC", "VDD", "GND", "VSS"}


def load_actual(args):
    if args.json:
        with open(args.json, encoding="utf-8") as f:
            return json.load(f)
    design, sheet = ids()
    print("读取网表 design_id=%s sheet_id=%s ..." % (design, sheet))
    return call("schematic_get_netlist", {"id": design, "sheet": sheet}, timeout=180)


def pin_of(entry):
    if isinstance(entry, (list, tuple)):
        return (entry[0], entry[1])
    ref, _, pin = str(entry).partition(".")
    return (ref, pin)


def spec_groups(spec):
    """清单 → {引脚: 组名}。电源网按名字归并，不按条数。

    元件自带的固定电平脚（`"pins": {"NO": "VCC"}`）也算交代过，一起收进来。
    """
    out = {}
    for net in spec.get("nets") or []:
        name = net["name"]
        group = name.upper() if name.upper() in POWER else "网:" + name
        for entry in net.get("pins") or []:
            out[pin_of(entry)] = group
    for comp in spec.get("components") or []:
        for pin, net_name in (comp.get("pins") or {}).items():
            group = net_name.upper() if net_name.upper() in POWER else "网:" + net_name
            out.setdefault((comp["ref"], pin), group)
    return out


def actual_maps(data):
    pin2net, netpins = {}, {}
    for net in verify.flatten(data):
        for pin in net["pins"]:
            pin2net[pin] = net["name"]
            netpins.setdefault(net["name"], []).append(pin)
    return pin2net, netpins


def main(argv=None):
    ap = argparse.ArgumentParser(description="比对清单与网表")
    ap.add_argument("--spec", required=True, help="清单 JSON")
    ap.add_argument("--json", help="离线网表 JSON；不给就连 Proteus")
    ap.add_argument("--verbose", action="store_true", help="逐网打印")
    ap.add_argument("--strict", action="store_true",
                    help="清单没交代的引脚也算失败（默认只警告）")
    args = ap.parse_args(argv)

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    try:
        data = load_actual(args)
    except McpError as exc:
        print("FAIL: 读不到网表（%s）" % exc)
        return 1

    want = spec_groups(spec)
    pin2net, netpins = actual_maps(data)
    problems = []

    print("清单：%d 个引脚、%d 个网；网表：%d 个引脚、%d 个网"
          % (len(want), len({v for v in want.values()}), len(pin2net), len(netpins)))

    missing = sorted(p for p in want if p not in pin2net)
    if missing:
        for ref, pin in missing:
            print("  缺引脚  %s.%s —— 图上根本没有这个引脚" % (ref, pin))
        problems.append("%d 个引脚缺失" % len(missing))

    group2actual = defaultdict(set)
    for pin, group in want.items():
        if pin in pin2net:
            group2actual[group].add(pin2net[pin])

    split = {g: sorted(v) for g, v in group2actual.items() if len(v) > 1}
    for group, names in split.items():
        print("  分家    %s 在网表里碎成 %d 段：%s" % (group, len(names), names))
        if args.verbose:
            for name in names:
                print("            %-12s %s" % (name, " ".join(
                    "%s.%s" % p for p in netpins.get(name, []))))
    if split:
        problems.append("%d 个网被拆开" % len(split))

    actual2group = defaultdict(set)
    for pin, group in want.items():
        if pin in pin2net:
            actual2group[pin2net[pin]].add(group)

    merged = {a: sorted(g) for a, g in actual2group.items() if len(g) > 1}
    for net, groups in merged.items():
        print("  短接    %s 同时连了 %s" % (net, groups))
        if args.verbose:
            print("            %s" % " ".join("%s.%s" % p for p in netpins.get(net, [])))
    if merged:
        problems.append("%d 处误连" % len(merged))

    ok_nets = len(group2actual) - len(split)
    print("\n一致 %d / %d 个网" % (ok_nets, len(group2actual)))

    # 清单漏写的连接，是这套比对最容易放过的一类错：清单没提，图也不连，
    # 两边自然"一致"。靠这一条兜底——图上接了、清单没交代的引脚，逐个点名。
    spec_refs = {c["ref"] for c in spec.get("components") or []}
    skip_pins = {"VCC", "VDD", "GND", "VSS", "$OE$"}
    extra = sorted(p for p in pin2net
                   if p not in want and p[0] in spec_refs and p[1] not in skip_pins)
    if extra:
        print("\n图上有归属、清单里没交代的引脚 %d 个：" % len(extra))
        for ref, pin in extra[:20]:
            print("  ?  %s.%s -> %s" % (ref, pin, pin2net[(ref, pin)]))
        if len(extra) > 20:
            print("  ... 还有 %d 个" % (len(extra) - 20))
        if args.strict:
            problems.append("%d 个引脚清单里没交代" % len(extra))
        else:
            print("（不确定是不是漏写，加 --strict 让这类问题算失败）")

    if problems:
        print("结论：不一致 -> %s" % "；".join(problems))
        return 1
    print("结论：网表与清单完全一致。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
