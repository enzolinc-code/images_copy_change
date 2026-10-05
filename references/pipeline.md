# 流水线细节

## 目录与数据

| 位置 | 内容 |
|---|---|
| `liebian\parents\<parent_id>\` | `parent.json`（母款快照与上游血缘）、`analysis.json`（CV 事实+语义标签）、`dna.json`（基因）、`plan.json`（裂变计划）、`children\`（子款图 + 每张的完整 prompt）、`rejected\`、`logs\` |
| `liebian\data\liebian.db` | 状态机（parent/child/verdict/gen_cache/run_log）；**状态以库为准**，metadata.json 只是导出物 |
| `liebian\out\batches\<批次>\` | `images/`、`metadata.json`、`manifest.csv`、`report.json`、`review.html` |
| `liebian\out\_jobs\o1key\<child_id>\` | 付费出图的任务目录（**已成功的任务可复用，不会重复扣费**） |
| `D:\AutoTaobao\styles\` | 风格卡库 + 选卡页面 |

## 母款名单

`data/inputs/*.json`：

```json
{"parents":[{"design_name":"M1005波点","source_image":"K:/自动套图 新/提取图案/1005/M1005波点.png",
             "data_trust":"unknown","note":"用户指定"}]}
```

从测款库导名单时用 `-m src.cli export-parents`（口径：爆款系统裂变池 / 测款库干净流量；本系统不判爆款）。

## 语义标签（analyze 会停下来等你）

`analyze` 会算出 CV 事实并写 `vision_request_labels.json`，然后要求把 JSON 写进同目录 `vision_answer_labels.json`（模板见 `liebian\config\prompts\analyze.md`）。要点：词表外的词一律 `unknown`；`ip_like` 只在主体明显是知名 IP 近似形象时给 true；有文字时给 `text.content`，要改字再加 `text.replace_from`。

**改了标签要清缓存**：标签按图片哈希缓存，改完必须删 `liebian\data\cache\vision\labels_<hash>.json` 再 `analyze --force`，否则读到的还是旧标签（踩过）。

## 算子与剂量

算子定义在 `liebian\config\operators.json`（配色/构图/道具/表情/动作/主体/风格/母题/语义/组合/文字替换/照片姿态/主角分列）。剂量 `L1/L2/L3` 映射到强度 0.2~0.6，`max_strength` 配置层就禁止 1.0。

按母款形态自动切换可用算子：

- **通版图案型**（波点、豹纹、格子这类无单一主体）→ 只用 母题/构图/配色/风格（+ 有实拍照片嵌图时可用照片姿态、有双人时可用主角分列）。
- **单一主体型** → 表情/动作/主体替换/风格/语义等。
- **IP-like 主体** → 禁止换主体。

## 风格卡

`style.json` 关键字段：`style_id / name / tags / source_parent / refs / canvas /
operators{算子:{values:[{text,name_word,en}]}} / text_rule{prompt_note 里用 {map}} /
naming / qc{known_risk} / examples / example_sources / how_to_use`。

`--style` 的效果：用卡里的取值池覆盖内置取值，并把卡里渲染好的改字规则写进提示词（优先于母款自带的 `prompt_note`）。

## 常见组合

```powershell
# 拆解一张参考图（免费）
& $py -X utf8 -m src.cli ingest --input data/inputs/parent_input_x.json
& $py -X utf8 -m src.cli analyze P_20261005_0001     # 写标签后重跑
& $py -X utf8 -m src.cli dna     P_20261005_0001

# 用风格卡排一批（免费）
& $py -X utf8 -m src.cli plan P_20261005_0001 --count 5 --operators motif,color --style bodian_polka

# 出图（付费，需用户确认）
& $py -X utf8 -m src.cli generate P_20261005_0001 --go

# 质检 + 交付
& $py -X utf8 -m src.cli qc P_20261005_0001 --force
& $py -X utf8 -m src.cli export P_20261005_0001
& $py -X utf8 -m src.cli handoff P_20261005_0001 --drop
```

## 可移植版（`pkg/imgcopy/`）的对应关系

同样的流程，把 `-m src.cli` 换成 `pkg/run.py`（或 `python -m imgcopy`，需先把 `pkg/` 加进 `sys.path`）：

| 本机版 | 可移植版 |
|---|---|
| `liebian\config\*` | `pkg/imgcopy/config/*`，可用 `<IMGCOPY_HOME>/config/*` 覆盖同名项 |
| `liebian\parents\<id>\` | `<IMGCOPY_HOME>/parents/<id>/`（结构一致：parent/analysis/dna/plan/children） |
| `liebian\data\liebian.db` | `<IMGCOPY_HOME>/data/state.json`（JSON 后端，接口同名） |
| `liebian\out\...` | `<IMGCOPY_HOME>/out/...` |
| `ROOT`（项目根） | `IMGCOPY_HOME`（默认当前目录） |

模块布局与另一份一致：`ingest.py / analyze/ / dna/ / plan/ / prompt/ / generate/ / qc/ /
similarity/ / select/ / tools/`，逐文件对应，方便两边对照改动。

`generate/` 三个后端：`replay`（免费假后端，默认）、`o1key`（付费，按张）、`comfyui`（本地免费）。
`handoff` 的整幅成品图在没配模板工具时用「contain 缩放 + 镜像延展补边」实现：
满幅、不留白边、不叠任何定位图形。
