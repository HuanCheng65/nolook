# Changelog

All notable changes, grouped by kind. Generated from commit messages by
[git-cliff](https://git-cliff.org).


## [0.5.0](https://github.com/HuanCheng65/nolook/releases/tag/v0.5.0) — 2026-11-05

### Features

- Judge gpu_p90 too, and call a 3-run separation likely ([f4377bc](https://github.com/HuanCheng65/nolook/commit/f4377bcf4dd4a08f6a8404b82287b6451d7bc618))
- Fling one warm-up run before perf frames keeps any ([5720f48](https://github.com/HuanCheng65/nolook/commit/5720f485caefbe4d7f6b52706816e7b4ea18bb81))
- Measure A and B in turns, and warn when the later one ran hotter ([4ddbca3](https://github.com/HuanCheng65/nolook/commit/4ddbca302ed701a4ce98b40fe8a1a099555c9077))
- Journal each action, and mark measurements busy ([7ff06ca](https://github.com/HuanCheng65/nolook/commit/7ff06ca2882610cc3336a4cb59e398906be89df3))
- Stream the screen and the action journal for the pane ([a6603e3](https://github.com/HuanCheng65/nolook/commit/a6603e3560e795a8c9a64476773d4fae19450e3a))
- Redraw the device pane as a live mirror beside what nolook did ([9d71d37](https://github.com/HuanCheng65/nolook/commit/9d71d37e7f819ad7c2af95682a59226f5e0344ef))
- Name the app with --package, and press enter after typing in flows ([915ab7f](https://github.com/HuanCheng65/nolook/commit/915ab7f4592b613b641a6e9b9f6e34de49deb65f))
- Drive web pages with the same verbs as a device ([c0ff3d9](https://github.com/HuanCheng65/nolook/commit/c0ff3d954ab609a9df78e474bfa2664c96caddd1))
- Run a flow piped in on stdin ([8ce6a2f](https://github.com/HuanCheng65/nolook/commit/8ce6a2ff28c1e260be6331af7088e4159b2d73c0))
- Scope web queries to containers with an id ([342d223](https://github.com/HuanCheng65/nolook/commit/342d223a93226b6215ca70efe2c4b513ec28784c))
- Check the browser side in doctor when driving the web ([9f9398e](https://github.com/HuanCheng65/nolook/commit/9f9398e29c918624418563bcb6a9476bd0ca84f8))

### Fixes

- See the lock screen where only KeyguardController reports it ([bd0f8d2](https://github.com/HuanCheng65/nolook/commit/bd0f8d2e4f42ed5a210e5b71274f49abe81d83dc))

### Documentation

- Design a web backend that drives pages over CDP ([7e219e9](https://github.com/HuanCheng65/nolook/commit/7e219e9bf9c6a6527fa5626111dced25bb3a99b4))
- Rewrite the skill around when to switch from commands to flows ([73f36bc](https://github.com/HuanCheng65/nolook/commit/73f36bcd54002662811794846939211076573c28))
- Describe driving web pages in the README and the package metadata ([8e92da1](https://github.com/HuanCheng65/nolook/commit/8e92da10ffe891faff307dcd54597ea6f078358f))

### Refactoring

- Move the Android driver behind a Backend ([862be5a](https://github.com/HuanCheng65/nolook/commit/862be5aead04d8b4a3747c65fd600f6776ab1d0f))
- Reach adb in perf and motion through an explicit Android backend ([22b957e](https://github.com/HuanCheng65/nolook/commit/22b957edfa84b1de67b57a6438daa6c41b752aaa))

## [0.4.0](https://github.com/HuanCheng65/nolook/releases/tag/v0.4.0) — 2026-11-03

### Features

- Let flows measure, close interruptions, and hand back results ([aca7730](https://github.com/HuanCheng65/nolook/commit/aca7730261385b9a6b18fb18609a0353efe878e8))
- Write flows with locators, waiting actions and plain assert ([27fd1af](https://github.com/HuanCheng65/nolook/commit/27fd1af6cc9c7cf20dfca731d2047e1e8549cb68))
- Measure frames over several runs, flag noise, and sample hangs ([dfdfd7f](https://github.com/HuanCheng65/nolook/commit/dfdfd7fa104851f600a518713a7af9d7236d6b6c))
- Say why a step found nothing, and how long flow steps waited ([7a24584](https://github.com/HuanCheng65/nolook/commit/7a24584688486f9c9db7ca62bce608d4b87bbb35))

### Fixes

- Fling with input swipe, timed on the device ([4dedaf7](https://github.com/HuanCheng65/nolook/commit/4dedaf777914dfe21d680da3521698109f9c8cca))
- Keep the pane's internal topng command out of --help ([dbd541d](https://github.com/HuanCheng65/nolook/commit/dbd541df0277161bfe88a597f38d4101ae18171b))
- Drop topng from the command list and the notes-app query example ([8e24894](https://github.com/HuanCheng65/nolook/commit/8e248942408bbc8e24d8e0f7bc017619ac4daeff))

### Documentation

- Describe the flow API's steps, measuring, interruptions and results ([f3643c1](https://github.com/HuanCheng65/nolook/commit/f3643c1139e52cc778e750274e4bd8650507b299))
- Show flows written with locators, waiting actions and assert ([a863b12](https://github.com/HuanCheng65/nolook/commit/a863b120cf5dc1006790781abb915e339a4880ef))
- Rules for honest perf conclusions, perf stack, and slow-wait flags ([99c1fda](https://github.com/HuanCheng65/nolook/commit/99c1fda57fc4ec03ef0b0c8b2d3bd0a0c081df72))

## [0.3.0](https://github.com/HuanCheng65/nolook/releases/tag/v0.3.0) — 2026-11-03

### Features

- Drive Android apps by their UI tree for coding agents ([9563d15](https://github.com/HuanCheng65/nolook/commit/9563d1542363bcbe97fa644bea51f86289313a07))
- Package the skill and a device-driver agent as a Claude Code plugin ([b6598c7](https://github.com/HuanCheng65/nolook/commit/b6598c774f6a017029dee2914c10331f0d3d90a0))
- Measure frame timing across swipes and compare labelled runs ([14a71f4](https://github.com/HuanCheng65/nolook/commit/14a71f441a417729719eaeb670660e18c63b55d3))
- Measure animations from a screen recording ([fe876be](https://github.com/HuanCheng65/nolook/commit/fe876bedc7795101b2e5b3a0a6022eccd563e8bc))
- Save small screenshots as PNG for panes that draw only PNG ([308c3c6](https://github.com/HuanCheng65/nolook/commit/308c3c6a9a3cdc4586186f253510697051a9b4d3))
- Show the device in a Claude Code pane beside the chat ([484cab4](https://github.com/HuanCheng65/nolook/commit/484cab40b60e23cfac99a6cd2eb42e7a5595125f))
- Fling at a chosen speed in perf frames and report the speed reached ([a3894a1](https://github.com/HuanCheng65/nolook/commit/a3894a1f3edeb7a90d91d2f7717186d7f02aecc3))
- Summarise Perfetto traces without a local trace processor ([a8c7f42](https://github.com/HuanCheng65/nolook/commit/a8c7f424dee7843668d3aa4ea515ba41e187f71f))
- Add doctor and snap --json, and keep the pane's files in the cache ([8ab2317](https://github.com/HuanCheng65/nolook/commit/8ab23173873c51b9860b17fcb1dac13559c57da8))

### Fixes

- Show the device the session's agent drove, not the default one ([c11fdbb](https://github.com/HuanCheng65/nolook/commit/c11fdbb962bb78c03a561f5f68c8279517f7f833))

### Documentation

- Rewrite the README in English and Chinese, and add the MIT license ([28f81f6](https://github.com/HuanCheng65/nolook/commit/28f81f6273a64cefdd36f8485411be283b73362d))

