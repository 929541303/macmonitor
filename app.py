#!/usr/bin/env python3
"""MacMonitor 单进程版：菜单栏 + 监控服务 + 面板窗口 三合一（同 Macs Fan Control）"""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import rumps
from AppKit import NSAlert, NSApp
from Foundation import NSObject

import server  # 后端：进程/CPU/GPU/网络/风扇 + 内嵌 HTTP(127.0.0.1:8765)
from panel import panel, start_qr_refresher  # 原生 AppKit 面板（替代 WKWebView）

VERSION = "1.3.1"
CHANGELOG = """v1.3.1  Memory: 64→52MB footprint; web UI English
v1.3.0  English UI, global release
v1.2.5  瘦身（安装包 40→23MB）；降频优化 CPU 3%
v1.2.4  更新流程修复：后台线程检查、原生提示框（无多余输入框）
v1.2.3  安装引导（DMG 直接运行时提示装入应用程序）；更新检查重试
v1.2.2  原生面板（弃 WKWebView）；CPU 稳定 <5%
v1.2.0  单进程架构（面板/菜单栏/服务合一），图标与菜单栏 🌀 同款
v1.1.0  棒棒糖图标；About 完善（简介/版本/更新）
v1.0.0  首版：进程/GPU/网络监控、排序与结束进程；风扇自动/恒定/传感器联动；菜单栏常驻指标"""
ICON = str(Path(__file__).resolve().parent / "icon.icns")

_panel = {}  # win/web/委托 全局引用，防 GC


def open_panel():
    """显示监控面板窗口（原生 AppKit，首次创建后复用）"""
    panel.build()


RELEASE_API = "https://api.github.com/repos/929541303/macmonitor/releases/latest"
_notified_tag = None


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
    except ValueError:
        return (0,)


def _alert(title, msg, ok="OK", cancel=None):
    """原生提示框（无输入框）"""
    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(msg)
    a.addButtonWithTitle_(ok)
    if cancel:
        a.addButtonWithTitle_(cancel)
    return a.runModal() == 1000  # 第一个按钮


class UpdateAsker(NSObject):
    def ask_(self, info):
        tag, url = info
        if _alert("New Version Available",
                  f"MacMonitor v{tag.lstrip('v')} is out.\n\nDownload from GitHub now?",
                  ok="Download", cancel="Later"):
            subprocess.Popen(["open", url])

    def result_(self, info):
        """检查完成（主线程）：按结果弹窗（原生，无输入框）"""
        rel, force = info[0], bool(info[1])
        if rel is None:
            if force:
                _alert("Check for Updates", "Cannot reach GitHub (tried proxy & direct).\nPlease try again later.")
            return
        tag = rel.get("tag_name") or ""
        url = rel.get("html_url") or "https://github.com/929541303/macmonitor/releases"
        dmg = next((a["browser_download_url"] for a in rel.get("assets", [])
                    if a["name"].lower().endswith(".dmg")), url)
        global _notified_tag
        if _ver_tuple(tag) > _ver_tuple(VERSION):
            if _notified_tag == tag and not force:
                return
            _notified_tag = tag
            self.ask_((tag, dmg))
        elif force:
            _alert("Check for Updates", f"You are up to date (v{VERSION}).")


def _fetch_release():
    """代理/直连交替重试 4 次，抗网络抖动"""
    openers = [
        urllib.request.build_opener(),  # 系统代理
        urllib.request.build_opener(urllib.request.ProxyHandler({})),  # 直连兜底
    ]
    for attempt in range(4):
        try:
            req = urllib.request.Request(RELEASE_API, headers={"User-Agent": "MacMonitor"})
            with openers[attempt % 2].open(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            time.sleep(2.5)
    return None


def check_update(force=False):
    """查 GitHub Release 最新版；后台线程拉取（不卡界面），结果回主线程弹窗"""
    import threading

    def work():
        rel = _fetch_release()
        if _asker:
            _asker.performSelectorOnMainThread_withObject_waitUntilDone_(
                "result:", (rel, bool(force)), False)

    threading.Thread(target=work, daemon=True).start()


def _update_loop():
    import time as _t
    _t.sleep(60)
    while True:
        check_update()
        _t.sleep(24 * 3600)


_asker = None


def start_update_checker():
    global _asker
    import threading
    _asker = UpdateAsker.alloc().init()
    threading.Thread(target=_update_loop, daemon=True).start()


def quit_app():
    """退出：风扇恢复系统自动（安全），再结束应用"""
    for i in (0, 1):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8765/api/fan",
                data=json.dumps({"fan": i, "mode": "auto"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=1)
        except Exception:
            pass
    rumps.quit_application()


class MonitorApp(rumps.App):
    def __init__(self):
        super().__init__("MacMonitor", quit_button=None)
        self.menu = [
            "Open Panel",
            None,
            "Fan",
            "CPU",
            "Memory",
            "GPU",
            "Network",
            None,
            "Check for Updates…",
            "Quit",
        ]
        self.menu["Open Panel"].set_callback(lambda _: open_panel())
        self.menu["Check for Updates…"].set_callback(lambda _: check_update(force=True))
        self.menu["Quit"].set_callback(lambda _: quit_app())
        for key in ("Fan", "CPU", "Memory", "GPU", "Network"):
            self.menu[key].set_callback(lambda _: open_panel())
        self.timer = rumps.Timer(self.tick, 4)
        self.timer.start()

    _last_titles = {}

    def set(self, key, title):
        if self._last_titles.get(key) == title:
            return  # 无变化不触发 AppKit 重绘
        self._last_titles[key] = title
        try:
            self.menu[key].title = title
        except Exception:
            pass

    def tick(self, _):
        try:
            d = server._stats.get("sys_d") or {}
            if not d or time.time() - server._stats.get("ts", 0) > 8:
                raise RuntimeError("stale")
            s = d["system"]
            cpu = round(s["cpu"])
            mem = round(s["memUsed"] / s["memTotal"] * 100)
            fans = d.get("fans") or []
            rpm = max(int(f["rpm"] or 0) for f in fans) if fans else 0
            gpu = max([g.get("util") or 0 for g in (d.get("gpus") or [])], default=0)
            t = f"🌀{rpm}  C{cpu}%  M{mem}%  G{round(gpu)}%"
            if t != getattr(self, "_last_title", ""):
                self._last_title = t
                self.title = t

            fan_txt = "  /  ".join(
                f"{f['rpm']:.0f}rpm{('M' if f.get('manual') else ('S' if (f.get('policy') or {}).get('mode') == 'sensor' else ''))}"
                for f in fans) or "No data"
            self.set("Fan", f"Fan  {fan_txt}")
            self.set("CPU", f"CPU  {s['cpu']:.0f}%   {d.get('temps', {}).get('cpu') or '-'}°C   Load {s['load'][0]:.1f}")
            self.set("Memory", f"Memory  {s['memUsed'] / 2 ** 30:.1f}/{s['memTotal'] / 2 ** 30:.0f}G   Swap {s['swapUsed'] / 2 ** 30:.1f}G")
            gpus = d.get("gpus") or []
            best = {}
            for g in gpus:
                k = g["name"]
                if k not in best or (g.get("util") or 0) > (best[k].get("util") or 0):
                    best[k] = g
            gpu_line = "  ".join(
                f"{g['name']}{g.get('util')}%" + (f" VRAM {g['vramInUse'] / 2 ** 30:.1f}G" if g.get("vramInUse") is not None else "")
                for g in best.values()) or "No data"
            self.set("GPU", f"GPU  {gpu_line}")
            net = d.get("net") or {}

            def kb(v):
                return f"{v / 1000:.0f}K" if v >= 1000 else f"{v:.0f}B"

            self.set("Network", f"Network  ↓{kb(net.get('down', 0))}/s  ↑{kb(net.get('up', 0))}/s")
        except Exception:
            import traceback
            with open("/tmp/resmon-app.log", "a") as f:
                f.write(traceback.format_exc() + "\n")
            self.title = "Offline"
            for k in ("Fan", "CPU", "Memory", "GPU", "Network"):
                self.set(k, f"{k}  –")


class MainCall(NSObject):
    def call_(self, fn):
        fn()


_maincall = None


def on_main(fn):
    if _maincall:
        _maincall.performSelectorOnMainThread_withObject_waitUntilDone_("call:", fn, False)


if __name__ == "__main__":
    _maincall = MainCall.alloc().init()
    server.UI_HOOKS["about"] = lambda: on_main(panel.show_about)

    def _demo_kill():
        # 演示用：挑一个可识别的进程弹详情（不实际结束）
        plist = server._stats.get("plist") or []
        prefer = ("WeChat", "Safari", "Chrome", "Firefox", "TextEdit", "Finder", "Music")
        pick = None
        for n in prefer:
            pick = next((p for p in plist if n.lower() in p["name"].lower()), None)
            if pick:
                break
        if not pick:
            pick = max(plist, key=lambda p: p["cpu"]) if plist else None
        if pick:
            panel.show_detail(pick)

    server.UI_HOOKS["kill"] = lambda: on_main(_demo_kill)
    server.UI_HOOKS["stopmodal"] = lambda: on_main(NSApp.stopModal)
    server.start_embedded(8765)  # 后台线程 + HTTP，同进程
    start_qr_refresher()  # 收款码每 6h 从 GitHub 刷新（成功才换新，失败保留旧图）
    start_update_checker()  # 版本检查：启动 1 分钟后 + 每 24 小时
    if not os.environ.get("MACMON_HIDE"):  # MACMON_HIDE=1: 仅菜单栏（测试用）
        rumps.Timer(lambda t: (t.stop(), open_panel()), 1.5).start()  # 启动即开面板
    MonitorApp().run()
