# config.json 规范（pet-desktop.ps1 运行时读取）

桌宠运行时会读取同目录下的 `config.json`（UTF-8，有无 BOM 均可）。所有字段如下，标 ⚙️ 的是需要按规则计算的字段。

> 正常流程下 config.json 由 `scripts/build_pet.py` 生成，本文档用于排障或手工微调已生成的包。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| petName | string | 宠物名字，也是窗口标题。默认 "我的桌面宠物"，最长 40 字符 |
| width | number | 宠物图片显示宽度 px，40~600，默认 120 |
| height | number | 宠物图片显示高度 px，40~700，默认 140 |
| windowWidth ⚙️ | number | 窗口宽度，计算规则见下 |
| windowHeight ⚙️ | number | 窗口高度，计算规则见下 |
| defaultAction | string | 默认状态名，必须存在于 actions，通常是 "idle" |
| hoverAction / clickAction / doubleClickAction / restAction | string | 各触发的兜底状态名，取对应 interactions 数组第一条的 action |
| bubbleEnabled | boolean | 是否显示对话气泡 |
| bubbleText | string | 兜底台词，最长 120 字符 |
| bubblePosition | string | 固定 "top" |
| restDelayMs | number | 休息状态切换间隔（毫秒）= 用户给的分钟数 × 60000，范围 1~240 分钟 |
| idleAfterInteractionMs | number | 交互后待机时长，固定 60000 |
| actionDurationMs | number | 动作播放时长，固定 1100 |
| bubbleDisplayMs | number | 气泡显示时长，固定 3000 |
| alwaysOnTop | boolean | 固定 true |
| draggable | boolean | 固定 true |
| actions | object | 状态名 → 图片相对路径，如 `"idle": "assets/idle.png"`。每个状态都要有键，没图的状态指向 idle 的图。多帧状态时指向该状态的第一帧 |
| frames | object | 可选。状态名 → `{ "files": ["assets/状态_1.png", "assets/状态_2.png", ...], "fps": 8 }`，让该状态在显示期间按 fps 循环连播成小动画。省略（或某状态不在其中）则该状态按单图静态显示 |
| interactions | object | 四种触发各自的随机互动列表，结构见下 |

## windowWidth / windowHeight 计算规则

以下公式与 pet-desktop.ps1 运行时的实际计算保持一致（build_pet.py 也是按这个生成）：

```
longestText = 所有台词（bubbleText + 所有 interactions 里的 text）中最长的字符数
textBasedWidth = min(460, max(180, longestText × 18 + 52))

bubbleEnabled = true 时:
  windowWidth = max(width + 120, textBasedWidth + 20)
  windowHeight = height + 88
bubbleEnabled = false 时:
  windowWidth = width + 40
  windowHeight = height + 28
```

注意：运行时会取 `max(配置值, 运行时按上式算出的值)` 作为最终窗口尺寸——配置值偏小会被自动抬高，偏大则按配置值。所以手工改包时 windowWidth 宁大勿小。

## interactions 结构

四种触发固定为 `rest`、`hover`、`click`、`doubleClick`。每种是一个数组，数组元素：

```json
{ "action": "状态名", "text": "台词（可为空字符串）" }
```

- action 必须是 actions 里存在的键。
- 运行时从数组里随机抽一条；想固定某一种结果就只写一条。
- 每个触发至少保留一条。

默认互动（用户没提要求时直接使用）：

```json
{
  "rest": [
    { "action": "rest", "text": "我休息一下～" },
    { "action": "idle", "text": "发会儿呆。" }
  ],
  "hover": [
    { "action": "wave", "text": "嗨！" },
    { "action": "talk", "text": "找我玩吗？" }
  ],
  "click": [
    { "action": "happy", "text": "嘿嘿！" },
    { "action": "wave", "text": "我在这里！" }
  ],
  "doubleClick": [
    { "action": "talk", "text": "你双击我啦！" },
    { "action": "happy", "text": "今天也要开心！" }
  ]
}
```

注意：默认互动引用了 rest / wave / happy / talk 状态。如果用户只提供 idle 一张图，把这些状态的图片路径全部指向 idle 的图即可（actions 里键要全，路径可以重复）。

## 帧序列（frames）：一个状态多张图连播

把同一个状态的多个连续画面（例如挥手的第 1、2、3 帧）放进 `frames`，运行时进入该状态后会按 `fps` 循环切换 `Image.Source`，达成 GIF 般的播放效果。

```json
{
  "actions": { "wave": "assets/wave_1.png", "idle": "assets/idle.png" },
  "frames": {
    "wave": { "files": ["assets/wave_1.png", "assets/wave_2.png", "assets/wave_3.png"], "fps": 10 }
  }
}
```

- `files` 为相对 `config.json` 的图片路径，顺序即播放顺序，第一张应与 `actions[state]` 一致。
- `fps` 建议 6~12（值越大越快）；非整数会被取整，低于阈值会被夹到 40ms/帧。
- 帧序列与「程序动画」（呼吸/歪头/弹跳/跳跃）可共存：进入帧状态时帧切换照常，同时仍叠加变换动画。
- 生成方式：用 AI 对同一个状态分别出多张连续画面（提示词保持构图一致、只改姿势/进度），或把一段 AI 视频抽帧；由 `scripts/build_pet.py` 在 spec.images 的该状态写成「图片路径列表」时自动产出 `frames` 配置。
- 单图宠物（无 `frames`）完全兼容，运行时自动走原静态逻辑。

## 状态名与图片文件名规则

- 状态名：trim 后把空白替换为 `_`，最长 32 字符，必须唯一；`idle` 必须存在。
- 图片文件名：`<状态名>.<原扩展名>`，状态名里只保留英文、数字、`_`、`-` 和中文，其他字符替换为 `_`。
- 支持 png / jpg / jpeg / webp / gif。静态图最稳妥；gif 会以第一帧静态显示（WPF Image 不播放动画）。
- webp 需特别注意：WPF 原生编解码器不含 webp，能否显示取决于系统是否装了 WIC webp 编解码器（Windows 11 较新版本自带，Windows 10 通常没有）。build_pet.py 在构建环境装有 Pillow 时会把 webp 自动转成 png；否则照收并给出 warning，建议改用 png/jpg。

## 完整示例

见 `assets/windows-pet-template/config.json`，那就是一个可直接运行的示例。
