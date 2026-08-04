---
name: desktop-pet-creator
description: 把用户提供的图片制作成 Windows 桌面宠物（桌宠）——桌面上一个无边框透明、可拖拽、会随机换图和弹台词气泡的小宠物。默认流程：用户上传一张宠物照片 → AI 派生多个状态图并自动抠图 → 可视化制作台配互动台词 → 一键生成桌面启动程序。当用户想做桌面宠物、桌宠、电子宠物，或想把某张图片/表情包/自设角色变成桌面上会动会说话的小伙伴时使用。产出是可直接运行的宠物包（zip + 桌面启动器），用户机器上无需安装 Node.js 或任何开发环境。
agent_created: true
---

# Desktop Pet Creator 桌面宠物生成器

## Overview

根据用户上传的图片生成可直接运行的 Windows 桌面宠物，并自动安装到用户桌面。宠物基于 PowerShell + WPF（运行时模板 `assets/windows-pet-template/`），支持四种交互（休息/悬停/点击/双击）、随机台词气泡、拖拽移动、程序动画（待机呼吸、悬停歪头、单击弹跳、双击跳跃），右键或 ESC 关闭。

默认工作流：**用户给一张真实照片 → AI 派生多状态图（ImageGen，先报积分）→ 自动抠图 → 可视化制作台配台词 → 一键完成并装到桌面 → 双击启动**。

组装由 `scripts/build_pet.py` 确定性完成（复制模板、图片改名、宽高与窗口尺寸计算、生成 config.json、自检、打包；加 `--install` 时额外安装到桌面）。**不要手工拼 config.json 或手工打包**——编码、尺寸公式、zip 目录层级都是易错点，脚本已内建校验，出错会拒建并说明原因。

核心原则：**用户只出照片和台词，其余全部代办**。不问技术问题，配置项全部提供默认值，用户说"都行"就直接用默认。

## 工作流

先判断**新建还是更新**：用户是"做一个新桌宠"，还是"改之前那个桌宠"（换图/改台词/调大小）？更新时走第 3 步备选的 `--base` 增量构建——用户已有的自定义设置（台词、名字、休息间隔、旧状态图）全部保留，不要从零重做。

### 第 1 步：收集图片和派生状态图（默认：AI 多状态）

用户给至少 1 张照片。默认用 `references/pet-state-prompts.md` 的提示词模板做 ImageGen 图生图，把真实照片派生为多个状态图：

- 默认状态组 idle/rest/curious/happy/excited/wave；预算有限可只做 idle/rest/happy/wave 4 张基础款。
- **先报生成张数和预估积分（每张约 5-10），用户同意后再生成**；先出 idle 与用户确认风格和相似度，满意后再批量出其余状态。
- 逐张过目，身份漂移大或构图不统一的单张重试，不要不确认就整批用掉；生成图带背景时用 `scripts/remove_bg.py` 抠图（纯色背景免费抠）或 ImageGen 直接出透明版。
- **用户明确只要一张原图（不派生）**：尊重用户，直接把原图作为 idle，其余状态复用同一张图（build_pet.py 自动处理，宠物照样能跑能说话）。
- 状态名规则：唯一、≤32 字符、空白替换为 `_`；`idle` 必须存在。图片支持 png/jpg/webp/gif。
- **一个状态想要连播动画（GIF 效果）**：对该状态生成 2~4 张连续画面（提示词保持构图一致，只改姿势/进度，如挥手的第 1/2/3 帧），在 spec.images 里把该状态写成「图片路径列表」，build_pet.py 自动产出 `frames` 配置，运行时进入该状态即按 fps 连播；其余状态仍可用程序动画（呼吸/弹跳）。每张帧都是一次 ImageGen 调用，积分成本随帧数上升，建议只对 1~2 个招牌状态（如 wave/happy）做帧序列。

找图技巧：用 Glob 按 `**/*.png`、`**/*.jpg`、`**/*.jpeg`、`**/*.webp`、`**/*.gif` 在工作区搜索，按修改时间排序，最近拖入的图排在最前；找不到就明确问用户图片路径。

### 第 2 步：确认配置（给默认值，少打扰）

用 AskUserQuestion 一次性向用户确认，把"全部默认（推荐）"放第一个选项，用户也可以回复"默认"跳过：

| 配置 | 默认 |
|------|------|
| 宠物名字 | 我的桌面宠物 |
| 宽×高 | 脚本按 idle 图实际比例自动计算（长边 150px），用户指定才覆盖 |
| 休息换图间隔 | 8 分钟 |
| 气泡 | 开启，兜底台词"你好呀！" |
| 四种交互的随机台词 | 用 `references/config-schema.md` 里的默认互动表 |

用户有自定义台词（比如"点击时随机说 xx 或 yy"）就写进 spec 的 interactions，只需给出要覆盖的触发，没给的触发自动用默认。

### 第 3 步：制作（方式一：可视化制作台，默认）

图片（含抠图）全部就位后，启动 `scripts/maker_server.py` 给用户一个可视化制作页面：

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
4. 用户点「完成制作」→ 服务端自动：跑 build_pet.py 出 zip → 安装到 `~/DesktopPets/<宠物名>/` → 在桌面生成 `<宠物名>-桌宠.bat`（GBK/mbcs 编码，中文路径不乱码）。
5. 告诉用户：**双击桌面的启动程序召唤宠物**（右键宠物或 ESC 关闭）。不要试图代用户启动——沙箱内启动的 GUI 进程窗口到不了用户桌面（实测 Bash 后台 / PowerShell Start-Process 均被拦或不可见），让用户自己双击是唯一可靠路径。

### 第 3 步备选：纯对话制作（方式二）

用户不想开页面时，按第 2 步的对话确认收集配置，然后写 build-spec.json 跑脚本：

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
- 所有字段都可省。省略时的取值顺序：**spec 显式写的 > `--base` 旧配置里的值 > 默认值**。
- 加 `--install` 会额外完成：构建 zip → 安装到 `~/DesktopPets/<宠物名>/` → 在桌面生成 `<宠物名>-桌宠.bat`（对话全自动出宠时推荐加）。

用 managed Python 运行（stdout 是一行 JSON 结果）：

```bash
"C:\Users\goodfather02\.workbuddy\binaries\python\versions\3.13.12\python.exe" \
  "C:\Users\goodfather02\.workbuddy\skills\desktop-pet-creator\scripts\build_pet.py" \
  --spec "<workspace>\build-spec.json" --workspace "<workspace>" --install
```

- 输出 `"ok": true` → zip 已生成在 `<workspace>/<宠物名>-desktop-pet.zip`；`installDir`/`launcher`/`launcherName` 给出安装路径与桌面启动器。检查 `warnings` 数组，把值得用户知道的提醒（如自动算的宽高、webp 回退）转告用户。
- 输出 `"ok": false` → 按 `error` 修 spec 重跑。常见原因：图片路径错、缺 idle、状态名重名、扩展名不支持。

#### 更新已有桌宠（--base，保留用户设置）

用户想改之前的桌宠（换图、调大小、加/改台词）而不是从头做时：

1. 找旧包：若是本 skill 之前生成的，先看 `<workspace>/desktop-pet-build/desktop-pet/config.json` 是否还在；否则请用户提供之前的 zip 或解压目录。
2. spec 里**只写要改的字段**（比如只给新的 `images`，或只给新的 `interactions.click`）。
3. 运行命令追加 `--base`（同样可加 `--install` 覆盖更新安装目录与桌面启动程序）：

```bash
"C:\Users\goodfather02\.workbuddy\binaries\python\versions\3.13.12\python.exe" \
  "C:\Users\goodfather02\.workbuddy\skills\desktop-pet-creator\scripts\build_pet.py" \
  --spec "<workspace>\build-spec.json" --workspace "<workspace>" \
  --base "<旧 config.json / 旧解压目录 / 旧 zip 路径>" --install
```

4. 没写的字段全部保留旧值：宠物名字、四种交互台词、休息间隔、气泡开关与兜底台词、宽高；`spec.images` 没给新图的状态继续沿用旧图（旧 zip 里的图也能直接复用，无需用户重新提供）。

### 第 4 步：交付与验收

- 用 present_files 把 zip 呈现给用户，并简要说明：解压 → 双击 start-pet.bat → 右键/ESC 关闭（若走了 `--install`，桌面已有启动文件，直接双击即可）。
- 主动提醒：宠物只能在 Windows 上运行；若 Windows 拦截脚本提示，选"允许"。
- 如果当前就是 Windows 且用户想立即看效果，征得同意后可以直接后台运行构建目录（JSON 结果里的 `build_dir`）里的 `start-pet.bat` 让用户预览，提示用户右键宠物即可关闭。

### 交付前自检

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
- **用户只想用一张原图、不派生多状态**：尊重用户，原图作 idle，其余状态复用（脚本自动处理，宠物同样能跑能说话）；想省积分也可以这么干。
- **gif 图**：可以收，但 WPF 只显示第一帧静态图，提前告知用户。
- **webp 图**：WPF 原生不支持 webp，能否显示取决于用户系统的 WIC 编解码器。构建环境装有 Pillow 时脚本会自动转 png；否则脚本会照收并在 warnings 里提醒，建议引导用户改用 png/jpg。
- **想改已生成宠物的台词/名字/图**：优先用 `--base` 增量更新——spec 只写新的字段，其余原样保留；也可以让用户解压后直接改 `config.json` 里对应值再重新压缩。
- **脚本运行时报 "ok": false**：stdout 的 JSON 里有 error 字段，按提示修 spec 重跑即可，不要绕过脚本手工组装。
- **用户想"装这个 skill"而不是用**：分发安装不需要用户手动放文件夹，三种方式：(1) 对话代装——把 `desktop-pet-creator.zip`（顶层目录 `desktop-pet-creator/`）拖进 WorkBuddy 对话说「帮我安装这个桌宠 skill」，AI 自动解压到 `~/.workbuddy/skills/`；(2) 一键 bat——「安装桌宠skill.bat」双击自动安装；(3) **从 git 下载安装（免拖文件，推荐分发）**——本 skill 公开仓库 `https://github.com/Mio0707/desktop-pet-creator`，zip 直链（`/releases/latest/` 始终指向最新版）：`https://github.com/Mio0707/desktop-pet-creator/releases/latest/download/desktop-pet-creator.zip`。把这条指令给用户即可：**「帮我从这个链接安装桌宠 skill：<直链>」**——AI 自动下载 → 解压到 `~/.workbuddy/skills/` → 安装完成（也可 `git clone https://github.com/Mio0707/desktop-pet-creator.git` 到 `~/.workbuddy/skills/`）。若本机已装过，跳过安装直接说「做个桌宠」。

## Resources

- `scripts/build_pet.py` — 确定性构建脚本：读 build-spec.json → 输出桌宠 zip；`--install` 额外安装到 `~/DesktopPets/<名>/` 并在桌面生成启动 bat；支持 `--base` 增量更新；仅依赖标准库，内建自检
- `scripts/remove_bg.py` — 纯色背景抠图（边缘洪水填充 + 羽化 + 包围盒裁剪）；需 Pillow venv：`C:\Users\goodfather02\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
- `scripts/maker_server.py` — 可视化制作台本地服务：读 maker-config.json 起 localhost 页面，「完成制作」自动构建 + 安装到 ~/DesktopPets/ + 桌面生成启动 bat（安装逻辑复用 build_pet.install_pet_to_desktop）
- `assets/maker-template.html` — 制作台页面模板（由 maker_server.py 填充渲染，不要直接打开）
- `assets/windows-pet-template/` — 桌宠运行时模板（pet-desktop.ps1 + start-pet.bat + 示例 config.json），由脚本原样复制、不要改动 ps1 和 bat
- `references/config-schema.md` — config.json 完整字段规范、窗口尺寸计算规则、默认互动表、命名规则（排障或手工微调已生成包时参考）
- `references/pet-state-prompts.md` — 真实照片派生状态图的 ImageGen 提示词模板、生成纪律（串行/姿势强化/限流）、默认状态组、状态→interactions 推荐映射
