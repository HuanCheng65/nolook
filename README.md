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

See [skills/nolook/SKILL.md](skills/nolook/SKILL.md) for the full usage an
agent gets.

## Install

```
uv tool install --editable .
```

Backed by [uiautomator2](https://github.com/openatx/uiautomator2); its
on-device server is pushed on first connect.
