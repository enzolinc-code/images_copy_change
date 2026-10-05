# pkg/ —— 可移植流水线

参考图 → 受控裂变 的整条流水线，从本机生产版（SQLite + 绝对路径）抽出来的可移植版本：
克隆下来就能跑，不依赖任何本机路径，状态用 JSON 文件存，**默认出图后端不花钱**。

```bash
export IMGCOPY_HOME=$PWD/_work        # PowerShell: $env:IMGCOPY_HOME="$PWD\_work"
python pkg/run.py doctor              # 自检
python pkg/tests/smoke.py             # 端到端冒烟测试（假母款 + replay 后端，全绿=0）
```

流程：`ingest → analyze → dna → plan → generate → qc → export → handoff`。
命令清单、配置覆盖方式、为什么默认只出「整幅成品图」见 [../references/install.md](../references/install.md)；
与本机版的逐条对应关系见 [../references/pipeline.md](../references/pipeline.md)。

目录：

```
pkg/run.py                 免安装入口：python pkg/run.py <命令>
pkg/imgcopy/               包本体（python -m imgcopy <命令>）
pkg/imgcopy/config/        随包默认配置（settings/geometry/operators/thresholds/...）
pkg/imgcopy/schemas/       JSON Schema
pkg/imgcopy/analyze|dna|plan|prompt|generate|qc|similarity|select|tools/
pkg/tests/smoke.py         端到端自测
```

依赖：Python 3.10+、numpy、Pillow、opencv-python。
