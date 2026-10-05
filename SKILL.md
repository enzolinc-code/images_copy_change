---
name: images-copy-change
description: 拿一张参考图（手机壳图案/商品主图/竞店图）做视觉拆解，再按「受控变异」生成一批风格相近、但可单独上架的新图；含风格卡库、AI 质检与交付约定。用户说"拆解这张图/照着这张图做几张/换个姿势底纹不变/把某个风格冻结成卡片/选卡出图"时使用。不负责淘宝上架、客服、订单。
---

# 参考图 → 相似图（受控裂变）

核心原则：**不从零随机生成，而是围绕参考图做受控变异**——先拆解出「哪些特征必须保住、哪些可以动」，再按矩阵逐张生成，最后用 AI 质检筛掉废图。

## 0. 先读本机规矩

动手前先读 `D:\AutoTaobao\规矩文档.txt` 与 `D:\AutoTaobao\踩坑日志.txt`（读不到要如实告诉用户）。产出全部落在 `D:\AutoTaobao` 下；**没有特别说明不要擅自决定**（字体、张数、是否花钱、交付范围、命名、用本地还是模型手段，都要先问）。

流水线本体在 `D:\AutoTaobao\shangji\liebian`（Python，用打包 runtime 的解释器跑）：

```powershell
$py = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
cd D:\AutoTaobao\shangji\liebian
```

## 1. 标准流程

```
入库 ingest → 视觉拆解 analyze → 基因 dna → 裂变计划 plan → 出图 generate
          → 质检 qc → 批次导出 export → 交付 handoff（印刷三件套 + 图案库整幅成品图）
```

| 步骤 | 命令 | 说明 |
|---|---|---|
| 入库 | `-m src.cli ingest --input data/inputs/<名单>.json` | 名单里可给 `source_image` 直接指向本地图；母图只读快照 |
| 拆解 | `-m src.cli analyze <parent_id>` | CV 事实自动算；**语义标签要人/agent 看图写**（会停在 `vision_answer_labels.json`） |
| 基因 | `-m src.cli dna <parent_id>` | 分 CORE / MUTABLE / DISPOSABLE，并算出允许哪些算子 |
| 计划 | `-m src.cli plan <parent_id> --count N --operators a,b,c [--style <卡id>]` | 先出计划后出图；单变量为主便于归因 |
| 出图 | `-m src.cli generate <parent_id>` → 确认后加 `--go` | 默认 dry-run；**张数 = 付费次数** |
| 质检 | `-m src.cli qc <parent_id> --force` | 技术 / 像素 / 相似度三闸 + 批次内去重 |
| 导出 | `-m src.cli export <parent_id>` | 只导通过质检的；`--include-rejected` 才连淘汰的一起导 |
| 交付 | `-m src.cli handoff <parent_id> --drop` | 印刷目录三件套（`_印刷`/`_预览`/无框整幅）+ 图案库整幅成品图 |

### 1.1 也可以直接用仓库自带的那份流水线（`pkg/`）

本仓库自带一份**可移植**的流水线（从上面那台本机版本抽出来的），克隆下来就能跑：不依赖任何
本机绝对路径，状态用 JSON 文件存，出图后端默认是**不花钱**的 replay 假后端。

```bash
export IMGCOPY_HOME=/path/to/workdir      # PowerShell: $env:IMGCOPY_HOME="D:\work"
python pkg/run.py doctor                  # 自检：依赖、配置、路径、后端
python pkg/run.py ingest                  # 读 <workdir>/data/inputs/parent_input.json
python pkg/run.py analyze --all           # 视觉拆解（agent 模式会停下来等标签）
python pkg/run.py dna --all               # 提取基因
python pkg/run.py plan --all --count 5 --operators color,composition [--style <卡id>]
python pkg/run.py generate <parent_id>    # 默认 dry-run；确认后加 --go
python pkg/run.py qc --all                # 质检 + 批次内去重
python pkg/run.py export --all            # 只导通过质检的
python pkg/run.py handoff --all --drop     # 交接单 + 整幅成品图 +（能出时）印刷三件套
```

- 工作目录全在 `IMGCOPY_HOME` 下（`parents/ out/ data/`）；随包默认配置在
  `pkg/imgcopy/config/`，你自己的路径写进 `<IMGCOPY_HOME>/config/settings.json` 覆盖同名项。
- 真出图：把 `generator.type` 改成 `o1key` 并填 `generator.o1key.script`（或 `comfyui`）。
- 依赖：Python 3.10+、numpy、Pillow、opencv-python。细节见 [references/install.md](references/install.md)。

## 2. 风格卡（选卡即复用）

把做好的一个风格冻结成卡片，以后换母款直接复用：卡片决定**算子取值池 + 文字规则**。

- 卡片库：`D:\AutoTaobao\styles\<style_id>\style.json`（含 `refs/`、`examples/`）
- 选择器页面：`D:\AutoTaobao\styles\index.html`（离线，点卡看示例、复制命令、导出选择 JSON）
- 用卡：`plan ... --style <style_id>`
- 建卡：写 `style.json` → `-m src.tools.style_pack_publish --pack <暂存目录>`（自动拷过去、压缩略图、刷新页面）

卡模板见 `assets/style.json.template`；机制细节见 [references/pipeline.md](references/pipeline.md)。

脚本（不依赖本机路径，可单独跑）：

```bash
python scripts/style_gallery.py --root <风格库目录>          # 生成/刷新选择器页面
python scripts/style_pack_publish.py --pack <卡目录> --root <风格库目录>
```

安装与依赖、以及"没有流水线时怎么手工走一遍"见 [references/install.md](references/install.md)。

## 3. 铁律（都是实测踩出来的）

1. **取值写具体名词**：写"换成同情绪的另一动物"模型几乎不动；写"换成一只圆头小老鼠"才生效。
2. **文字让模型自己写，字体跟随原图**：不要用本地字体去仿（Georgia/Courier 都不像）。做法是在标签里给 `text.replace_from`，流水线会自动生成"把 X 换成 Y"的强指令。
3. **商标/品牌字样不能留**：命中品牌词要按"可丢弃"处理并明确要求替换；长商标词模型改不准，走本地 inpaint + 写字。
4. **规则纹样不要交给 AI 重画**（点阵/格子/豹纹底）：点距会走形；要换密度就走几何重建或保留原像素。
5. **一个设计只出一套**：交付目录同名文件会互相覆盖；新子款命名前先查重名，发现了先报不要自己改名。
6. **合规**：作者署名/店铺 logo 去不去必须问用户；IP-like 主体默认禁止换主体，只裂配色/构图/道具。
7. **花钱前先确认**：先 `plan` + dry-run 给用户看，得到明确指令再 `--go`；能复用已付费输出就复用。
8. **看图纪律**：大图先压到最长边 ≤1280 再看，避免会话被 413 掐断。

更多实例与坑见 [references/pitfalls.md](references/pitfalls.md)。

## 4. 交付约定

- 印刷目录 `K:\自动套图 新\印刷文件\<MMDD>\`：`<名字>.png`（纯图案不叠任何定位图形）、`<名字>_印刷.png`（透明底+壳体裁形+摄像头镂空）、`<名字>_预览.png`（白底按壳体裁形）。**禁止把壳体轮廓/角标/摄像头图形烤进图里**。
- 图案库 `K:\自动套图 新\提取图案\<MMDD>\`：只放整幅成品图，命名不带后缀。
- 中间产物（几何检查、备选、旧版）不进交付目录，放项目 `out\` 或 `_过程\`。
