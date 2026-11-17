#!/usr/bin/env python3
"""MacMonitor 单进程版：菜单栏 + 监控服务 + 面板窗口 三合一（同 Macs Fan Control）"""
import json
import os
import time
from pathlib import Path

import rumps

import server  # 后端：进程/CPU/GPU/网络/风扇 + 内嵌 HTTP(127.0.0.1:8765)
from panel import panel  # 原生 AppKit 面板（替代 WKWebView）

VERSION = "1.2.2"
CHANGELOG = """v1.2.2  原生面板（弃 WKWebView）；CPU 稳定 <5%
v1.2.0  单进程架构（面板/菜单栏/服务合一），图标与菜单栏 🌀 同款
v1.1.0  棒棒糖图标；About 完善（简介/版本/更新）
v1.0.0  首版：进程/GPU/网络监控、排序与结束进程；风扇自动/恒定/传感器联动；菜单栏常驻指标"""
ICON = str(Path(__file__).resolve().parent / "icon.icns")

_panel = {}  # win/web/委托 全局引用，防 GC


def open_panel():
    """显示监控面板窗口（原生 AppKit，首次创建后复用）"""
    panel.build()


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
            "打开完整面板",
            None,
            "风扇",
            "CPU",
            "内存",
            "GPU",
            "网络",
            None,
            "退出",
        ]
        self.menu["打开完整面板"].set_callback(lambda _: open_panel())
        self.menu["退出"].set_callback(lambda _: quit_app())
        for key in ("风扇", "CPU", "内存", "GPU", "网络"):
            self.menu[key].set_callback(lambda _: open_panel())
        self.timer = rumps.Timer(self.tick, 3)
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
                f"{f['rpm']:.0f}rpm{('手动' if f.get('manual') else ('联动' if (f.get('policy') or {}).get('mode') == 'sensor' else ''))}"
                for f in fans) or "无数据"
            self.set("风扇", f"风扇  {fan_txt}")
            self.set("CPU", f"CPU  {s['cpu']:.0f}%   温度 {d.get('temps', {}).get('cpu') or '-'}°C   负载 {s['load'][0]:.1f}")
            self.set("内存", f"内存  {s['memUsed'] / 2 ** 30:.1f}/{s['memTotal'] / 2 ** 30:.0f}G   交换 {s['swapUsed'] / 2 ** 30:.1f}G")
            gpus = d.get("gpus") or []
            best = {}
            for g in gpus:
                k = g["name"]
                if k not in best or (g.get("util") or 0) > (best[k].get("util") or 0):
                    best[k] = g
            gpu_line = "  ".join(
                f"{g['name']}{g.get('util')}%" + (f" 显存{g['vramInUse'] / 2 ** 30:.1f}G" if g.get("vramInUse") is not None else "")
                for g in best.values()) or "无数据"
            self.set("GPU", f"GPU  {gpu_line}")
            net = d.get("net") or {}

            def kb(v):
                return f"{v / 1000:.0f}K" if v >= 1000 else f"{v:.0f}B"

            self.set("网络", f"网络  ↓{kb(net.get('down', 0))}/s  ↑{kb(net.get('up', 0))}/s")
        except Exception:
            import traceback
            with open("/tmp/resmon-app.log", "a") as f:
                f.write(traceback.format_exc() + "\n")
            self.title = "监控离线"
            for k in ("风扇", "CPU", "内存", "GPU", "网络"):
                self.set(k, f"{k}  –")


if __name__ == "__main__":
    server.start_embedded(8765)  # 后台线程 + HTTP，同进程
    if not os.environ.get("MACMON_HIDE"):  # MACMON_HIDE=1: 仅菜单栏（测试用）
        rumps.Timer(lambda t: (t.stop(), open_panel()), 1.5).start()  # 启动即开面板
    MonitorApp().run()
