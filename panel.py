#!/usr/bin/env python3
"""MacMonitor 原生面板（AppKit）：风扇控制 + 温度/信息行 + 进程表
替代 WKWebView——原生 NSTableView 常驻开销极低（WKWebView 空窗口即 ~8% CPU）。"""
import time
import urllib.request
from pathlib import Path

import psutil
from AppKit import (
    NSAlert, NSApp, NSButton, NSColor, NSFont, NSImage, NSPopUpButton,
    NSScrollView, NSSearchField, NSSlider, NSTableColumn, NSTableView,
    NSTextField, NSView, NSWindow, NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskFullSizeContentView,
    NSBackingStoreBuffered, NSMakeRect,
    NSAppearance, NSImageView, NSButton,
)
from Foundation import NSObject, NSRunLoop, NSTimer, NSSortDescriptor
from objc import YES, NO

import server

VERSION = "1.2.2"

QR_URL = "https://raw.githubusercontent.com/929541303/macmonitor/main/donate/wechat-qr.png"
QR_CACHE = Path.home() / "Library/Application Support/MacMonitor/wechat-qr.png"

# 配色（与网页版一致）
BG = (0.067, 0.075, 0.094, 1.0)      # #111318
TXT = (0.875, 0.890, 0.918, 1.0)     # #dfe3ea
DIM = (0.529, 0.569, 0.639, 1.0)     # #8791a3
ACC = (0.310, 0.549, 1.0, 1.0)       # #4f8cff
WARN = (0.902, 0.635, 0.235, 1.0)    # #e6a23c
DAN = (0.890, 0.365, 0.365, 1.0)     # #e35d5d

F = 11.0

COLS = [  # (key, 标题, 宽)
    ("pid", "PID", 46), ("name", "进程", 130),
    ("cpu", "CPU%", 50), ("gpu", "GPU%", 50),
    ("net", "网络", 88), ("mem", "内存", 56),
    ("threads", "线程", 44), ("kill", "·", 24),
]


def c(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def fmt(b):
    """网络速率：<1K 用 bit，<1M 用 K，<1G 用 M"""
    if b < 128:
        return "%db" % round(b * 8)
    if b < 1048576:
        return ("%.1fK" if b < 10240 else "%.0fK") % (b / 1024)
    if b < 1073741824:
        return "%.1fM" % (b / 1048576)
    return "%.2fG" % (b / 1073741824)


def net_txt(p):
    i, o = p.get("netIn") or 0, p.get("netOut") or 0
    if not i and not o:
        return "–"
    return "↓%s ↑%s" % (fmt(i), fmt(o))


def label(frame, text="", color=DIM, size=F, align_right=False):
    t = NSTextField.labelWithString_(text)
    t.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, 0))
    t.setTextColor_(c(*color))
    t.setBackgroundColor_(c(0, 0, 0, 0))
    t.setEditable_(NO)
    t.setSelectable_(NO)
    if align_right:
        t.setAlignment_(2)
    if frame:
        t.setFrame_(frame)
    return t


class PanelTable(NSObject):
    """进程表数据源/委托"""

    def numberOfRowsInTableView_(self, tv):
        return len(panel.rows)

    def tableView_viewForTableColumn_row_(self, tv, col, row):
        key = col.identifier()
        p = panel.rows[row]
        cell = tv.makeViewWithIdentifier_owner_(key, self)
        if cell is None:
            cell = NSTextField.labelWithString_("")
            cell.setIdentifier_(key)
            cell.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(F, 0))
            cell.setBackgroundColor_(c(0, 0, 0, 0))
            cell.setEditable_(NO)
            cell.setSelectable_(NO)
        v, colr = "", TXT
        if key == "pid":
            v, colr = str(p["pid"]), DIM
        elif key == "name":
            v, colr = p["name"], TXT
        elif key == "cpu":
            v = "%.1f" % p["cpu"]
            colr = WARN if p["cpu"] > 30 else (DAN if p["cpu"] > 80 else TXT)
        elif key == "gpu":
            v = "%.1f" % (p.get("gpu") or 0)
            colr = WARN if (p.get("gpu") or 0) > 15 else (DAN if (p.get("gpu") or 0) > 50 else TXT)
        elif key == "net":
            v = net_txt(p)
        elif key == "mem":
            v = "%.0f" % p["mem"]
        elif key == "threads":
            v, colr = str(p["threads"]), DIM
        elif key == "kill":
            v, colr = "✕", DIM
        cell.setStringValue_(v)
        cell.setTextColor_(c(*colr))
        cell.setAlignment_(0 if key == "name" else 2)
        return cell

    def tableViewSelectionDidChange_(self, note):
        row = note.object().selectedRow()
        if 0 <= row < len(panel.rows):
            note.object().deselectRow_(row)
            panel.show_detail(panel.rows[row])

    def tableView_sortDescriptorsDidChange_(self, tv, old):
        sds = tv.sortDescriptors()
        if sds:
            sd = sds[0]
            panel.sort_k = sd.key()
            panel.sort_asc = bool(sd.ascending())
            panel.refresh_table()


class QRUpdater(NSObject):
    """下载完成后回主线程刷新 About 里的二维码"""

    def apply_(self, path):
        panel.reload_qr(str(path))


def _fetch_qr():
    """从 GitHub 拉最新收款码；有变化才写缓存，返回新路径"""
    try:
        req = urllib.request.Request(QR_URL)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if not data.startswith(b"\x89PNG") or len(data) < 500:
            return None
        old = QR_CACHE.read_bytes() if QR_CACHE.exists() else b""
        if data == old:
            return None
        QR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        QR_CACHE.write_bytes(data)
        return str(QR_CACHE)
    except Exception:
        return None


def _qr_loop():
    while True:
        time.sleep(5 if not QR_CACHE.exists() else 20)  # 无缓存时更快取一次
        p = _fetch_qr()
        if p:
            _qr_updater.performSelectorOnMainThread_withObject_waitUntilDone_("apply:", p, False)
        time.sleep(6 * 3600)  # 每 6 小时检查一次


_qr_updater = None


def start_qr_refresher():
    global _qr_updater
    import threading
    _qr_updater = QRUpdater.alloc().init()
    threading.Thread(target=_qr_loop, daemon=True).start()


class PanelWinDelegate(NSObject):
    def windowShouldClose_(self, sender):
        sender.orderOut_(None)  # 关窗=隐藏，菜单栏驻留
        NSApp.setActivationPolicy_(1)  # 回到仅菜单栏（无 Dock 图标）
        return NO


class SearchDelegate(NSObject):
    """搜索框实时过滤（每敲一个字就刷新，不必回车）"""

    def controlTextDidChange_(self, notif):
        f = notif.object()
        panel.filter = f.stringValue().lower()
        panel.refresh_table()



class PanelTimer(NSObject):
    def tick_(self, t):
        panel.refresh()

    def slider_(self, t):
        panel.slider_apply()


class PanelActions(NSObject):
    """控件动作中转：AppKit 的 target 必须是 ObjC 对象才能收到点击"""
    def modeChanged_(self, s):
        panel.onMode_(s)

    def sliderMoved_(self, s):
        panel.onSlider_(s)

    def sensorChanged_(self, s):
        panel.onSensorCfg_(s)

    def searchEntered_(self, s):
        panel.onSearch_(s)

    def targetEdited_(self, s):
        panel.onTarget_(s)

    def aboutClicked_(self, s):
        panel.onAbout_(s)


class Panel:
    """面板窗口构建与刷新（PyObjC 动作转发到本类实例）"""

    def __init__(self):
        self.win = None
        self.rows = []
        self.sort_k, self.sort_asc = "cpu", False
        self.filter = ""
        self.cfg_until = 0.0       # 配置锁：操作后 3s 内不回写控件
        self._built = False
        self._last_plist = None
        self._sl_timer = None
        self._sens_map = {}

    def build(self):
        if self._built:
            NSApp.setActivationPolicy_(0)  # Regular：可置前、有 Dock 图标
            NSApp.activateIgnoringOtherApps_(True)
            self.win.makeKeyAndOrderFront_(None)
            return
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 560, 300),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        win.setTitle_("MacMonitor")
        win.setMinSize_((470, 260))
        win.setLevel_(3)        # NSFloatingWindowLevel：悬浮在普通窗口之上
        win.setCollectionBehavior_(1)  # CanJoinAllSpaces：全屏空间也可见
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        win.setBackgroundColor_(c(*BG))
        self.win_delegate = PanelWinDelegate.alloc().init()  # 强引用：防 GC 后 AppKit 访问野指针
        win.setDelegate_(self.win_delegate)
        content = win.contentView()
        H = content.frame().size.height
        W = 560

        # ── 风扇行（顶部）──
        fan = NSView.alloc().initWithFrame_(NSMakeRect(0, H - 34, W, 34))
        fan.setAutoresizingMask_(2 | 8)  # 宽伸缩 + 顶边距固定
        fan.addSubview_(label(NSMakeRect(8, 10, 14, 15), "左"))
        self.rpm_l = label(NSMakeRect(28, 5, 58, 22), "–", TXT, 15)
        fan.addSubview_(self.rpm_l)
        fan.addSubview_(label(NSMakeRect(100, 10, 14, 15), "右"))
        self.rpm_r = label(NSMakeRect(120, 5, 58, 22), "–", TXT, 15)
        fan.addSubview_(self.rpm_r)

        self.mode_sel = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(186, 6, 78, 22), False)
        self.mode_sel.setFont_(NSFont.systemFontOfSize_(F))
        for m in ("自动", "恒定", "联动"):
            self.mode_sel.menu().addItemWithTitle_action_keyEquivalent_(m, None, chr(0))
        fan.addSubview_(self.mode_sel)

        self.st = label(NSMakeRect(256, 10, 76, 15), "")
        fan.addSubview_(self.st)

        self.slider = NSSlider.alloc().initWithFrame_(NSMakeRect(330, 8, 128, 19))
        self.slider.setMinValue_(2000)
        self.slider.setMaxValue_(6000)
        fan.addSubview_(self.slider)
        self.sv = label(NSMakeRect(464, 10, 64, 15), "", ACC, align_right=True)
        fan.addSubview_(self.sv)

        self.sensor_sel = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(330, 6, 106, 22), False)
        self.sensor_sel.setFont_(NSFont.systemFontOfSize_(F))
        fan.addSubview_(self.sensor_sel)
        fan.addSubview_(label(NSMakeRect(438, 10, 12, 15), "≤"))
        self.tgt = NSTextField.alloc().initWithFrame_(NSMakeRect(452, 7, 30, 20))
        self.tgt.setStringValue_("70")
        self.tgt.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(F, 0))
        self.tgt.setTextColor_(c(*TXT))
        self.tgt.setBackgroundColor_(c(*BG))
        self.tgt.setBezeled_(YES)
        self.tgt.setAlignment_(2)
        fan.addSubview_(self.tgt)
        fan.addSubview_(label(NSMakeRect(484, 10, 10, 15), "°"))
        content.addSubview_(fan)

        # ── 温度行 / 信息行 ──
        self.temps = label(NSMakeRect(10, H - 52, W - 20, 15), "温度加载中…")
        self.temps.setAutoresizingMask_(2 | 8)
        content.addSubview_(self.temps)
        self.info = label(NSMakeRect(10, H - 68, W - 20, 15), "加载中…")
        self.info.setAutoresizingMask_(2 | 8)
        content.addSubview_(self.info)

        # ── 工具行 ──
        self.search = NSSearchField.alloc().initWithFrame_(NSMakeRect(8, H - 93, 180, 21))
        self.search.setPlaceholderString_("搜索进程…")
        self.search.setFont_(NSFont.systemFontOfSize_(F - 1))
        self.search_delegate = SearchDelegate.alloc().init()  # 强引用：同上
        self.search.setDelegate_(self.search_delegate)  # 输入即过滤
        self.search.setAutoresizingMask_(2 | 8)  # 宽伸缩 + 顶边距固定（防被表格盖住）
        content.addSubview_(self.search)
        self.pcount = label(NSMakeRect(192, H - 91, 80, 14), "", size=9.5)
        self.pcount.setAutoresizingMask_(8)
        content.addSubview_(self.pcount)
        about = NSButton.buttonWithTitle_target_action_("ⓘ", self, "onAbout:")
        about.setBezelStyle_(11)
        about.setFont_(NSFont.systemFontOfSize_(F))
        about.setFrame_(NSMakeRect(W - 38, H - 93, 28, 21))
        about.setAutoresizingMask_(4 | 8)  # 右边距固定
        content.addSubview_(about)

        # ── 进程表（填充剩余）──
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H - 97))
        scroll.setAutoresizingMask_(2 | 16)
        tv = NSTableView.alloc().init()
        tv.setBackgroundColor_(c(0, 0, 0, 0))
        tv.setRowHeight_(20)
        self.tbl = PanelTable.alloc().init()
        tv.setDataSource_(self.tbl)
        tv.setDelegate_(self.tbl)
        for key, title, width in COLS:
            col = NSTableColumn.alloc().initWithIdentifier_(key)
            col.setMinWidth_(width * 0.7)
            col.setWidth_(width)
            col.headerCell().setStringValue_(title)
            if key != "kill":
                col.setSortDescriptorPrototype_(
                    NSSortDescriptor.sortDescriptorWithKey_ascending_selector_(key, True, None))
            tv.addTableColumn_(col)
        # 初始按 CPU 降序（表头箭头同步）
        tv.setSortDescriptors_([NSSortDescriptor.sortDescriptorWithKey_ascending_selector_("cpu", False, None)])
        scroll.setDocumentView_(tv)
        content.addSubview_(scroll)
        self.tv = tv

        # 动作接线：必须经 NSObject 中转（纯 Python target 收不到 AppKit action）
        self.actions = PanelActions.alloc().init()
        self.mode_sel.setTarget_(self.actions)
        self.mode_sel.setAction_("modeChanged:")
        self.slider.setTarget_(self.actions)
        self.slider.setAction_("sliderMoved:")
        self.sensor_sel.setTarget_(self.actions)
        self.sensor_sel.setAction_("sensorChanged:")
        self.search.setTarget_(self.actions)
        self.search.setAction_("searchEntered:")
        self.tgt.setTarget_(self.actions)
        self.tgt.setAction_("targetEdited:")
        about.setTarget_(self.actions)
        about.setAction_("aboutClicked:")
        self.fill_sensors()  # 预填：打开联动时下拉不空

        self.win = win
        self._built = True
        win.center()
        self.timer = PanelTimer.alloc().init()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            3.0, self.timer, "tick:", None, True)
        NSApp.setActivationPolicy_(0)  # Regular：窗口可压到最前
        NSApp.activateIgnoringOtherApps_(True)
        win.makeKeyAndOrderFront_(None)

    # ── 刷新（PanelTimer.tick_ 每 3s）──
    def refresh(self):
        st = server._stats
        sysd = st.get("sys_d")
        if not sysd or time.time() - st.get("ts", 0) > 8:
            return
        s = sysd["system"]
        fans = sysd.get("fans") or []
        if fans:
            self.rpm_l.setStringValue_("%d" % round(fans[0]["rpm"] or 0))
            if len(fans) > 1:
                self.rpm_r.setStringValue_("%d" % round(fans[1]["rpm"] or 0))
            if time.time() > self.cfg_until:
                mode = "sensor" if any((f.get("policy") or {}).get("mode") == "sensor" for f in fans) \
                    else ("manual" if any(f.get("manual") for f in fans) else "auto")
                self.mode_sel.selectItemWithTitle_({"sensor": "联动", "manual": "恒定"}.get(mode, "自动"))
                pol = next((f["policy"] for f in fans if (f.get("policy") or {}).get("mode") == "sensor"), None)
                if mode == "manual":
                    rpm = int(sum(f["rpm"] or 0 for f in fans) / len(fans))
                    self.st.setStringValue_("恒定 %d" % rpm)
                    self.slider.setIntegerValue_(rpm)
                    self.sv.setStringValue_("%d rpm" % rpm)
                elif pol:
                    self.st.setStringValue_("联动≤%s°" % pol.get("target"))
                else:
                    self.st.setStringValue_("自动")
                self.show_cfg(mode)
        # 温度行
        short = {"CPU 附近": "CPU", "CPU 核心": "核", "CPU 封装": "封装", "主板 1": "板1",
                 "主板 2": "板2", "内存": "Mem", "电池": "电池", "电源": "电源"}
        parts = ["%s %s°" % (short.get(x["name"], x["name"]), round(x["value"]))
                 for x in server.get_sensors()]
        self.temps.setStringValue_("  ".join(parts) or "无传感器")
        # 信息行
        gpus = sysd.get("gpus") or []
        gmax = max([g.get("util") or 0 for g in gpus], default=0)
        gp = "·".join("%s%d" % (("独" if g["name"] == "独显" else "核" if g["name"] == "核显" else g["name"][:2]),
                                g.get("util") or 0)
                      for g in gpus if (g.get("util") or 0) > 0) or "–"
        net = sysd.get("net") or {}
        self.info.setStringValue_(
            "CPU %d%%   内存 %.1f/%.0fG   GPU %d%% (%s)   网 ↓%s/s ↑%s/s" % (
                round(s["cpu"]), s["memUsed"] / 2 ** 30, s["memTotal"] / 2 ** 30,
                round(gmax), gp, fmt(net.get("down") or 0), fmt(net.get("up") or 0)))
        # 进程表：仅当新快照到达
        plist = st.get("plist")
        if plist is not None and plist is not self._last_plist:
            self._last_plist = plist
            self.refresh_table()

    def refresh_table(self):
        if not self._built:
            return  # 构建期间 sortDescriptors 回调先于 tv 赋值
        plist = server._stats.get("plist") or []
        q = self.filter
        rows = [p for p in plist if not q or q in p["name"].lower() or q in str(p["pid"])]
        k = self.sort_k
        if k == "net":
            keyf = lambda p: (p.get("netIn") or 0) + (p.get("netOut") or 0)
        elif k == "name":
            keyf = lambda p: p["name"]
        else:
            keyf = lambda p: p.get(k) if p.get(k) is not None else 0
        try:
            rows.sort(key=keyf, reverse=not self.sort_asc)
        except TypeError:
            rows.sort(key=lambda p: str(p.get(k) or 0), reverse=not self.sort_asc)
        self.rows = rows[:150]
        self.pcount.setStringValue_("%d 进程" % len(rows))
        self.tv.reloadData()

    # ── 交互动作 ──
    def onMode_(self, sender):
        self.cfg_until = time.time() + 3
        m = sender.titleOfSelectedItem()
        self.show_cfg({"恒定": "manual", "联动": "sensor"}.get(m, "auto"))
        if m == "自动":
            self.fan_set("auto")
        elif m == "恒定":
            self.fan_set("manual", rpm=int(self.slider.value()))
        else:
            self.fill_sensors()
            pol = (server._fan_policy or {}).get(0) or {}
            if pol.get("mode") == "sensor":
                # 键 -> 中文名选中
                name = server.SENSOR_KEYS.get(pol.get("sensor"), pol.get("sensor"))
                idx = self.sensor_sel.indexOfItemWithTitle_(name)
                if idx >= 0:
                    self.sensor_sel.selectItemAtIndex_(idx)
                self.tgt.setStringValue_(str(pol.get("target", 70)))
            self.apply_sensor()

    def onSlider_(self, sender):
        v = int(sender.value())
        self.sv.setStringValue_("%d rpm" % v)
        self.cfg_until = time.time() + 3
        if self._sl_timer:
            self._sl_timer.invalidate()
        self._sl_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.45, self.timer, "slider:", None, False)

    def slider_apply(self):
        self._sl_timer = None
        self.fan_set("manual", rpm=int(self.slider.value()))

    def onSensorCfg_(self, sender):
        self.cfg_until = time.time() + 3
        self.apply_sensor()

    def onTarget_(self, sender):
        self.cfg_until = time.time() + 3
        try:
            int(sender.stringValue())
        except ValueError:
            sender.setStringValue_("70")
        self.apply_sensor()

    def apply_sensor(self):
        name = self.sensor_sel.titleOfSelectedItem()
        key = self._sens_map.get(name) if name else None
        if not key:  # 映射缺失时按中文名反查
            for k, v in server.SENSOR_KEYS.items():
                if v == name:
                    key = k
                    break
        try:
            target = int(self.tgt.stringValue())
        except ValueError:
            target = 70
        self.fan_set("sensor", sensor=key or "TC0P", target=target)

    def onSearch_(self, sender):
        self.filter = sender.stringValue().lower()
        self.refresh_table()

    def onAbout_(self, sender):
        self.show_about()

    def reload_qr(self, path):
        iv = getattr(self, "about_iv", None)
        if iv is not None and self.about_win is not None and self.about_win.isVisible():
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img:
                iv.setImage_(img)

    def show_about(self):
        if getattr(self, "about_win", None):
            NSApp.activateIgnoringOtherApps_(True)
            self.about_win.makeKeyAndOrderFront_(None)
            return
        W, H = 300, 436
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskFullSizeContentView,
            NSBackingStoreBuffered, False)
        win.setTitle_("关于")
        win.setTitlebarAppearsTransparent_(YES)
        win.setTitleVisibility_(1)  # NSWindowTitleHidden
        win.setMovableByWindowBackground_(YES)
        win.setLevel_(3)
        win.setBackgroundColor_(c(*BG))
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        win.setReleasedWhenClosed_(NO)
        win.setDelegate_(self.win_delegate)
        cv = win.contentView()
        cv.setWantsLayer_(YES)

        # App 图标
        icon_path = server.ROOT / "icon.png"
        if not icon_path.exists():
            icon_path = server.ROOT / "icon.icns"
        ic = NSImageView.alloc().initWithFrame_(NSMakeRect(W / 2 - 36, H - 78, 72, 72))
        if icon_path.exists():
            im = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if im:
                ic.setImage_(im)
        ic.setImageScaling_(3)
        cv.addSubview_(ic)

        def center(y, h, text, color, size, weight=0):
            t = NSTextField.labelWithString_(text)
            t.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
            t.setTextColor_(c(*color))
            t.setBackgroundColor_(c(0, 0, 0, 0))
            t.setEditable_(NO)
            t.setSelectable_(NO)
            t.setAlignment_(1)
            t.setFrame_(NSMakeRect(16, y, W - 32, h))
            cv.addSubview_(t)
            return t

        center(H - 100, 22, "MacMonitor", TXT, 17, 0.6)
        center(H - 120, 16, "v" + VERSION, DIM, 11)
        center(H - 148, 30, "进程 / GPU / 网络监控 · 风扇三模式控温\n数据仅在本地，不上传", DIM, 11)

        # 打赏卡片（缓存=GitHub 最新版，捆绑图为兜底）
        qr = QR_CACHE if QR_CACHE.exists() else (server.ROOT / "donate" / "wechat-qr.png")
        if qr.exists():
            qimg = NSImage.alloc().initWithContentsOfFile_(str(qr))
            if qimg:
                card = NSView.alloc().initWithFrame_(NSMakeRect(W / 2 - 92, 88, 184, 184))
                card.setWantsLayer_(YES)
                card.layer().setBackgroundColor_(c(1, 1, 1, 1).CGColor())
                card.layer().setCornerRadius_(14.0)
                card.layer().setMasksToBounds_(YES)
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(9, 9, 166, 166))
                iv.setImage_(qimg)
                iv.setImageScaling_(3)
                card.addSubview_(iv)
                cv.addSubview_(card)
                self.about_iv = iv
        center(52, 18, "创作不易 · 赏杯咖啡 ☕️", ACC, 12.5, 0.5)
        center(30, 14, "微信扫一扫，支持作者继续维护", DIM, 10)

        self.about_win = win
        win.center()
        NSApp.activateIgnoringOtherApps_(True)
        win.makeKeyAndOrderFront_(None)

    def show_cfg(self, mode):
        self.slider.setHidden_(mode != "manual")
        self.sv.setHidden_(mode != "manual")
        self.sensor_sel.setHidden_(mode != "sensor")
        self.tgt.setHidden_(mode != "sensor")
        if mode != "sensor":
            for v in (self.sensor_sel, self.tgt):
                v.setHidden_(True)

    def fill_sensors(self):
        """下拉显示中文名，提交时映射回 SMC 键"""
        if self.sensor_sel.numberOfItems() == 0:
            self._sens_map = {}
            items = server.get_sensors() or [{"key": k, "name": v} for k, v in server.SENSOR_KEYS.items()]
            for x in items:
                self.sensor_sel.menu().addItemWithTitle_action_keyEquivalent_(x["name"], None, chr(0))
                self._sens_map[x["name"]] = x["key"]

    def fan_set(self, mode, rpm=None, sensor=None, target=None):
        """两风扇共用一套策略；SMC 写入异步避免卡 UI"""
        import threading

        def work():
            for i in (0, 1):
                try:
                    server.set_fan(i, mode, rpm=rpm, sensor=sensor, target=target)
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def show_detail(self, p):
        a = NSAlert.alloc().init()
        a.setMessageText_("%s（PID %d）" % (p["name"], p["pid"]))
        a.setInformativeText_(
            "CPU %.1f%%   GPU %.1f%%   内存 %.0fMB   线程 %d\n"
            "网 ↓%s/s ↑%s/s   累计 ↓%s ↑%s" % (
                p["cpu"], p.get("gpu") or 0, p["mem"], p["threads"],
                fmt(p.get("netIn") or 0), fmt(p.get("netOut") or 0),
                fmt(p.get("bytesIn") or 0), fmt(p.get("bytesOut") or 0)))
        a.addButtonWithTitle_("退出进程")
        a.addButtonWithTitle_("强杀")
        a.addButtonWithTitle_("关闭")
        r = a.runModal()
        if r == 1000:
            self.kill(p, False)
        elif r == 1001:
            self.kill(p, True)

    def kill(self, p, force):
        try:
            proc = psutil.Process(p["pid"])
            proc.kill() if force else proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            err = NSAlert.alloc().init()
            err.setMessageText_("权限不足")
            err.setInformativeText_("系统/他人进程，无法结束")
            err.addButtonWithTitle_("好")
            err.runModal()
        except Exception:
            pass


panel = Panel()
