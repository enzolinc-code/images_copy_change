# 安装与依赖

## 1. 把 skill 装到本机

```powershell
# 方式一：直接拷贝
Copy-Item -Recurse -Force <本仓库> "$env:USERPROFILE\.codex\skills\images-copy-change"

# 方式二：让 Codex 用 skill-installer 从 GitHub 装
#   在 Codex 里说：用 skill-installer 安装 https://github.com/enzolinc-code/images_copy_change
```

装好后技能名是 `images-copy-change`（技能目录名要与 `SKILL.md` 里的 `name` 一致）。

## 2. 只做「风格卡库」需要什么

只要 **Python 3.10+**（生成页面只用标准库；`style_pack_publish.py` 压略图时用 Pillow，没装也能跑，只是示例图不压缩）。

```bash
# 生成/刷新选择器页面（产出 <root>/index.html，离线、双击即开）
python scripts/style_gallery.py --root D:/AutoTaobao/styles

# 发布一张卡（拷进库里 + 压略图 + 刷新页面）
python scripts/style_pack_publish.py --pack ./my_style --root D:/AutoTaobao/styles
```

从零建一张卡：拿一个 `assets/style.json.template` 改字段 → 填 `operators.*.values`（**必须写具体名词**）、`text_rule`、`example_sources` → 用上面的命令发布。

## 3. 要跑完整流水线（拆解 → 出图 → 质检 → 交付）还需要什么

完整流水线是另一个工程（本机在 `D:\AutoTaobao\shangji\liebian`），它额外需要：

| 依赖 | 用途 | 说明 |
|---|---|---|
| Python 3.12 + numpy / Pillow / opencv | CV 事实、相似度、图处理 | 本机用打包 runtime：`C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` |
| 图像生成通道 | 出图 | 本机默认 O1Key（GPT Image 2.5，**按张付费**）；也可换 ComfyUI / 本地模型 |
| 视觉判断通道 | 语义标签、质检 | 本机默认"agent 在环"（人/Codex 看图写标签）；可选 gemini-web |
| LaMa（可选） | 抹除局域（改字/去水印） | pattern-extract 的 `.venv-lama` |

**没有流水线也能用**：SKILL.md 里的流程是通用的——只要你有"能按提示词+参考图出图"的通道，就可以照着 §1 的步骤手工执行（拆解 → 写取值池 → 出图 → 人工/模型质检 → 按 §4 交付约定导出）。

## 4. 路径与配置

流水线里所有路径/阈值都在 `liebian\config\settings.json`（图案库、印刷目录、生成后端、预算上限、画布尺寸），换机器只改这一个文件。风格卡库里每张卡自带 `source_parent` 与 `example_sources`，都是相对路径或绝对路径，不写死在代码里。
