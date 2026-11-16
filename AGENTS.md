# AGENTS.md — 给 AI agent 的 ProPilot 操作规程

你要操作 Proteus（ISIS 原理图 + VSM 仿真）。**不要用鼠标键盘自动化**，走 ProPilot 自带的 MCP 端点。
本仓库只是客户端 + 验收层：不装服务器、不接管进程、不碰 GUI。

## 0. 一句话结论

Proteus 的 ProPilot 在 `http://127.0.0.1:8001/` 提供 MCP（JSON-RPC over HTTP），
用 `scripts/mcpc.py` 调用即可；画完必须用 `scripts/verify.py` 读网表自证。

## 1. 探活（每次开工先做）

```bash
python scripts/mcpc.py probe      # 找到活着的端点
python scripts/mcpc.py status     # 取 design_id / sheet_id
```

`probe` 全失败 → 让用户启动 Proteus 并打开工程、确认 ProPilot 面板启用过。
不要自己猜端口，也不要开始写 GUI 自动化脚本。

绝不要硬编码 `design_id` / `sheet_id`，用 `mcpc.ids()` 每次现取。

## 2. 一次构建的固定套路

```
probe → status(取 id) → begin_transaction → place_device ×N
      → place_terminal(打标号) ×N → route_nets(逐网) ×N
      → end_transaction → get_netlist/verify → runctrl 验证功能
```

放器件的调用模板（照抄，别省字段）：

```python
call("schematic_place_device", {
    "id": design, "sheet": sheet,
    "name": "74LS373",          # 必须是 list_parts_bin / library_search 里的精确名字
    "x": -4200, "y": 2900,      # thou；原点在图纸中心，允许负值
    "reference": "U2",          # 位号在放件时定死，后面引用的都是它
    "value": "74LS373",         # 74LS/74HC 系列必须给，否则仿真报模型加载失败
})
```

给引脚打标号（一句搞定"标号 + 连线"）：

```python
call("schematic_place_terminal", {
    "id": design, "sheet": sheet, "terminal_type": "DEFAULT", "label": "GND",
    "anchor_ref": "U2", "anchor_pin": "$OE$", "connect": True,
})
```

布线（**一个网一次调用**）：

```python
for name, pins in nets:
    call("schematic_route_nets", {
        "id": design, "sheet": sheet, "max_corners": 4,
        "nets": [{"name": name, "pins": [{"ref": r, "name": p} for r, p in pins]}],
    })
```

多引脚电源轨（推荐写法，元件引脚自动连到最近的电源端子）：

```python
call("schematic_route_nets", {
    "id": design, "sheet": sheet,
    "nets": [{"name": "GND", "power": True,
              "terminals": [{"ref": "GROUND", "name": "GND"}],
              "pins": [{"ref": "U1", "name": "GND"}, {"ref": "U2", "name": "GND"}]}],
})
```

## 3. 硬规则

**必须**

1. 整批操作包在 `schematic_begin_transaction` / `schematic_end_transaction` 之间，
   这样失败可以整批 undo。
2. **先打标号，再布其余线。** 顺序反了标号抢不到通道。
3. `route_nets` **一次只有一个 net**。
4. 提交后必须 `schematic_get_netlist`（或用 `verify.py`）核对连通性，再谈"做完了"。
5. 判断调用成败看 `result.content[0].text` 是否以 `Error` 开头（没有 `isError` 字段）。
6. 器件名字用 `schematic_list_parts_bin` 或 `library_search` 查到的精确名。

**禁止**

1. 禁止用 pywin32 / pyautogui 点 Proteus 界面（DPI、输入法、模态框三重坑）。
2. 禁止把多个网塞进一次 `route_nets`。
3. 禁止用电源端子走线来接 GND/VCC（端子自动吸附邻近线 → 短路）。
4. 禁止假设 `schematic_get_image` 能出 PNG —— 它只回 SVG，`format` 参数实测被拒。
5. 禁止在没读网表的情况下宣称电路正确。

## 4. 坐标与布局

- 单位 **thou**：100 thou = 0.1 inch = 1 个默认网格（`schematic_set_grid` 可改）。
- 原点在**图纸中心**，所以坐标常为负；A4 图幅约 ±5800 × ±4100 thou 可用。
- 初学布局按列排：输入开关最左（x ≈ -6200），锁存器中间（x ≈ -4200），
  核心器件（x ≈ -600），输出显示最右（x ≈ 3200+）。
- 同类器件**等间距**（500 thou 的整数倍），给布线留通道，否则会 over_corners。
- **通道宽度**：N 根线要穿过两个器件之间同一个缺口，缺口至少要有 (N+1) 个网格宽。
  布不通时先加大间距；实在挤不开再试 `bus_grid: 50` / `25`，别硬塞。
- `route_nets` 返回 `summary.over_corners > 0` 或出现 `diagonal: true` 是"布局有问题"的
  信号，挪开器件重布；报告里的长度数字是 base unit，不是 thou，别拿它算距离。

## 5. 验证闭环

```bash
python scripts/verify.py --refs U1,U2 --expect "U1.A0=U2.Q0,U1.F0=U5.D0"
```

1. 网表里每个信号网至少 2 个引脚；`notconnected` 里出现的就是悬空。
2. 电源引脚（`VCC`/`VDD`/`GND`/`VSS`）要落在同名网里，`$OE$` 该接 GND。
3. `interactive_runctrl` `{"mode":"run","time":1}` → `interactive_get_log`
   看有没有 `model` / `no model` 错误；再 `{"mode":"stop"}`。
4. 要彩色图给用户看：MCP 只有 SVG，位图请系统截屏（Windows 用 PIL `ImageGrab`）。

## 6. 报错对照

| 报错 | 含义 | 处理 |
|---|---|---|
| `cannot reach ... 10061` | 端口没监听 | Proteus 没开，或 ProPilot 没启用 |
| `502 Bad Gateway` | 请求被系统代理劫持 | 客户端禁用代理（`ProxyHandler({})`） |
| `Error: Unknown parameter: format` | 该版本不认这个参数 | 去掉参数，按最小参数集调用 |
| `Error: Unknown device` | 器件名不对 | `library_search` 查精确名，注意 `SW-SPDT`、`LED-RED` 这类带横杠的 |
| `routed: 0` | 该网布不通 | 挪器件拉大间距，或先打标号再布 |
| 模型加载失败 | 器件 VALUE 为空 | 重新放件并带 `value` |
| `get_netlist` 返回 0 个网 | 返回体比离线导出多包一层 `data` | 读 `resp["data"]["nets"]` |
| `file_save` 超时无返回 | 8.x 工程存盘要确认转 9.2 格式，弹了模态框 | 用 `ui_answer_dialog` 回答（key 用 `Yes`/`Ok`），对话框要满 4 秒才能关 |
| `not recompiled until you commit` | 刚 `end_transaction`，网表还在重编译 | 等 1~2 秒重试 `get_netlist`，不要重复提交 |
| 每条网都 `routed: 0` 但图上确实有连线 | `mcpc.call()` 已返回 dict，被外层又 `json.loads` 了一次 | 先判断 `isinstance(res, dict)`，再取 `res["data"]["summary"]` |
| `reads the compiled netlist` | 事务未提交 | 先 `end_transaction`；中途只想看用 `get_image` |
| `nested transaction` / `abandon is only valid for the outermost` | `begin` 多于 `end` | 普通 `end` 逐层关掉，最后才能 `abandon` |
| `would overlap the existing terminal(s)` | 两个网络标号抢同一位置 | 拉大器件间距或斜向错开 |

## 7. 交付前自检

- [ ] `verify.py` 退出码 0
- [ ] 仿真 run/stop 无报错
- [ ] 关键断言（如 `U1.A0=U2.Q0`）全 OK
- [ ] 有给用户看的图（SVG 或截屏）
