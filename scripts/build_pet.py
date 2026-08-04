#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_pet.py - desktop-pet-creator skill 的确定性构建脚本。

读取一份 build-spec.json，自动完成桌宠包组装：
  1. 校验输入（idle 必存在、图片存在、扩展名受支持）
  2. 状态名规范化（空白->下划线，非法字符->下划线，<=32 字符，唯一）
  3. 复制运行时模板（pet-desktop.ps1 / start-pet.bat，不复制示例 config.json）
  4. 图片复制为 assets/<状态名>.<扩展名>；webp 在装有 Pillow 时自动转 png
  5. 宽高：未指定时按 idle 图实际比例自动计算（长边 150px）
  6. windowWidth/windowHeight 按 pet-desktop.ps1 运行时公式计算
  7. 生成 config.json（UTF-8 无 BOM）与 README.txt（UTF-8, CRLF）
  8. 自检：JSON 合法、actions 键都有图、四触发非空且 action 有效、idle 存在
  9. 打 zip（根目录平铺，不多套文件夹）

支持 --base 增量更新：以旧的 config.json / 解压目录 / 桌宠 zip 为基底，
spec 里没写的字段（名字、台词、休息间隔、气泡、宽高、旧状态图）全部保留。
字段取值顺序：spec 显式指定 > base 旧配置 > 内置默认值。

用法:
  python build_pet.py --spec <build-spec.json> [--workspace <dir>] [--base <旧 config.json|目录|zip>]

stdout 只输出一行 JSON 结果，供调用方解析：
  成功: {"ok": true, "zip": "...", "build_dir": "...", "width": N, "height": N,
         "window": [W, H], "states": [...], "warnings": [...]}
  失败: {"ok": false, "error": "..."}（退出码 1）

仅依赖 Python 标准库；Pillow 为可选增强（webp -> png 转换）。
"""

import argparse
import io
import json
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "windows-pet-template"

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DEFAULT_SIZE = {"width": 120, "height": 140}
AUTO_LONG_SIDE = 150
WIDTH_RANGE = (40, 600)
HEIGHT_RANGE = (40, 700)
MAX_PET_NAME = 40
MAX_BUBBLE_TEXT = 120
REST_MINUTES_RANGE = (1, 240)

DEFAULT_BUBBLE_TEXT = "你好呀！"

DEFAULT_INTERACTIONS = {
    "rest": [
        {"action": "rest", "text": "我休息一下～"},
        {"action": "idle", "text": "发会儿呆。"},
    ],
    "hover": [
        {"action": "wave", "text": "嗨！"},
        {"action": "talk", "text": "找我玩吗？"},
    ],
    "click": [
        {"action": "happy", "text": "嘿嘿！"},
        {"action": "wave", "text": "我在这里！"},
    ],
    "doubleClick": [
        {"action": "talk", "text": "你双击我啦！"},
        {"action": "happy", "text": "今天也要开心！"},
    ],
}

TRIGGERS = ("rest", "hover", "click", "doubleClick")

README_TEMPLATE = """# {pet_name}

使用方式：

1. 解压这个 zip。
2. 双击 start-pet.bat。
3. 鼠标悬停、点击、双击会随机出现你设置的互动；宠物待机时会轻轻呼吸起伏，悬停/点击/双击有歪头、弹跳、跳跃动画；配置了帧序列的状态会连播成一段小动画。
4. 任意交互后先待机 1 分钟，再回到休息；休息状态下每隔设定时间随机切换一次休息图；拖动宠物可以移动位置。
5. 右键宠物窗口或按 ESC 可以关闭。

如果 Windows 提示是否允许运行脚本，请选择允许。
"""


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# 图片尺寸探测（仅标准库，解析文件头）
# ---------------------------------------------------------------------------

def _parse_header(header):
    """从 >=64 字节的文件头解析 PNG/GIF/WebP 尺寸；JPEG 返回 None（需全文件扫描）。"""
    if len(header) < 10:
        return None
    # PNG
    if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
        w, h = struct.unpack(">II", header[16:24])
        return (w, h) if w > 0 and h > 0 else None
    # GIF
    if header[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", header[6:10])
        return (w, h) if w > 0 and h > 0 else None
    # WebP (RIFF)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP" and len(header) >= 30:
        fourcc = header[12:16]
        payload = header[20:30]
        if fourcc == b"VP8X" and len(payload) >= 10:
            return (1 + int.from_bytes(payload[4:7], "little"),
                    1 + int.from_bytes(payload[7:10], "little"))
        if fourcc == b"VP8 " and payload[3:6] == b"\x9d\x01\x2a":
            w = int.from_bytes(payload[6:8], "little") & 0x3FFF
            h = int.from_bytes(payload[8:10], "little") & 0x3FFF
            return (w, h) if w > 0 and h > 0 else None
        if fourcc == b"VP8L" and payload[0:1] == b"\x2f":
            bits = int.from_bytes(payload[1:5], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


def image_size_bytes(data):
    """从完整文件字节解析尺寸；失败返回 None。"""
    if not data or len(data) < 10:
        return None
    header = data[:64]
    if header[:2] == b"\xff\xd8":
        return _jpeg_size(io.BytesIO(data))
    return _parse_header(header)


def image_size(path):
    """从文件路径解析尺寸；失败返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 10:
                return None
            if head[:2] == b"\xff\xd8":
                f.seek(0)
                return _jpeg_size(f)
            return _parse_header(head)
    except OSError:
        return None


def _jpeg_size(f):
    """从类文件对象扫描 JPEG SOF 标记获取尺寸。"""
    sof_markers = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
    standalone = set(range(0xD0, 0xDA)) | {0x01, 0xD8, 0xD9}
    try:
        f.read(2)  # FFD8
        while True:
            byte = f.read(1)
            if not byte:
                return None
            if byte != b"\xff":
                continue
            marker_b = f.read(1)
            if not marker_b:
                return None
            marker = marker_b[0]
            if marker == 0xFF:
                continue
            if marker in standalone:
                continue
            if marker == 0xDA:  # SOS，之后是压缩数据
                return None
            length_b = f.read(2)
            if len(length_b) < 2:
                return None
            seg_len = struct.unpack(">H", length_b)[0]
            if seg_len < 2:
                return None
            if marker in sof_markers:
                data = f.read(5)
                if len(data) < 5:
                    return None
                h, w = struct.unpack(">HH", data[1:5])
                return (w, h) if w > 0 and h > 0 else None
            f.seek(seg_len - 2, 1)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 命名规范化
# ---------------------------------------------------------------------------

def normalize_state_name(name):
    """状态名：trim、空白->_、非法字符->_、<=32 字符。非法则抛 BuildError。"""
    cleaned = re.sub(r"\s+", "_", str(name).strip())
    cleaned = re.sub(r"[^0-9A-Za-z_\-一-鿿]", "_", cleaned)
    cleaned = cleaned[:32]
    if not cleaned:
        raise BuildError("状态名规范化后为空：%r" % (name,))
    return cleaned


def sanitize_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip().strip(".")
    return cleaned or "desktop-pet"


# ---------------------------------------------------------------------------
# base（旧配置）加载
# ---------------------------------------------------------------------------

def _read_config_file(cfg_path):
    try:
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BuildError("base 的 config.json 不是合法 JSON：%s" % exc)
    except OSError as exc:
        raise BuildError("读取 base 的 config.json 失败：%s" % exc)
    if not isinstance(cfg, dict):
        raise BuildError("base 的 config.json 根节点必须是对象")
    return cfg


def load_base(base_arg):
    """加载旧配置。返回 (config: dict, source: ('dir', Path) | ('zip', ZipFile))。"""
    p = Path(str(base_arg)).expanduser()
    if not p.exists():
        raise BuildError("--base 路径不存在：%s" % base_arg)
    if p.is_dir():
        cfg_path = p / "config.json"
        if not cfg_path.is_file():
            raise BuildError("--base 目录里没有 config.json：%s" % p)
        return _read_config_file(cfg_path), ("dir", p.resolve())
    if p.suffix.lower() == ".zip":
        try:
            zf = zipfile.ZipFile(str(p))
        except (zipfile.BadZipFile, OSError):
            raise BuildError("--base 不是合法的 zip：%s" % p)
        try:
            raw = zf.read("config.json")
        except KeyError:
            zf.close()
            raise BuildError("--base zip 根目录没有 config.json：%s" % p)
        try:
            cfg = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            zf.close()
            raise BuildError("--base zip 里的 config.json 不是合法 JSON：%s" % exc)
        if not isinstance(cfg, dict):
            zf.close()
            raise BuildError("--base zip 里的 config.json 根节点必须是对象")
        return cfg, ("zip", zf)
    # 单个 config.json 文件
    return _read_config_file(p), ("dir", p.resolve().parent)


def base_asset_source(base_src, rel_path):
    """从 base 源解析 assets 相对路径。返回 ('path', Path) | ('zip', ZipFile, arcname) | None。"""
    rel_str = str(rel_path).replace("\\", "/")
    if base_src[0] == "dir":
        base_dir = base_src[1]
        candidate = (base_dir / rel_str).resolve()
        # 防目录穿越：必须仍在 base 目录内
        if not str(candidate).startswith(str(base_dir)):
            return None
        if candidate.is_file():
            return ("path", candidate)
        return None
    zf = base_src[1]
    if rel_str in zf.namelist():
        return ("zip", zf, rel_str)
    return None


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------

def clamp(value, lo, hi, label, warnings):
    if value < lo:
        warnings.append("%s %s 小于下限 %s，已调整为 %s" % (label, value, lo, lo))
        return lo
    if value > hi:
        warnings.append("%s %s 大于上限 %s，已调整为 %s" % (label, value, hi, hi))
        return hi
    return value


def source_ext(src):
    """src: ('path', Path) | ('zip', zf, arcname)"""
    if src[0] == "path":
        return src[1].suffix.lower()
    return Path(src[2]).suffix.lower()


def read_source_bytes(src):
    if src[0] == "path":
        try:
            return src[1].read_bytes()
        except OSError:
            return None
    try:
        return src[1].read(src[2])
    except (KeyError, OSError):
        return None


def resolve_pet_size(spec, base_cfg, idle_size_fn, idle_from_spec, warnings):
    """宽高决策，优先级：spec 两个值 > spec 单边+比例推 > base 旧值 > 按 idle 图自动 > 默认 120x140。"""
    sw, sh = spec.get("width"), spec.get("height")
    bw = (base_cfg or {}).get("width")
    bh = (base_cfg or {}).get("height")
    try:
        bw = int(bw) if bw is not None else None
        bh = int(bh) if bh is not None else None
    except (TypeError, ValueError):
        bw = bh = None

    if sw is not None and sh is not None:
        w, h = int(sw), int(sh)
    elif sw is not None or sh is not None:
        size = idle_size_fn()
        if size is not None:
            iw, ih = size
            if sw is not None:
                w, h = int(sw), max(1, round(ih * (int(sw) / iw)))
            else:
                w, h = max(1, round(iw * (int(sh) / ih))), int(sh)
        elif bw and bh:
            if sw is not None:
                w, h = int(sw), max(1, round(bh * (int(sw) / bw)))
            else:
                w, h = max(1, round(bw * (int(sh) / bh))), int(sh)
            warnings.append("无法解析 idle 图片尺寸，按旧配置比例推算另一边为 %sx%s" % (w, h))
        else:
            raise BuildError("只提供了宽或高其中之一时，需要能解析 idle 图片尺寸来推算另一边，但解析失败")
    elif idle_from_spec:
        # 用户给了新 idle 图但没给尺寸：按新图比例自动算（而不是沿用旧尺寸，避免比例不匹配）
        size = idle_size_fn()
        if size is not None:
            iw, ih = size
            scale = AUTO_LONG_SIDE / max(iw, ih)
            w, h = max(1, round(iw * scale)), max(1, round(ih * scale))
            warnings.append("未指定宽高，按 idle 图比例 %sx%s 自动计算为 %sx%s" % (iw, ih, w, h))
        elif bw and bh:
            w, h = bw, bh
            warnings.append("无法解析新 idle 图片尺寸，沿用旧配置宽高 %sx%s" % (w, h))
        else:
            w, h = DEFAULT_SIZE["width"], DEFAULT_SIZE["height"]
            warnings.append("无法解析 idle 图片尺寸，使用默认 %sx%s" % (w, h))
    elif bw and bh:
        w, h = bw, bh
        warnings.append("未指定宽高，沿用旧配置 %sx%s" % (w, h))
    else:
        size = idle_size_fn()
        if size is None:
            w, h = DEFAULT_SIZE["width"], DEFAULT_SIZE["height"]
            warnings.append("无法解析 idle 图片尺寸，使用默认 %sx%s" % (w, h))
        else:
            iw, ih = size
            scale = AUTO_LONG_SIDE / max(iw, ih)
            w, h = max(1, round(iw * scale)), max(1, round(ih * scale))
            warnings.append("未指定宽高，按 idle 图比例 %sx%s 自动计算为 %sx%s" % (iw, ih, w, h))

    w = clamp(w, WIDTH_RANGE[0], WIDTH_RANGE[1], "宽度", warnings)
    h = clamp(h, HEIGHT_RANGE[0], HEIGHT_RANGE[1], "高度", warnings)
    return w, h


def compute_window_size(width, height, bubble_enabled, texts):
    """与 pet-desktop.ps1 运行时公式保持一致。"""
    longest = max([len(t) for t in texts] + [0])
    text_based = min(460, max(180, longest * 18 + 52))
    if bubble_enabled:
        window_width = max(width + 120, text_based + 20)
        window_height = height + 88
    else:
        window_width = width + 40
        window_height = height + 28
    return window_width, window_height


# ---------------------------------------------------------------------------
# interactions 处理（spec > base > 默认，按触发各自继承）
# ---------------------------------------------------------------------------

def _clean_rows(rows, label, warnings):
    if not isinstance(rows, list):
        return []
    valid = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append("%s[%d] 不是对象，已忽略" % (label, i))
            continue
        action = str(row.get("action") or "").strip()
        text = str(row.get("text") or "")
        if not action and not text:
            warnings.append("%s[%d] action 和 text 都为空，已忽略" % (label, i))
            continue
        valid.append({"action": normalize_state_name(action) if action else "", "text": text})
    return valid


def build_interactions(spec, base_cfg, warnings):
    custom = spec.get("interactions")
    if custom is not None and not isinstance(custom, dict):
        raise BuildError("spec.interactions 必须是对象（触发名 -> 互动数组）")
    custom = custom or {}
    base_inter = (base_cfg or {}).get("interactions")
    if not isinstance(base_inter, dict):
        base_inter = {}

    interactions = {}
    inherited = []
    for trigger in TRIGGERS:
        rows = None
        if trigger in custom:
            rows = _clean_rows(custom[trigger], "interactions.%s" % trigger, warnings)
            if not rows:
                warnings.append("spec.interactions.%s 没有有效条目，尝试继承旧配置" % trigger)
        if not rows and trigger in base_inter:
            rows = _clean_rows(base_inter[trigger], "base.interactions.%s" % trigger, warnings)
            if rows:
                inherited.append(trigger)
        if not rows:
            rows = [dict(r) for r in DEFAULT_INTERACTIONS[trigger]]
        interactions[trigger] = rows
    if inherited:
        warnings.append("以下触发的台词/互动沿用旧配置：%s" % "、".join(inherited))

    referenced = set()
    for trigger in TRIGGERS:
        for row in interactions[trigger]:
            if row["action"]:
                referenced.add(row["action"])
    return interactions, referenced


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="构建桌面宠物 zip 包")
    parser.add_argument("--spec", required=True, help="build-spec.json 路径")
    parser.add_argument("--workspace", default=None, help="输出工作区目录（默认取 spec 里的 workspace 或 spec 所在目录）")
    parser.add_argument("--base", default=None, help="旧配置：config.json / 解压目录 / 桌宠 zip，用于增量更新时保留用户设置")
    parser.add_argument("--install", action="store_true",
                        help="构建后自动安装到 ~/DesktopPets/<宠物名>/ 并在桌面生成启动 bat（零门槛对话模式用）")
    args = parser.parse_args()

    base_zf = None
    try:
        result, base_zf = run(args)
        print(json.dumps(result, ensure_ascii=False))
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
    except Exception as exc:  # 兜底，保证 stdout 永远是 JSON
        print(json.dumps({"ok": False, "error": "未预期错误：%s" % exc}, ensure_ascii=False))
        sys.exit(1)
    finally:
        if base_zf is not None:
            try:
                base_zf.close()
            except Exception:
                pass


def run(args):
    warnings = []

    spec_path = Path(args.spec).resolve()
    if not spec_path.is_file():
        raise BuildError("找不到 spec 文件：%s" % spec_path)
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BuildError("spec 不是合法的 UTF-8 JSON：%s" % exc)
    if not isinstance(spec, dict):
        raise BuildError("spec 根节点必须是对象")

    workspace = Path(args.workspace or spec.get("workspace") or spec_path.parent).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # ---- base（旧配置） ----
    base_cfg, base_src = None, None
    base_zf = None
    if args.base:
        base_cfg, base_src = load_base(args.base)
        if base_src[0] == "zip":
            base_zf = base_src[1]
        warnings.append("已加载旧配置作为基底：%s（spec 未覆盖的字段全部保留）" % args.base)

    # ---- 基本信息（spec > base > 默认） ----
    pet_name = str(spec.get("petName") or (base_cfg or {}).get("petName") or "我的桌面宠物").strip() or "我的桌面宠物"
    if len(pet_name) > MAX_PET_NAME:
        warnings.append("宠物名字超过 %d 字符，已截断" % MAX_PET_NAME)
        pet_name = pet_name[:MAX_PET_NAME]

    if "bubble" in spec:
        bubble_enabled = bool(spec["bubble"])
    elif base_cfg is not None and "bubbleEnabled" in base_cfg:
        bubble_enabled = bool(base_cfg["bubbleEnabled"])
    else:
        bubble_enabled = True

    bubble_text = str(spec.get("bubbleText") or (base_cfg or {}).get("bubbleText") or DEFAULT_BUBBLE_TEXT)
    if len(bubble_text) > MAX_BUBBLE_TEXT:
        warnings.append("兜底台词超过 %d 字符，已截断" % MAX_BUBBLE_TEXT)
        bubble_text = bubble_text[:MAX_BUBBLE_TEXT]

    rest_minutes = spec.get("restMinutes")
    if rest_minutes is None and base_cfg is not None:
        try:
            rest_minutes = float(base_cfg["restDelayMs"]) / 60000.0
        except (KeyError, TypeError, ValueError):
            rest_minutes = None
    if rest_minutes is None:
        rest_minutes = 8
    try:
        rest_minutes = float(rest_minutes)
    except (TypeError, ValueError):
        raise BuildError("restMinutes 必须是数字（分钟）")
    if not (REST_MINUTES_RANGE[0] <= rest_minutes <= REST_MINUTES_RANGE[1]):
        clamped = min(max(rest_minutes, REST_MINUTES_RANGE[0]), REST_MINUTES_RANGE[1])
        warnings.append("休息间隔 %s 分钟超出范围，已调整为 %s 分钟" % (rest_minutes, clamped))
        rest_minutes = clamped
    rest_delay_ms = int(rest_minutes * 60000)

    # ---- 模板检查 ----
    ps1 = TEMPLATE_DIR / "pet-desktop.ps1"
    bat = TEMPLATE_DIR / "start-pet.bat"
    for tpl in (ps1, bat):
        if not tpl.is_file():
            raise BuildError("skill 模板缺失：%s" % tpl)

    # ---- 图片与状态：spec.images 优先，base 补齐 ----
    # 单图：state_sources[state] = ('path', Path) | ('zip', zf, arcname)
    # 多帧：state_frames[state] = ([sources...], fps_or_None)
    state_sources = {}
    state_frames = {}
    spec_states = set()

    def _resolve_one(item, state):
        if not isinstance(item, str):
            raise BuildError("状态 %s 的图片路径必须是字符串" % state)
        src = Path(str(item)).expanduser()
        if not src.is_file():
            alt = (spec_path.parent / str(item)).resolve()
            if alt.is_file():
                src = alt
            else:
                raise BuildError("图片不存在：%s（状态 %s）" % (item, state))
        ext = src.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            raise BuildError("不支持的图片格式 %s（状态 %s），仅支持 %s" % (ext, state, "/".join(sorted(SUPPORTED_EXTS))))
        return ("path", src)

    def _resolve_image_value(raw, state):
        """支持：字符串 / 字符串列表 / {frames:[...], fps:N}。返回 (sources 列表, fps)。"""
        fps = None
        if isinstance(raw, dict):
            fr = raw.get("frames")
            if isinstance(fr, list):
                sources = [_resolve_one(x, state) for x in fr]
            else:
                sources = []
            if isinstance(raw.get("fps"), int) and raw["fps"] > 0:
                fps = raw["fps"]
        elif isinstance(raw, list):
            sources = [_resolve_one(x, state) for x in raw]
        else:
            sources = [_resolve_one(raw, state)]
        if not sources:
            raise BuildError("状态 %s 没有任何可用图片" % state)
        return sources, fps

    images = spec.get("images")
    if images is not None and not isinstance(images, dict):
        raise BuildError("spec.images 必须是对象：状态名 -> 图片路径（或图片路径列表）")
    for raw_name, raw_path in (images or {}).items():
        state = normalize_state_name(raw_name)
        if state in state_sources or state in state_frames:
            raise BuildError("状态名规范化后重复：%r 与 %r 都是 %s" % (raw_name, state, state))
        sources, fps = _resolve_image_value(raw_path, state)
        if len(sources) == 1:
            state_sources[state] = sources[0]
        else:
            state_frames[state] = (sources, fps)
        spec_states.add(state)

    if base_cfg is not None and base_src is not None:
        base_actions = base_cfg.get("actions")
        if isinstance(base_actions, dict):
            reused = []
            for raw_state, rel in base_actions.items():
                try:
                    state = normalize_state_name(raw_state)
                except BuildError:
                    continue
                if state in state_sources or state in state_frames:
                    continue
                src = base_asset_source(base_src, rel)
                if src is None:
                    warnings.append("旧配置里状态 %s 的图片缺失（%s），已忽略" % (state, rel))
                    continue
                if source_ext(src) not in SUPPORTED_EXTS:
                    warnings.append("旧配置里状态 %s 的图片格式不支持（%s），已忽略" % (state, rel))
                    continue
                state_sources[state] = src
                reused.append(state)
            if reused:
                warnings.append("以下状态沿用旧图片：%s" % "、".join(sorted(reused)))

        # 旧配置里若某状态是帧序列，也一并继承
        base_frames = base_cfg.get("frames")
        if isinstance(base_frames, dict):
            for raw_state, fval in base_frames.items():
                try:
                    state = normalize_state_name(raw_state)
                except BuildError:
                    continue
                if state in state_sources or state in state_frames:
                    continue
                if not isinstance(fval, dict):
                    continue
                ffiles = fval.get("files")
                if not isinstance(ffiles, list) or not ffiles:
                    continue
                fps = fval.get("fps")
                fps = fps if (isinstance(fps, int) and fps > 0) else None
                sources = []
                for fr in ffiles:
                    s = base_asset_source(base_src, fr)
                    if s is None:
                        continue
                    if source_ext(s) not in SUPPORTED_EXTS:
                        continue
                    sources.append(s)
                if len(sources) > 1:
                    state_frames[state] = (sources, fps)
                    warnings.append("状态 %s 沿用旧帧序列（%d 帧）" % (state, len(sources)))
                elif len(sources) == 1:
                    state_sources[state] = sources[0]
                    warnings.append("状态 %s 沿用旧图片" % state)

    if not state_sources and not state_frames:
        raise BuildError("没有任何可用的状态图片：spec.images 为空%s" % ("，且 --base 里也没有可复用的图" if base_cfg else ""))
    if "idle" not in state_sources and "idle" not in state_frames:
        raise BuildError("缺少必需的 idle 状态图片（spec.images 和 --base 里都没有）")

    def _out_ext(src):
        ext = source_ext(src)
        return ".png" if (ext == ".webp" and pillow_available()) else ext

    idle_is_frames = "idle" in state_frames
    if idle_is_frames:
        idle_first_ext = _out_ext(state_frames["idle"][0][0])
    else:
        idle_first_ext = _out_ext(state_sources["idle"])

    # ---- interactions（spec > base > 默认） ----
    interactions, referenced_states = build_interactions(spec, base_cfg, warnings)

    spec_fps = spec.get("fps")

    def resolve_fps(per_state_fps):
        if isinstance(per_state_fps, int) and per_state_fps > 0:
            return per_state_fps
        if isinstance(spec_fps, int) and spec_fps > 0:
            return spec_fps
        return 8

    # 规划输出文件名：单图 -> <state>.<ext>；多帧 -> <state>_1.<ext>, <state>_2.<ext> ...
    planned = {}      # state -> [output filenames]
    for state, src in state_sources.items():
        planned[state] = ["%s%s" % (state, _out_ext(src))]
    for state, (sources, fps) in state_frames.items():
        names = []
        for i, src in enumerate(sources, 1):
            names.append("%s_%d%s" % (state, i, _out_ext(src)))
        planned[state] = names

    actions = {}
    for state in state_sources:
        actions[state] = "assets/%s" % planned[state][0]
    for state in state_frames:
        actions[state] = "assets/%s" % planned[state][0]

    # 帧序列配置（仅多帧状态写入 config.frames）
    frames_cfg = {}
    for state, (sources, fps) in state_frames.items():
        frames_cfg[state] = {
            "files": ["assets/%s" % n for n in planned[state]],
            "fps": resolve_fps(fps),
        }

    # 被引用但没图的状态：回退复用 idle（含帧序列）
    for state in sorted(referenced_states):
        if state not in actions:
            if idle_is_frames:
                actions[state] = actions["idle"]
                frames_cfg[state] = {
                    "files": ["assets/%s" % n for n in planned["idle"]],
                    "fps": frames_cfg["idle"]["fps"],
                }
                warnings.append("状态 %s 没有单独图片，已复用 idle 的图与帧序列" % state)
            else:
                actions[state] = "assets/idle%s" % idle_first_ext
                warnings.append("状态 %s 没有单独图片，已复用 idle 的图" % state)

    # 统一源列表（state -> 有序 sources 列表），驱动写盘
    all_sources = {}
    for state, src in state_sources.items():
        all_sources[state] = [src]
    for state, (sources, fps) in state_frames.items():
        all_sources[state] = list(sources)

    # ---- 宽高与窗口尺寸 ----
    idle_size_cache = {}

    def idle_size_fn():
        if "size" not in idle_size_cache:
            src = state_frames["idle"][0][0] if idle_is_frames else state_sources["idle"]
            if src[0] == "path":
                idle_size_cache["size"] = image_size(src[1])
            else:
                data = read_source_bytes(src)
                idle_size_cache["size"] = image_size_bytes(data) if data else None
        return idle_size_cache["size"]

    width, height = resolve_pet_size(spec, base_cfg, idle_size_fn, idle_from_spec=("idle" in spec_states), warnings=warnings)
    all_texts = [bubble_text] + [row["text"] for t in TRIGGERS for row in interactions[t]]
    window_width, window_height = compute_window_size(width, height, bubble_enabled, all_texts)

    # ---- 组装构建目录 ----
    # 注意：不用 shutil.rmtree 重建目录——沙箱环境会拦截目录级删除（SAFE_DELETE_FAIL_CLOSED）。
    # 改为覆盖写入已知文件 + 逐文件清理 assets 里的遗留文件；zip 只收本次实际写入的文件。
    # --base 可能就是当前构建目录（同工作区重建），清理时要保护本次构建的源文件和输出文件。
    build_dir = workspace / "desktop-pet-build" / "desktop-pet"
    assets_dir = build_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    keep_names = set()
    for state, sources in all_sources.items():
        for name in planned[state]:
            keep_names.add(name)
        for src in sources:
            if src[0] == "path":
                try:
                    if src[1].resolve().parent == assets_dir.resolve():
                        keep_names.add(src[1].name)  # 源文件就在 assets 里，不能当遗留清掉
                except OSError:
                    pass
    for stale in assets_dir.iterdir():
        if stale.is_file() and stale.name not in keep_names:
            try:
                stale.unlink()
            except OSError:
                pass  # 删不掉就留下，zip 只收本次引用的文件，不影响产物

    shutil.copy2(str(ps1), str(build_dir / "pet-desktop.ps1"))
    shutil.copy2(str(bat), str(build_dir / "start-pet.bat"))

    written_assets = []
    for state, sources in all_sources.items():
        for src, out_name in zip(sources, planned[state]):
            out = assets_dir / out_name
            ext = source_ext(src)
            same_file = False
            if src[0] == "path":
                try:
                    same_file = src[1].resolve() == out.resolve()
                except OSError:
                    same_file = False
            if same_file:
                # 就地复用（--base 为当前构建目录时），文件已在，无需复制
                written_assets.append(out.name)
            elif ext == ".webp" and pillow_available():
                from PIL import Image
                data = read_source_bytes(src)
                if data is None:
                    raise BuildError("读取状态 %s 的图片失败" % state)
                with Image.open(io.BytesIO(data)) as im:
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGBA")
                    im.save(str(out), "PNG")
                warnings.append("webp 图片（状态 %s）已自动转换为 png（WPF 对 webp 支持依赖系统编解码器）" % state)
                written_assets.append(out.name)
            elif ext == ".webp":
                data = read_source_bytes(src)
                if data is None:
                    raise BuildError("读取状态 %s 的图片失败" % state)
                out.write_bytes(data)
                warnings.append(
                    "状态 %s 的图片是 webp：部分 Windows 无法显示（依赖系统 WIC 编解码器）。"
                    "建议改用 png/jpg，或安装 Pillow 后重跑本脚本自动转 png" % state
                )
                written_assets.append(out.name)
            else:
                if src[0] == "path":
                    shutil.copy2(str(src[1]), str(out))
                else:
                    data = read_source_bytes(src)
                    if data is None:
                        raise BuildError("读取状态 %s 的图片失败" % state)
                    out.write_bytes(data)
                written_assets.append(out.name)

    config = {
        "petName": pet_name,
        "width": width,
        "height": height,
        "windowWidth": window_width,
        "windowHeight": window_height,
        "defaultAction": "idle",
        "hoverAction": interactions["hover"][0]["action"] or "idle",
        "clickAction": interactions["click"][0]["action"] or "idle",
        "doubleClickAction": interactions["doubleClick"][0]["action"] or "idle",
        "restAction": interactions["rest"][0]["action"] or "idle",
        "bubbleEnabled": bubble_enabled,
        "bubbleText": bubble_text,
        "bubblePosition": "top",
        "restDelayMs": rest_delay_ms,
        "actionDurationMs": 1100,
        "bubbleDisplayMs": 3000,
        "alwaysOnTop": True,
        "draggable": True,
        "actions": actions,
        "interactions": interactions,
        "idleAfterInteractionMs": 60000,
    }
    if frames_cfg:
        config["frames"] = frames_cfg
    config_text = json.dumps(config, ensure_ascii=False, indent=2)
    (build_dir / "config.json").write_text(config_text, encoding="utf-8")

    readme = README_TEMPLATE.format(pet_name=pet_name).replace("\n", "\r\n")
    (build_dir / "README.txt").write_text(readme, encoding="utf-8")

    # ---- 自检（不过则拒建） ----
    self_check(build_dir, config)

    # ---- 打 zip（根目录平铺；"w" 模式自动截断旧 zip，无需先删） ----
    zip_name = "%s-desktop-pet.zip" % sanitize_filename(pet_name)
    zip_path = workspace / zip_name
    members = ["config.json", "pet-desktop.ps1", "start-pet.bat", "README.txt"]
    members += ["assets/%s" % name for name in sorted(set(written_assets))]
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in members:
            zf.write(str(build_dir / rel), rel)

    install_dir = None
    launcher = None
    if args.install:
        try:
            install_dir, launcher = install_pet_to_desktop(build_dir, pet_name)
        except Exception as exc:  # 安装失败不应让构建成果作废
            warnings.append("已构建成功，但自动安装到桌面失败：%s（可手动解压 zip 并双击里面的 start-pet.bat）" % exc)

    return {
        "ok": True,
        "zip": str(zip_path),
        "build_dir": str(build_dir),
        "installDir": str(install_dir) if install_dir else None,
        "launcher": str(launcher) if launcher else None,
        "launcherName": launcher.name if launcher else None,
        "width": width,
        "height": height,
        "window": [window_width, window_height],
        "states": sorted(actions.keys()),
        "warnings": warnings,
    }, base_zf


def install_pet_to_desktop(build_dir, pet_name):
    """安装到 ~/DesktopPets/<宠物名>/ 并在桌面生成启动 bat。返回 (install_dir, launcher)。

    单一安装入口：零门槛对话模式（build_pet.py --install）与可视化制作台都走这里，
    避免两份安装逻辑漂移。
    """
    build_dir = Path(build_dir)
    install_dir = Path.home() / "DesktopPets" / pet_name
    (install_dir / "assets").mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "pet-desktop.ps1", "start-pet.bat", "README.txt"):
        src = build_dir / name
        if src.is_file():
            shutil.copy2(str(src), str(install_dir / name))
    assets_src = build_dir / "assets"
    if assets_src.is_dir():
        for img in assets_src.iterdir():
            if img.is_file():
                shutil.copy2(str(img), str(install_dir / "assets" / img.name))

    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        raise BuildError("找不到桌面目录：%s" % desktop)
    launcher = desktop / ("%s-桌宠.bat" % sanitize_filename(pet_name))
    content = (
        "@echo off\r\n"
        "cd /d \"{dir}\"\r\n"
        "start \"\" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{dir}\\pet-desktop.ps1\"\r\n"
    ).format(dir=str(install_dir))
    launcher.write_bytes(content.encode("mbcs", errors="replace"))
    return install_dir, launcher


def pillow_available():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def self_check(build_dir, config):
    problems = []
    try:
        json.loads((build_dir / "config.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append("config.json 不是合法 JSON：%s" % exc)

    if "idle" not in config["actions"]:
        problems.append("actions 缺少 idle 键")

    for state, rel in config["actions"].items():
        if not (build_dir / rel).is_file():
            problems.append("状态 %s 的图片文件不存在：%s" % (state, rel))

    for state, fval in (config.get("frames") or {}).items():
        for fr in (fval.get("files") or []):
            if not (build_dir / fr).is_file():
                problems.append("状态 %s 的帧图片文件不存在：%s" % (state, fr))

    for trigger in TRIGGERS:
        rows = config["interactions"].get(trigger)
        if not rows:
            problems.append("interactions.%s 为空" % trigger)
            continue
        for row in rows:
            if row["action"] and row["action"] not in config["actions"]:
                problems.append("interactions.%s 引用了不存在的状态：%s" % (trigger, row["action"]))

    if problems:
        raise BuildError("自检未通过：\n- " + "\n- ".join(problems))


if __name__ == "__main__":
    main()
