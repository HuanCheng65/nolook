# nolook

让编程 agent 通过控件树而不是截图来操作 Android 应用和网页。

[English](README.md)

agent 在设备上调试应用时，通常是这样一个循环：截图、读图、估出按钮坐标、点击、再截图。每张截图都会留在对话里，并随之后的每一次请求重新发送，所以步骤越多，会话越慢、越贵；估出来的坐标也可能点错地方。

nolook 把屏幕内容变成一份简短的文本列表交给 agent。它按元素的文字或 id 点击，每次操作后报告界面发生了哪些变化，可以一次调用执行多个步骤，只有在需要判断视觉效果时才截取一小块图片。同样的命令也能通过 DevTools 协议操作 Chromium 中的网页。

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
- **也能操作网页。** `nolook web start` 让一个无头 Chromium 在命令之间保持运行，页面、滚动位置和登录状态都会保留；加 `--web`（或在配置里写 `[web]` 表）后，所有命令都作用于它。也可以连接你自己用 `--remote-debugging-port` 启动的 Chrome。
- **向你展示设备画面**（Claude Code）：对话旁的面板实时显示设备屏幕，框出每次点击命中的元素，并列出每个操作的结果，子 agent 和 flow 的操作也包括在内。画面只发给你的终端，模型不会读到。perf 或 motion 测量期间，面板不会截图。

## 安装

要求：Python 3.11 及以上，`adb` 在 `PATH` 中，设备或模拟器已开启 USB 调试。`motion` 需要 `ffmpeg`。首次连接时，nolook 会把 uiautomator2 的测试服务推送到设备上，不会改动你的应用。操作网页时只需要 Chrome 或 Chromium，不需要 adb 和设备。

```sh
uv tool install nolook          # 或：pipx install nolook
nolook doctor                   # 检查 adb、设备、ffmpeg 和 Perfetto
nolook --web doctor             # 检查浏览器、网站和浏览器配置
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

### 网页

```sh
nolook web start http://localhost:3000   # 无头运行；--headed 显示窗口
nolook --web snap
nolook --web tap 登录
nolook --web open /settings              # 相对于 [web] 中的 url
nolook web status                        # 浏览器及其页面
nolook web stop
```

等待界面稳定时，也会等操作发出的网络请求完成。`key back` 是浏览器后退。

### 查询语法

| 写法 | 匹配 |
|---|---|
| `@e12` | 上一次输出中的引用 |
| `#title` | resource id 或 Compose 的 test tag；网页上是 `data-testid`，没有时用 `id` |
| `保存` | 文字、内容描述，或列表行标签中的一段，完全一致 |
| `~保` | 标签包含 |
| `/^第\d+项$/` | 标签匹配正则表达式 |
| `btn:保存` | 以上任一写法，并限定角色 |

没有匹配时，nolook 列出最接近的元素；匹配到多个时，列出它们，并提示改用引用、角色或 `--nth`。

### 流程

```python
"""打开一条笔记并检查编辑器。"""
with d.dismissing("给我们评分", tap="以后再说"):
    d.launch(fresh=True)
    d.go("笔记", "购物清单")            # 每次点击都会等目标出现
    step = d("#toolbar").tap("编辑")    # 只在 #toolbar 内查找
    assert step.shows("#body")
    d.result(title=d("#title").text)    # 以一行 JSON 返回
```

`nolook run open_note` 每一步打印一行。失败时打印失败的步骤和行号、当时的屏幕内容，以及一张已保存但未读取的截图的路径。

每个操作都会等待目标出现在屏幕上且未被遮挡，因此 flow 中很少需要显式等待。`d(q)` 是一个定位器：每次使用时重新查找元素，并可以把查找范围限定在某个容器内。每个操作都会返回它造成的变化（`.added`、`.removed`、`.shows(q)`），检查直接使用 `assert`，还可以在 flow 中直接测量（`d.frames()`、`with d.measure("x"):`、`d.motion(…)`、`with d.slow(4):`）。

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
nolook perf ab --a "adb install -r a.apk && nolook run open-feed" --b "adb install -r b.apk && nolook run open-feed"   # 交替测 A B B A …
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

环境变量 `NOLOOK_SERIAL`、`NOLOOK_ADB_PORT`、`NOLOOK_PACKAGE` 优先于配置文件，命令前的 `--serial`、`--package` 也一样：`nolook --package com.example.notes run flow.py`。

对于 Jetpack Compose，请在根部设置 `Modifier.semantics { testTagsAsResourceId = true }`（仅 debug 构建即可），使 `testTag` 显示为 `#id`；并为图标按钮设置 `contentDescription`。网页上，`data-testid` 显示为 `#id`，`aria-label` 为图标按钮命名。

操作网页时，加一个 `[web]` 表（或使用 `--web`）：

```toml
[web]
url = "http://localhost:3000"   # launch 打开的地址；open /path 相对于它
profile = "dev"                 # 浏览器配置：cookie 和登录状态保存在这里
device = "desk"                 # 窗口大小：desk（1440×900）| phone（390×844）| 宽x高
```

## 局限

- 无障碍树中没有的内容，nolook 也看不到：WebView 的部分内容、canvas、游戏、视频。这些场景请使用 `tap --xy` 和截图。
- Flutter 应用暴露给无障碍树的信息很少。
- `motion` 只判断像素，不判断意图：它能说明动画持续多久、曲线是什么形状，不能判断手感是否合适。请与可信版本的带标签记录对比。
- 模拟器上的帧耗时参考价值有限，请在真机上测量。
- 网页上暂不支持 `log`、`motion` 和 `perf`。对无头浏览器弹出人机验证的网站，请用 `nolook web attach` 连接你自己启动的 Chrome。
- 面板需要支持 kitty 图形协议的终端（Ghostty、kitty、iTerm2 3.5 及以上）。如果终端复用器能响应图形查询但报告了其他名称（例如 herdr 报告为 `libghostty`），请设置 `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1`。面板不支持 Windows。

## 与其他方案的比较

- **adb `screencap` + `input tap`**：agent 默认采用的方式，也就是 nolook 要替代的截图循环。
- **Maestro**：用 YAML 编写 UI 测试流程，成熟且快速。nolook 的流程是 Python，可以写条件和循环；输出面向 agent 的上下文设计：引用、变化、带下一步建议的拒绝信息。
- **Appium / uiautomator2 的 MCP 服务**：每个操作一次工具调用，返回原始结果。nolook 是 CLI，输出简短且稳定，长会话的上下文因此更小。
- **playwright-cli、agent-browser**：在浏览器上采用了同样以文本为先的思路。nolook 用同一套命令和 flow 同时操作 Android 应用和对应的网页。

nolook 通过 [uiautomator2](https://github.com/openatx/uiautomator2) 操作 Android，通过 Chrome DevTools 协议操作网页。

## 许可证

MIT
