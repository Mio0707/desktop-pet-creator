---
name: desktop-pet-creator
description: 把用户提供的图片制作成 Windows 桌面宠物（桌宠）——桌面上一个无边框透明、可拖拽、会随机换图和弹台词气泡的小宠物。面向电脑小白的默认用法：给一张照片即可全自动生成并装到桌面，双击启动文件就能看到宠物，全程无需任何配置、不消耗额外积分。当用户想做桌面宠物、桌宠、电子宠物，或想把某张图片/表情包/自设角色变成桌面上会动会说话的小伙伴时使用。产出是可直接运行的宠物包（zip + 桌面启动器），用户机器上无需安装 Node.js 或任何开发环境。
agent_created: true
---

# Desktop Pet Creator 桌面宠物生成器

## Overview

根据用户上传的一张图片，全自动生成可直接运行的 Windows 桌面宠物，并自动安装到用户桌面。宠物基于 PowerShell + WPF（运行时模板 `assets/windows-pet-template/`），支持四种交互（休息/悬停/点击/双击）、随机台词气泡、拖拽移动、程序动画（待机呼吸、悬停歪头、单击弹跳、双击跳跃），右键或 ESC 关闭。

组装由 `scripts/build_pet.py` 确定性完成（复制模板、图片改名、宽高与窗口尺寸计算、生成 config.json、自检、打包；加 `--install` 时额外安装到桌面）。**不要手工拼 config.json 或手工打包**——编码、尺寸公式、zip 目录层级都是易错点，脚本已内建校验，出错会拒建并说明原因。

核心原则：**面向小白，默认零门槛——用户只出一张照片，其余全自动，不问任何配置、不弹任何页面、不消耗额外积分。** 想要更精细（多动作状态 / 自定义每句台词 / 帧动画）再走进阶模式。

## 两条路径

先判断用户要哪种：

- **零门槛模式（默认，电脑小白首选）**：一张照片 → 全自动出宠并装到桌面 → 双击启动。不问配置、不弹制作台、不派生多状态、零额外积分。
- **进阶自定义模式**：用户明确要多个动作状态、自己配每句互动台词、做帧序列动画，或点名要可视化制作台时走这条。

## 零门槛模式（默认路径）

目标：用户只做两件事——**拖一张照片 + 说"做个桌宠"**，然后**双击桌面的启动文件**。中间全部自动。

1. **取照片**：用户拖入一张宠物照片即触发。若一次拖了多张，默认用最近拖入的那张（用 Glob 按修改时间找最新的 `**/*.png`/`**/*.jpg`/`**/*.jpeg`/`**/*.webp`/`**/*.gif`）；用户没给图就先请他把图片拖进对话。
2. **全自动构建**：写一份最简 build-spec.json（只给 idle 图，其余全默认），用 managed Python 跑：

```bash
"C:\Users\goodfather02\.workbuddy\binaries\python\versions\3.13.12\python.exe" \
  "C:\Users\goodfather02\.workbuddy\skills\desktop-pet-creator\scripts\build_pet.py" \
  --spec "<workspace>\build-spec.json" --workspace "<workspace>" --install
```

最简 spec 示例：

```json
{
  "petName": "我的桌面宠物",
  "images": { "idle": "照片绝对路径" },
  "bubbleText": "你好呀！",
  "restMinutes": 8
}
```

`--install` 会让脚本一次性完成：构建 zip → 安装到 `~/DesktopPets/<宠物名>/` → 在桌面生成 `<宠物名>-桌宠.bat`（GBK/mbcs 编码，中文路径不乱码）。stdout 返回一行 JSON，含 `installDir`、`launcher`、`launcherName`；`"ok": false` 时按 `error` 修 spec 重跑。

3. **零打断原则**：本模式**不要**调用 AskUserQuestion、**不要**启动 maker_server、**不要**逐张确认图、**不要**调用 ImageGen。名字/尺寸/间隔/气泡/四种互动台词全部走默认（默认互动表见 `references/config-schema.md`）。被引用的状态若没有单独图，脚本自动复用 idle 并给 warning，宠物照样能跑能说话。
4. **交付**：一句话告诉用户——**双击桌面上的「<宠物名>-桌宠.bat」就能看到宠物；拖拽可移动，右键或按 ESC 关闭**。可顺手把返回的 zip 用 present_files 给用户留作备份。
   - 宠物只能在 Windows 运行；若双击后 Windows 提示是否允许运行脚本，选「允许」（启动后无黑窗口）。
   - **不要代用户启动**——沙箱内 GUI 进程窗口到不了用户桌面（历史实测 Bash/PowerShell 启动均被拦或不可见），让用户自己双击是唯一可靠路径。
5. **关于背景**：照片若带复杂背景，宠物会连同背景一起显示在桌面（矩形块）。零门槛模式不自动抠图（避免额外积分 / AI 调用）。用户想透明背景时再说一句「帮我把背景去掉」，再按进阶模式处理：纯色背景用 `scripts/remove_bg.py` 免费抠；复杂背景可用 ImageGen 重生成透明版，需先告知积分成本并征得同意。

## 进阶自定义模式（可选）

用户明确要更生动或更可控时启用。

### 收集图片与状态

- 至少 1 张 idle 图；多张时与用户确认状态名（idle/rest/curious/happy/excited/wave/talk/sleep…，可中文，≤32 字符，空白转 `_`，`idle` 必存在）。图片支持 png/jpg/webp/gif。
- 只有 1 张：作为 idle，其余状态复用它。
- **AI 派生多状态（可选增强）**：用户只有 1 张真实照片但想要多动作时，用 `references/pet-state-prompts.md` 的提示词模板做 ImageGen 图生图。先报张数与预估积分（每张约 5-10），用户同意后再生成；先出 idle 确认风格与相似度，再批量其余；逐张过目，身份漂移大或构图不统一的单张重试。状态图就位后照常构建；状态与 interactions 的映射按 references 推荐表。
- **帧序列动画（GIF 效果）**：对某状态生成 2~4 张连续画面（提示词保持构图一致，只改姿势/进度），在 spec.images 里把该状态写成「图片路径列表」，build_pet.py 自动产出 `frames` 配置，运行时进入该状态即按 fps 连播；其余状态仍可用程序动画。每张帧都是一次 ImageGen 调用，积分成本随帧数上升，建议只对 1~2 个招牌状态（如 wave/happy）做。

### 可视化制作台（方式一，推荐给想自己调的人）

图片（含抠图）就位后，启动 `scripts/maker_server.py` 给用户一个制作页面：

1. 在工作区写 `maker-config.json`：

```json
{
  "workspace": "工作区绝对路径",
  "petName": "预填的宠物名",
  "bubbleText": "你好呀！",
  "restMinutes": 8,
  "images": { "idle": "抠图后PNG绝对路径", "rest": "...", "curious": "...", "happy": "...", "excited": "...", "wave": "..." },
  "interactions": { "rest": [...], "hover": [...], "click": [...], "doubleClick": [...] }
}
```

interactions 预填值按 `references/pet-state-prompts.md` 的推荐映射和台词参考填，用户在页面上可以随便改。

2. 后台启动服务（stdout 第一行是 MAKER_URL）：

```bash
"C:\Users\goodfather02\.workbuddy\binaries\python\versions\3.13.12\python.exe" \
  "C:\Users\goodfather02\.workbuddy\skills\desktop-pet-creator\scripts\maker_server.py" \
  --config "<workspace>\maker-config.json"
```

3. 用 present_files 把 MAKER_URL 给用户（内置浏览器直接打开）。页面上：状态图画廊（透明底）+ 四个触发的「状态+台词」行编辑 + 名字/兜底台词/间隔。
4. 用户点「完成制作」→ 服务端自动：跑 build_pet.py 出 zip → 安装到 `~/DesktopPets/<宠物名>/` → 在桌面生成 `<宠物名>-桌宠.bat`。
5. 告诉用户：**双击桌面的启动程序召唤宠物**（右键宠物或 ESC 关闭）。

### 纯对话制作（方式二）

用户不想开页面时，按最简 spec 写 build-spec.json 跑脚本（加不加 `--install` 都行，加则自动装桌面）：

```json
{
  "petName": "宠物名字",
  "images": { "idle": "图片绝对路径", "happy": "另一张图路径" },
  "width": 150,
  "height": 160,
  "restMinutes": 8,
  "bubble": true,
  "bubbleText": "你好呀！",
  "interactions": {
    "click": [
      { "action": "happy", "text": "自定义台词1" },
      { "action": "wave", "text": "自定义台词2" }
    ]
  }
}
```

- `width`/`height` 省略时脚本按 idle 图比例自动算；只给一个时另一个按比例推。
- `interactions` 省略则用默认互动表；只写要覆盖的触发（rest/hover/click/doubleClick）即可。
- interactions 引用了没有图的状态名时，脚本自动让它复用 idle 的图并给出 warning。
- 所有字段都可省。省略时取值顺序：**spec 显式写的 > `--base` 旧配置里的值 > 默认值**。

用 managed Python 运行（stdout 一行 JSON）：`"ok": true` → zip 已生成在 `<workspace>/<宠物名>-desktop-pet.zip`；`"ok": false` → 按 `error` 修 spec 重跑。

### 更新已有桌宠（--base，两个模式通用）

用户想改之前的桌宠（换图、调大小、加/改台词）而不是从头做时：

```bash
"C:\Users\goodfather02\.workbuddy\binaries\python\versions\3.13.12\python.exe" \
  "C:\Users\goodfather02\.workbuddy\skills\desktop-pet-creator\scripts\build_pet.py" \
  --spec "<workspace>\build-spec.json" --workspace "<workspace>" \
  --base "<旧 config.json / 旧解压目录 / 旧 zip 路径>" --install
```

spec 里**只写要改的字段**（比如只给新的 `images`，或只给新的 `interactions.click`），其余全保留：名字、台词、间隔、宽高、旧图。加 `--install` 会覆盖更新安装目录与桌面启动程序。

## 交付前自检

脚本已自动强制校验以下各项（不通过会拒建并报错）：

- [x] `idle` 状态存在且有图
- [x] `config.json` 是合法 JSON、UTF-8 编码
- [x] `actions` 所有键都有对应文件存在于 `assets/`
- [x] `interactions` 四个触发（rest/hover/click/doubleClick）都至少有一条，且 action 在 actions 中
- [x] zip 根目录平铺（解压后可直接看到 start-pet.bat）

人工只需确认：

- [ ] `warnings` 里没有用户不能接受的内容（比如 webp 无法转换）
- [ ] 预览时换图、气泡、拖拽、右键关闭都正常（若在 Windows 且用户愿意预览）

## 常见问题

- **用户只给一句话没带图**：先答"可以，把图片拖进来就能做"，不要空跑流程。
- **gif 图**：可以收，但 WPF 只显示第一帧静态图，提前告知用户。
- **webp 图**：WPF 原生不支持 webp，能否显示取决于用户系统的 WIC 编解码器。构建环境装有 Pillow 时脚本会自动转 png；否则脚本会照收并在 warnings 里提醒，建议引导用户改用 png/jpg。
- **想改已生成宠物的台词/名字/图**：优先用 `--base` 增量更新——spec 只写新的字段，其余原样保留；也可以让用户解压后直接改 `config.json` 里对应值再重新压缩。
- **脚本运行时报 "ok": false**：stdout 的 JSON 里有 error 字段，按提示修 spec 重跑即可，不要绕过脚本手工组装。
- **用户想"装这个 skill"而不是用**：分发安装不需要用户手动放文件夹。两种方式：(1) 对话代装——让用户把 `desktop-pet-creator.zip`（顶层目录必须是 `desktop-pet-creator/`）拖进 WorkBuddy 对话，说「帮我安装这个桌宠 skill」，AI 自动解压到 `~/.workbuddy/skills/`，新会话即生效；(2) 一键 bat——配合「安装桌宠skill.bat」双击自动解压到 `~/.workbuddy/skills/`。若本机已装过，跳过安装直接说「做个桌宠」。

## Resources

- `scripts/build_pet.py` — 确定性构建脚本：读 build-spec.json → 输出桌宠 zip；`--install` 额外安装到 `~/DesktopPets/<名>/` 并在桌面生成启动 bat；支持 `--base` 增量更新；仅依赖标准库，内建自检
- `scripts/remove_bg.py` — 纯色背景抠图（边缘洪水填充 + 羽化 + 包围盒裁剪）；需 Pillow venv：`C:\Users\goodfather02\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
- `scripts/maker_server.py` — 可视化制作台本地服务：读 maker-config.json 起 localhost 页面，「完成制作」自动构建 + 安装到 ~/DesktopPets/ + 桌面生成启动 bat（安装逻辑复用 build_pet.install_pet_to_desktop）
- `assets/maker-template.html` — 制作台页面模板（由 maker_server.py 填充渲染，不要直接打开）
- `assets/windows-pet-template/` — 桌宠运行时模板（pet-desktop.ps1 + start-pet.bat + 示例 config.json），由脚本原样复制、不要改动 ps1 和 bat
- `references/config-schema.md` — config.json 完整字段规范、窗口尺寸计算规则、默认互动表、命名规则（排障或手工微调已生成包时参考）
- `references/pet-state-prompts.md` — 真实照片派生状态图的 ImageGen 提示词模板、生成纪律（串行/姿势强化/限流）、默认状态组、状态→interactions 推荐映射
