# nolook

Drive an Android app from a coding agent by its UI tree, not by screenshots.

Every screenshot an agent reads stays in its conversation and is re-sent with
every later request. nolook reads the screen as a short text list, taps by
text or id, reports what changed after each action, runs multi-step Python
flows in one call, and takes small cropped screenshots only when asked.

```
$ nolook tap 动态
tap @e73 btn "动态" (356,2221) · settled 0.96s
screen changed (10 elements):
@e77  btn    "推荐" (540,141)
…
```

See [plugin/skills/nolook/SKILL.md](plugin/skills/nolook/SKILL.md) for the full usage an
agent gets.

## Install

```
uv tool install --editable .
```

Then in Claude Code:

```
/plugin marketplace add ~/nolook
/plugin install nolook@nolook
```

Backed by [uiautomator2](https://github.com/openatx/uiautomator2); its
on-device server is pushed on first connect.

## The device pane

In Claude Code the plugin also adds a pane beside the chat that shows the
device after every nolook command, the last screenshot or motion sheet the
agent saved, and the last few actions. The pictures go to your terminal
only; the model never reads them.

`/device` opens it, `/device live` refreshes it every two seconds,
`/device still` stops that. In the pane, `r` refreshes and `l` toggles live.

It needs a terminal with the kitty graphics protocol (Ghostty, kitty, or
iTerm2 3.5+). Inside a multiplexer Claude Code may not recognise one: herdr,
for instance, answers the graphics query but reports itself as
`libghostty`. Set `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1` there.
