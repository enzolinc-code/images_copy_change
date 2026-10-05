# 任务：为一张手机壳图案做结构化视觉拆解

你会看到一张手机壳图案（画布 1252×2232，正面朝上，画面里**不应该**有壳体/摄像头/文字水印；
如果看到了，说明输入有问题，在 `notes` 里说明）。

**纪律**

1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。
2. 词表以外的词一律填 `unknown`，不要自己发明标签。
3. 每条判断都要能落到画面上。不确定就给低 `confidence`，**不要猜**。
4. `ip_like` 只在主体明显是知名 IP 形象或其近似变体时填 `true`，
   并在 `ip_note` 里写清像哪个（例："像 Hello Kitty 的猫头造型"）。
5. 已经给你一组**像素级事实**（调色板、主体 bbox、留白比例、边缘密度、摄像头区重叠比例等）。
   这些是算出来的，不要推翻，只做语义解释；如果某个事实和你的观感冲突，写进 `notes`。

## 输出格式

```json
{
  "subject": {
    "primary": "<主体主词，必须在词表内，例如 猫/少女/花/短句/涂鸦>",
    "detail": "<更具体的描述，中文，≤20 字>",
    "count": 1,
    "ip_like": false,
    "ip_note": ""
  },
  "composition": ["<从 composition 词表里选 1~3 个>"],
  "style": { "<风格桶名或风格词>": 0.0 },
  "style_detail": ["<具体笔触/质感描述，例如 蜡笔颗粒感、粗描边、网点印刷>"],
  "emotion": { "<emotion 词表里的英文键>": 0.0 },
  "visual_hooks": [
    { "desc": "<最容易吸引眼球的元素，中文>", "strength": 0.0, "region": [x, y, w, h] }
  ],
  "text": { "present": false, "content": "", "role": "none|hook|decoration" },
  "notes": ""
}
```

- `style` / `emotion` 的权重是你对这张图的判断（0~1，可多选），不是概率。
- `region` 用归一化坐标 `[x, y, w, h]`（左上角为原点）。
- `visual_hooks` 按吸引力从高到低排，最多 3 条。
