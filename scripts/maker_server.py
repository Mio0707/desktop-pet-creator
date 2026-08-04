#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maker_server.py - desktop-pet-creator skill 的可视化制作台本地服务。

在 localhost 起一个 HTTP 服务，渲染制作台页面（状态图 + 交互/台词编辑），
用户点「完成制作」后：
  1. 把页面提交的配置 + 服务端持有的图片路径拼成 build-spec.json
  2. 调 build_pet.py 构建桌宠 zip（stdout JSON）
  3. 把构建产物安装到 ~/DesktopPets/<宠物名>/
  4. 在桌面生成启动程序 <宠物名>-桌宠.bat（GBK/mbcs 编码，中文路径不乱码）
  5. 页面展示结果；用户双击桌面启动程序召唤宠物

用法:
  python maker_server.py --config <maker-config.json> [--port 18923]

maker-config.json 格式:
  {
    "workspace": "构建输出目录",
    "petName": "奶牛",
    "bubbleText": "你好呀！",
    "restMinutes": 8,
    "images": { "idle": "绝对路径.png", "rest": "...", ... },
    "interactions": { "rest": [{"action":"rest","text":"..."}], "hover": [...],
                      "click": [...], "doubleClick": [...] }
  }

启动后 stdout 第一行打印 MAKER_URL=<url>，之后进入服务循环（后台常驻）。
仅绑定 127.0.0.1；POST /build 需要 token 校验。
"""

import argparse
import json
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

SCRIPT_DIR = Path(__file__).resolve().parent
BUILD_SCRIPT = SCRIPT_DIR / "build_pet.py"
TEMPLATE_HTML = SCRIPT_DIR.parent / "assets" / "maker-template.html"

sys.path.insert(0, str(SCRIPT_DIR))
from build_pet import sanitize_filename, install_pet_to_desktop  # noqa: E402


def load_config(path):
    cfg_path = Path(path).resolve()
    if not cfg_path.is_file():
        raise SystemExit("maker-config 不存在：%s" % path)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for key in ("workspace", "petName", "images", "interactions"):
        if key not in cfg:
            raise SystemExit("maker-config 缺少字段：%s" % key)
    if "idle" not in cfg["images"]:
        raise SystemExit("maker-config.images 必须包含 idle")
    for state, p in cfg["images"].items():
        paths = p if isinstance(p, list) else [p]
        for pp in paths:
            if not Path(pp).is_file():
                raise SystemExit("图片不存在：%s（状态 %s）" % (pp, state))
    return cfg


def render_page(cfg, token):
    html = TEMPLATE_HTML.read_text(encoding="utf-8")
    states = []
    for s, p in cfg["images"].items():
        paths = p if isinstance(p, list) else [p]
        frames = ["/img/%s/%d" % (s, i) for i in range(len(paths))]
        states.append({"name": s, "frames": frames})
    defaults = {
        "petName": cfg.get("petName", "我的桌面宠物"),
        "bubbleText": cfg.get("bubbleText", "你好呀！"),
        "restMinutes": cfg.get("restMinutes", 8),
        "interactions": cfg["interactions"],
    }
    html = html.replace("__STATES_JSON__", json.dumps(states, ensure_ascii=False))
    html = html.replace("__DEFAULTS_JSON__", json.dumps(defaults, ensure_ascii=False))
    html = html.replace("__TOKEN__", token)
    return html.encode("utf-8")


class MakerHandler(BaseHTTPRequestHandler):
    server_version = "PetMaker/1.0"

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send(200, self.server.page_html)
            return
        if path.startswith("/img/"):
            parts = path[len("/img/"):].split("/")
            state = parts[0]
            imgs = self.server.images.get(state)
            if imgs:
                try:
                    idx = int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    idx = 0
                if 0 <= idx < len(imgs):
                    img = imgs[idx]
                    if Path(img).is_file():
                        self._send(200, Path(img).read_bytes(), "image/png")
                        return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/build":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        token = parse_qs(parsed.query).get("token", [""])[0]
        if token != self.server.token:
            self._json(403, {"ok": False, "error": "token 无效"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.server.build(body)
            self._json(200, result)
        except Exception as exc:
            self._json(200, {"ok": False, "error": str(exc)})


def run_build(server, page_payload):
    """页面提交 → build-spec.json → build_pet.py → 安装 + 桌面启动程序。"""
    cfg = server.cfg
    ws = Path(cfg["workspace"]).resolve()
    pet_name = str(page_payload.get("petName") or cfg["petName"]).strip() or cfg["petName"]

    spec = {
        "petName": pet_name,
        "images": cfg["images"],
        "bubble": True,
        "bubbleText": str(page_payload.get("bubbleText") or cfg.get("bubbleText") or "你好呀！"),
        "restMinutes": page_payload.get("restMinutes", cfg.get("restMinutes", 8)),
        "interactions": page_payload.get("interactions") or cfg["interactions"],
    }
    spec_path = ws / "build-spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--spec", str(spec_path), "--workspace", str(ws)],
        capture_output=True, text=True, encoding="utf-8",
    )
    try:
        result = json.loads(proc.stdout.strip())
    except ValueError:
        return {"ok": False, "error": "构建脚本输出异常：%s%s" % (proc.stdout[:300], proc.stderr[:300])}
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "构建失败")}

    install_dir, launcher = install_pet_to_desktop(result["build_dir"], pet_name)

    return {
        "ok": True,
        "zip": result["zip"],
        "installDir": str(install_dir),
        "launcher": str(launcher),
        "launcherName": launcher.name,
        "warnings": result.get("warnings", []),
    }


class MakerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, cfg, token):
        super().__init__(addr, MakerHandler)
        self.cfg = cfg
        self.token = token
        self.images = {}
        for s, p in cfg["images"].items():
            paths = p if isinstance(p, list) else [p]
            self.images[s] = [str(x) for x in paths]
        self.page_html = render_page(cfg, token)
        self._build_lock = threading.Lock()

    def build(self, payload):
        with self._build_lock:
            return run_build(self, payload)


def main():
    parser = argparse.ArgumentParser(description="桌宠可视化制作台")
    parser.add_argument("--config", required=True, help="maker-config.json 路径")
    parser.add_argument("--port", type=int, default=18923)
    args = parser.parse_args()

    cfg = load_config(args.config)
    token = secrets.token_hex(8)

    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = MakerServer(("127.0.0.1", port), cfg, token)
            break
        except OSError:
            continue
    if server is None:
        print("MAKER_ERROR=没有可用端口", flush=True)
        sys.exit(1)

    print("MAKER_URL=http://127.0.0.1:%d/" % server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
