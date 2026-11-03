# nolook

让编程 agent 通过控件树而不是截图来操作 Android 应用。

[English](README.md)

agent 在设备上调试应用时，通常是这样一个循环：截图、读图、估出按钮坐标、点击、再截图。每张截图都会留在对话里，并随之后的每一次请求重新发送，所以步骤越多，会话越慢、越贵；估出来的坐标也可能点错地方。

nolook 把屏幕内容变成一份简短的文本列表交给 agent。它按元素的文字或 id 点击，每次操作后报告界面发生了哪些变化，可以一次调用执行多个步骤，只有在需要判断视觉效果时才截取一小块图片。

```
$ nolook snap
com.example.notes · 1080x2400 · 9 elements
@e1  btn    "返回" (63,140)
@e2  text   "笔记" (300,140)
@e3  scroll #note_list (540,1250)
  @e4  btn    "购物清单 · 牛奶、鸡蛋、面包" (540,420)
  @e5  btn    "出行 · 车票已订" (540,610)
@e6  btn    "新建笔记" #fab_new [covered] (960,2180)
@e7  btn    "搜索" (1000,140)

$ nolook tap 购物清单
tap @e4 btn "购物清单 · 牛奶、鸡蛋、面包" (540,420) · settled 0.42s
screen changed (6 elements):
@e8  btn    "返回" (63,140)
@e9  input  "购物清单" #title (540,300)
@e10 input  "牛奶、鸡蛋、面包" #body (540,700)
…
```

## 功能

- **以文本读取屏幕。** `snap` 列出可交互的元素和文字，每个元素一行，附带一个简短的引用（`@e4`）。列表中的一行会合并为一行显示。被浮动栏遮住的元素标记为 `[covered]`。
- **按查询操作，并防止误操作。** `tap 购物清单`、`tap #fab_new`、`tap @e4`。查询必须恰好匹配一个元素；已经离开屏幕的引用会报错，而不会点到原位置上的其他元素。被遮住的元素，以及看起来是写操作的按钮（点赞、关注、发送、删除等），除非明确要求，否则拒绝点击。
- **报告变化。** 每次操作后等待界面稳定，打印新出现和消失的元素，agent 通常无需再次查看屏幕。
- **执行流程（flow）。** 把多个步骤写成一个简短的 Python 文件，可以包含条件和循环，一次调用执行完毕。常用的流程保存在项目中，可按名称调用。
- **按需截取小图。** 只截取某个元素所在的区域，保存为小尺寸 JPEG 文件，nolook 只打印文件路径和预估的 token 数，agent 确有需要时才读取。
- **测量动画。** 录制操作前后的屏幕，报告动画时长、达到 50% 和 90% 的时间、进度曲线的形状、过冲和卡顿，并附一张关键帧拼图。
- **测量性能。** 反复滑动时的帧耗时（gfxinfo）、切换编译模式、把 Perfetto trace 汇总为一页文字、查看 ANR 堆栈。
- **向你展示设备画面**（Claude Code）：对话旁的面板会在每条命令后显示设备屏幕。画面只发给你的终端，模型不会读到。

## 安装

要求：Python 3.11 及以上，`adb` 在 `PATH` 中，设备或模拟器已开启 USB 调试。`motion` 需要 `ffmpeg`。首次连接时，nolook 会把 uiautomator2 的测试服务推送到设备上，不会改动你的应用。

```sh
uv tool install nolook          # 或：pipx install nolook
nolook doctor                   # 检查 adb、设备、ffmpeg 和 Perfetto
```

### Claude Code

```
/plugin marketplace add HuanCheng65/nolook
/plugin install nolook@nolook
```

插件包含三部分：`nolook` skill（说明如何用好这个 CLI）、`nolook-driver` agent（执行多步设备操作，只以文字汇报结果），以及 `/device` 面板。

### 其他 agent

任何能执行 shell 命令的 agent 都可以使用这个 CLI。把 [`plugin/skills/nolook/SKILL.md`](plugin/skills/nolook/SKILL.md) 作为说明交给它即可。

## 用法

```sh
nolook snap                         # 查看屏幕内容
nolook tap "网络和互联网"            # 按文字点击，并打印变化
nolook type "你好" --into "#search"
nolook scroll down --until "关于手机"
nolook wait "已保存" --timeout 5
nolook back
nolook shot "#toolbar"              # 截取一小块，打印路径
nolook run flows/open_note.py       # 一次执行多个步骤
```

### 查询语法

| 写法 | 匹配 |
|---|---|
| `@e12` | 上一次输出中的引用 |
| `#title` | resource id 或 Compose 的 test tag |
| `保存` | 文字、内容描述，或列表行标签中的一段，完全一致 |
| `~保` | 标签包含 |
| `/^第\d+项$/` | 标签匹配正则表达式 |
| `btn:保存` | 以上任一写法，并限定角色 |

没有匹配时，nolook 列出最接近的元素；匹配到多个时，列出它们，并提示改用引用、角色或 `--nth`。

### 流程

```python
"""打开一条笔记并检查编辑器。"""
d.launch(fresh=True)
d.tap("购物清单")
if d.exists("放弃草稿？"):
    d.tap("保留")
d.check(d.exists("#body"), "编辑器已打开")
d.shot("#toolbar")
```

`nolook run open_note` 每一步打印一行。失败时打印失败的步骤和行号、当时的屏幕内容，以及一张已保存但未读取的截图的路径。

flow 还可以直接测量（`d.frames()`、`with d.measure("x"):`、`d.motion(…)`），自动关闭意外弹窗（`d.dismiss_when("给我们评分", tap="以后再说")`），根据某一步操作的结果分支（`if d.tap("保存").shows("已保存"):`），并以 JSON 返回读取到的值（`d.result(count=3)`）。

### 动画

```sh
nolook motion --tap "#fab_new" --label open
nolook motion --key back --label close --slow 4
nolook motion-compare
```

```
motion [open] · #fab_new · 61 frames over 1.2s
runs 335ms to within 1% of its end · 50% at 54ms · 90% at 137ms · in [486, 331, 1080, 1168]
progress ▁▃▅▆▆▇██████████  decelerating (ease-out) · by position (p at 25/50/75% of time: 0.73/0.99/0.99)
monotonic: yes
14 frames in motion, median 16.4ms apart · no stalls
sheet (not read): ~/.cache/nolook/…/shots/012554-motion.jpg
```

测量只在发生变化的区域内进行，并把每一帧与首帧、末帧比较，因此弹簧动画缓慢的收尾也会计入，小面积的菜单也不会被大片静止的画面稀释。录屏帧率较低的设备可以用 `--slow N` 放慢动画后再测。

### 性能

```sh
nolook perf frames --label before        # 重置 gfxinfo → 反复滑动 → 汇总
nolook perf frames --label after --trace traces/
nolook perf compare
nolook trace summary traces/nolook-….pftrace
nolook perf compile speed                # 切换 ART 编译模式，之后重新启动应用
nolook perf anr
```

`trace summary` 使用 Perfetto 的 trace processor 读取 trace（首次使用时自动下载），输出卡顿类型及其含义、帧耗时分位数、最严重的几帧中主线程执行的内容，以及主线程和渲染线程的时间分布。

## 配置

在项目根目录（或任一上级目录）放置 `.nolook.toml`：

```toml
serial = "emulator-5554"
adb_port = 5037                 # 使用非默认 adb server 时设置
package = "com.example.notes"
flows = "tools/flows"
guard = ["/api/like", "/api/post"]   # 每次操作后在 logcat 中检查的正则
danger = ["点赞", "发送", "删除"]      # 不带 --confirm 时拒绝点击的标签
```

环境变量 `NOLOOK_SERIAL` 和 `NOLOOK_ADB_PORT` 优先于配置文件。

对于 Jetpack Compose，请在根部设置 `Modifier.semantics { testTagsAsResourceId = true }`（仅 debug 构建即可），使 `testTag` 显示为 `#id`；并为图标按钮设置 `contentDescription`。

## 局限

- 无障碍树中没有的内容，nolook 也看不到：WebView 的部分内容、canvas、游戏、视频。这些场景请使用 `tap --xy` 和截图。
- Flutter 应用暴露给无障碍树的信息很少。
- `motion` 只判断像素，不判断意图：它能说明动画持续多久、曲线是什么形状，不能判断手感是否合适。请与可信版本的带标签记录对比。
- 模拟器上的帧耗时参考价值有限，请在真机上测量。
- 面板需要支持 kitty 图形协议的终端（Ghostty、kitty、iTerm2 3.5 及以上）。如果终端复用器能响应图形查询但报告了其他名称（例如 herdr 报告为 `libghostty`），请设置 `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1`。面板不支持 Windows。

## 与其他方案的比较

- **adb `screencap` + `input tap`**：agent 默认采用的方式，也就是 nolook 要替代的截图循环。
- **Maestro**：用 YAML 编写 UI 测试流程，成熟且快速。nolook 的流程是 Python，可以写条件和循环；输出面向 agent 的上下文设计：引用、变化、带下一步建议的拒绝信息。
- **Appium / uiautomator2 的 MCP 服务**：每个操作一次工具调用，返回原始结果。nolook 是 CLI，输出简短且稳定，长会话的上下文因此更小。
- **Web 端**：[playwright-cli](https://github.com/microsoft/playwright-cli) 和 [agent-browser](https://agent-browser.dev/) 在浏览器上采用了相同的思路。

nolook 基于 [uiautomator2](https://github.com/openatx/uiautomator2) 构建。

## 许可证

MIT
