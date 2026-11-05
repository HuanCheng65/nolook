<h1 align="center">nolook</h1>

<h3 align="center">All of the screen. None of the pixels.</h3>

<p align="center">为编程 agent 设计的界面自动化工具，支持 Android 应用和网页。</p>

<p align="center">
  <a href="https://pypi.org/project/nolook/"><img alt="PyPI" src="https://img.shields.io/pypi/v/nolook?color=2b2b2b"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-2b2b2b">
  <a href="https://github.com/HuanCheng65/nolook/blob/main/README.zh-CN.md#claude-code"><img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-2b2b2b"></a>
  <a href="https://github.com/HuanCheng65/nolook/blob/main/LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-2b2b2b"></a>
</p>

<p align="center">
  <a href="https://github.com/HuanCheng65/nolook/blob/main/README.md">English</a> · 中文
</p>

agent 检查应用时，通常是这样一个循环：截图、读图、点击、再截图。一张整屏截图约 1,500 token，并在之后的整个会话中一直占用上下文窗口。到第二十步时，对话里已经积累了几万 token 不再需要的图片。

nolook 把屏幕变成几行文本交给 agent，之后的每条输出也同样简短：点击后只报告变化，整个流程只返回一行结果，动画只给一页数字。只有需要判断视觉效果时才截图，而且只截一小块，agent 确有需要时才读取。

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

<sub>一张 1080×2400 的截图按 Claude 默认图片尺寸缩放为 706×1568，按 宽 × 高 / 750 计算约 1,476 token。上面这次 `snap` 不到 200 token。</sub>

## 上下文花在哪里

| agent 想要 | 截图的做法 | nolook |
|---|---|---|
| 查看屏幕 | 一张整屏截图 | `snap`：每个元素一行，附带引用 |
| 知道点击的结果 | 再截一张图 | 只列出新出现和消失的元素 |
| 走过五个页面 | 五轮对话、五张图 | 一个 flow、一轮对话、一行 JSON |
| 检查视觉效果 | 一张整屏截图 | 只截取一个元素，保存为文件，只打印路径和预估 token 数 |
| 判断动画 | 几十帧画面 | 时长、曲线、过冲和卡顿的文字报告，外加一张关键帧拼图 |
| 排查卡顿 | 原始 trace | 一页 trace 摘要 |
| 让你看到画面 | 对话里的截图 | 对话旁的面板实时显示屏幕，只给你看（Claude Code） |

同样的命令也能通过 DevTools 协议操作 Chromium 中的网页。

## 开始使用

要求：Python 3.11 及以上，`adb` 在 `PATH` 中，设备或模拟器已开启 USB 调试；`motion` 需要 `ffmpeg`。首次连接时，nolook 会把 uiautomator2 的测试服务推送到设备上，不会改动你的应用。操作网页时只需要 Chrome 或 Chromium，不需要 adb 和设备。

```sh
uv tool install nolook          # 或：pipx install nolook
nolook doctor                   # 检查 adb、设备、ffmpeg 和 Perfetto
nolook snap                     # 查看屏幕内容
nolook tap "网络和互联网"        # 按文字点击，并打印变化
```

### Claude Code

```
/plugin marketplace add HuanCheng65/nolook
/plugin install nolook@nolook
```

插件包含 `nolook` skill、`nolook-driver` agent（执行多步设备操作，只以文字汇报结果），以及 `/device` 面板。

### 其他 agent

任何能执行 shell 命令的 agent 都可以使用 nolook。把 [`SKILL.md`](https://github.com/HuanCheng65/nolook/blob/main/plugin/skills/nolook/SKILL.md) 作为说明交给它即可。

> **给 agent。** 先运行 `nolook doctor`，按它的提示处理。先用命令探索，路径确定后改用 flow（`nolook run -`）。只有问题在于视觉效果时才读取截图。点击被拒绝时，提示会说明原因和下一步；只有用户明确要求该操作时才加 `--confirm`。

## 命令

```sh
nolook snap                         # 查看屏幕内容
nolook tap "网络和互联网"            # 按文字点击，并打印变化
nolook type "你好" --into "#search"
nolook scroll down --until "关于手机"
nolook wait "已保存" --timeout 5
nolook back
nolook get @e4                      # 一个元素的全部信息
nolook shot "#toolbar"              # 截取一小块，打印路径
nolook run flows/open_note.py       # 一次执行多个步骤
nolook --help                       # 全部命令
```

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

### 拒绝的操作

- 查询必须恰好匹配一个元素。已经离开屏幕的引用会报错，而不会点到原位置上的其他元素。
- 被浮动栏遮住的元素（`[covered]`）拒绝点击。
- 看起来是写操作的按钮（点赞、关注、发送、删除等），除非用 `--confirm` 明确要求，否则拒绝点击。项目可以添加自己的标签，以及每次操作后在 logcat 中检查的规则。

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

### 网页

```sh
nolook web start http://localhost:3000   # 无头运行；--headed 显示窗口
nolook --web snap
nolook --web tap 登录
nolook --web open /settings              # 相对于 [web] 中的 url
nolook web status                        # 浏览器及其页面
nolook web stop
```

浏览器在命令之间保持运行，页面、滚动位置和登录状态都会保留。等待界面稳定时，也会等操作发出的网络请求完成。`key back` 是浏览器后退。`nolook web attach` 可以连接你自己用 `--remote-debugging-port` 启动的 Chrome。

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

[web]                           # 或使用 --web
url = "http://localhost:3000"   # launch 打开的地址；open /path 相对于它
profile = "dev"                 # 浏览器配置：cookie 和登录状态保存在这里
device = "desk"                 # 窗口大小：desk（1440×900）| phone（390×844）| 宽x高
```

环境变量 `NOLOOK_SERIAL`、`NOLOOK_ADB_PORT`、`NOLOOK_PACKAGE` 优先于配置文件，命令前的 `--serial`、`--package` 也一样：`nolook --package com.example.notes run flow.py`。

**让应用可读。** 对于 Jetpack Compose，请在根部设置 `Modifier.semantics { testTagsAsResourceId = true }`（仅 debug 构建即可），使 `testTag` 显示为 `#id`；并为图标按钮设置 `contentDescription`。网页上，`data-testid` 显示为 `#id`，`aria-label` 为图标按钮命名。

## 局限

- 无障碍树中没有的内容，nolook 也看不到：WebView 的部分内容、canvas、游戏、视频。这些场景请使用 `tap --xy` 和截图。
- Flutter 应用暴露给无障碍树的信息很少。
- `motion` 只判断像素，不判断意图：它能说明动画持续多久、曲线是什么形状，不能判断手感是否合适。请与可信版本的带标签记录对比。
- 模拟器上的帧耗时参考价值有限，请在真机上测量。
- 网页上暂不支持 `log`、`motion` 和 `perf`。对无头浏览器弹出人机验证的网站，请用 `nolook web attach` 连接你自己启动的 Chrome。
- 面板需要支持 kitty 图形协议的终端（Ghostty、kitty、iTerm2 3.5 及以上）。如果终端复用器能响应图形查询但报告了其他名称（例如 herdr 报告为 `libghostty`），请设置 `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1`。面板不支持 Windows。

## 与其他方案的比较

| 方案 | 用途 | nolook 的不同 |
|---|---|---|
| adb `screencap` + `input tap` | agent 默认采用的方式 | 正是 nolook 要替代的截图循环 |
| Maestro | 用 YAML 编写 UI 测试流程，成熟且快速 | flow 是 Python，可以写条件和循环；输出面向 agent 的上下文：引用、变化、带下一步建议的拒绝信息 |
| Appium / uiautomator2 的 MCP 服务 | 每个操作一次工具调用，返回原始结果 | CLI 输出简短且稳定，长会话的上下文因此更小 |
| playwright-cli、agent-browser | 在浏览器上采用同样以文本为先的思路 | 同一套命令和 flow 同时操作 Android 应用和对应的网页 |

nolook 通过 [uiautomator2](https://github.com/openatx/uiautomator2) 操作 Android，通过 Chrome DevTools 协议操作网页。

## 许可证

[MIT](https://github.com/HuanCheng65/nolook/blob/main/LICENSE)
