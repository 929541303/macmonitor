#!/usr/bin/env python3
"""资源监控面板后端：进程/CPU/内存/GPU/网络/风扇"""
import json, os, re, struct, subprocess, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent
SMC = ROOT / "bin" / "smc"
FAN_COUNT = 2  # 实测该机型双风扇

_last_net = None  # (t, bytes_sent, bytes_recv)
_fan_range_cache = {}
_procs_cpu_cache = {}


def smc_read(key):
    try:
        out = subprocess.run([str(SMC), "-k", key, "-r"], capture_output=True, text=True, timeout=5, close_fds=False).stdout  # posix_spawn：避免在大进程里 fork
        m = re.search(r"\]  ([\d.]+) \(", out)
        if m:
            return float(m.group(1))
        # ui8 等无小数格式: "F0Md  [ui8 ]  0 (bytes 00)"
        m = re.search(r"\]  (\d+) \(bytes", out)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def smc_write(key, hex_bytes):
    r = subprocess.run([str(SMC), "-k", key, "-w", hex_bytes], capture_output=True, text=True, timeout=5, close_fds=False)  # posix_spawn：避免在大进程里 fork
    return r.returncode == 0 and "Error" not in (r.stdout + r.stderr)


def rpm_to_hex(rpm):
    return struct.pack("<f", float(rpm)).hex()  # SMC 小端浮点


_md_cache = {"ts": 0.0, "vals": [None] * FAN_COUNT}


def get_fans():
    import time as _t
    if _t.time() - _md_cache["ts"] > 20:  # 手动标志只在用户操作时变化
        _md_cache["vals"] = [bool(smc_read(f"F{i}Md")) for i in range(FAN_COUNT)]
        _md_cache["ts"] = _t.time()
    fans = []
    for i in range(FAN_COUNT):
        if i not in _fan_range_cache:
            mn, mx = smc_read(f"F{i}Mn"), smc_read(f"F{i}Mx")
            if mn and mx:
                _fan_range_cache[i] = (mn, mx)
        rng = _fan_range_cache.get(i)
        fans.append({
            "index": i,
            "name": f"风扇 {i}",
            "rpm": smc_read(f"F{i}Ac"),
            "min": rng[0] if rng else 2000,
            "max": rng[1] if rng else 6000,
            "manual": _md_cache["vals"][i],
            "policy": _fan_policy.get(i),
        })
    return fans


SENSOR_KEYS = {
    "TC0P": "CPU 附近", "TC0D": "CPU 核心", "TC0E": "CPU 封装", "TG0P": "GPU",
    "Ts0S": "主板 1", "Ts1S": "主板 2", "Tm0P": "内存", "TB1T": "电池", "Tp0P": "电源",
}

_fan_policy = {}  # {index: {"mode": "sensor", "sensor": key, "target": °C}}
_POLICY_FILE = ROOT / "fan-policy.json"
if _POLICY_FILE.exists():
    try:
        _fan_policy = {int(k): v for k, v in json.loads(_POLICY_FILE.read_text()).items()}
    except Exception:
        pass


def save_policy():
    _POLICY_FILE.write_text(json.dumps({str(k): v for k, v in _fan_policy.items()}))


def fan_control_once():
    """传感器联动模式：按温度线性调速（Macs Fan Control 的 custom 方案）"""
    for i, pol in list(_fan_policy.items()):
        if pol.get("mode") != "sensor":
            continue
        t = smc_read(pol.get("sensor", "TC0P"))
        if t is None:
            continue
        rng = _fan_range_cache.get(i)
        if not rng:
            continue
        lo, hi = pol["target"] - 8, pol["target"] + 2  # 目标-8°C 起步，+2°C 拉满
        frac = min(1.0, max(0.0, (t - lo) / (hi - lo)))
        rpm = int(rng[0] + (rng[1] - rng[0]) * frac)
        smc_write(f"F{i}Md", "01")
        smc_write(f"F{i}Tg", rpm_to_hex(rpm))


def set_fan(index, mode, rpm=None, sensor=None, target=None):
    global _fan_policy
    index = int(index)
    if index < 0 or index >= FAN_COUNT:
        raise ValueError("风扇编号无效")
    rng = _fan_range_cache.get(index) or (smc_read(f"F{index}Mn"), smc_read(f"F{index}Mx"))
    if not rng or not rng[0]:
        rng = _fan_range_cache[index] = (2000, 6000)
    if mode == "manual":
        rpm = max(rng[0], min(rng[1], int(rpm)))
        # 本机型实测：F%dMn 只读，需写目标键 F%dTg（Macs Fan Control 同款方案）
        ok1 = smc_write(f"F{index}Md", "01")
        ok2 = smc_write(f"F{index}Tg", rpm_to_hex(rpm))
        if not (ok1 and ok2):
            raise PermissionError(f"写入失败(mode={ok1},rpm={ok2})，SMC 写入需要 root 权限")
        _fan_policy.pop(index, None); save_policy()
    elif mode == "sensor":
        pol = {"mode": "sensor", "sensor": sensor or "TC0P", "target": int(target or 70)}
        _fan_policy[index] = pol; save_policy()  # 调速循环接管
    else:  # auto
        smc_write(f"F{index}Md", "00")
        _fan_policy.pop(index, None); save_policy()
    _md_cache["vals"][index] = (mode == "manual")  # 切换后立即生效，不等 20s 缓存
    _md_cache["ts"] = time.time()
    fans = get_fans()
    for f in fans:
        f["policy"] = _fan_policy.get(f["index"])
    return fans


_gpus_cache = {"ts": 0.0, "data": []}


def get_gpus():
    if time.time() - _gpus_cache["ts"] < 8:  # GPU 利用率变化慢
        return _gpus_cache["data"]
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
                             capture_output=True, text=True, timeout=5, close_fds=False).stdout  # posix_spawn：避免在大进程里 fork
    except Exception:
        return []
    gpus = []
    for block in out.split("+-o "):
        cls = re.search(r'"IOClass" = "([^"]+)"', block)
        stats = re.search(r'"PerformanceStatistics" = \{(.*)\}', block)
        if not cls or not stats:
            continue
        s = stats.group(1)
        def num(k):
            m = re.search(rf'"{k}"=(\d+)', s)
            return int(m.group(1)) if m else None
        util = num("Device Utilization %")
        vfree, vinuse = num("vramFreeBytes"), num("inUseVidMemoryBytes")
        temp, power = num("Temperature(C)"), num("Total Power(W)")
        if util is None and vfree is None:
            continue
        gpus.append({
            "class": cls.group(1),
            "name": "核显" if "Intel" in cls.group(1) else ("独显" if "AMD" in cls.group(1) else cls.group(1)),
            "util": util, "vramFree": vfree, "vramInUse": vinuse,
            "temp": temp, "power": power,
        })
    _gpus_cache.update(ts=time.time(), data=gpus)
    return gpus


def get_temps():
    return {"cpu": smc_read("TC0P")}


_sens = {"ts": 0.0, "list": []}


def get_sensors():
    """传感器列表（6s 缓存）：HTTP 与原生面板共用"""
    now = time.time()
    if now - _sens["ts"] > 10:
        out = []
        for k, name in SENSOR_KEYS.items():
            v = smc_read(k)
            if v is not None:
                out.append({"key": k, "name": name, "value": round(v, 1)})
        _sens.update(list=out, ts=now)
    return _sens["list"]


def net_rates():
    global _last_net
    io = psutil.net_io_counters()
    now = time.time()
    if _last_net is None:
        _last_net = (now, io.bytes_sent, io.bytes_recv)
        return {"down": 0, "up": 0}
    dt = now - _last_net[0]
    down = (io.bytes_recv - _last_net[2]) / dt
    up = (io.bytes_sent - _last_net[1]) / dt
    _last_net = (now, io.bytes_sent, io.bytes_recv)
    return {"down": round(down, 1), "up": round(up, 1)}


_mem_cache = {}  # pid -> (mem MB, threads)：重周期刷新，轻周期复用


def collect_processes(heavy=True):
    """单趟扫描进程列表；heavy 周期才取内存/线程（隔 5s 刷新），轻周期复用缓存"""
    attrs = ["pid", "name", "status"] + (["memory_info", "num_threads"] if heavy else [])
    procs = []
    for p in psutil.process_iter(attrs):
        i = p.info
        pid = i.get("pid")
        if pid is None:
            continue
        if heavy:
            mi = i.get("memory_info")
            _mem_cache[pid] = (round((mi.rss if mi else 0) / 1048576, 1), i.get("num_threads") or 0)
        mem, threads = _mem_cache.get(pid, (0, 0))
        net = _net_by_pid.get(pid) or {}
        procs.append({
            "pid": pid,
            "name": i.get("name") or "",
            "status": i.get("status") or "",
            "cpu": round(_procs_cpu_cache.get(pid, 0.0), 1),
            "mem": mem,
            "threads": threads,
            "gpu": round(_gpu_by_pid.get(pid, 0) / 10, 1),  # 1000ms/s = 100%
            "netIn": round(net.get("rate_in", 0.0), 1),
            "netOut": round(net.get("rate_out", 0.0), 1),
            "bytesIn": net.get("bytes_in", 0),
            "bytesOut": net.get("bytes_out", 0),
            "conns": net.get("conns", 0),
        })
    return procs


def poll_cpu_once():
    """刷新所有进程 cpu_percent 到缓存"""
    for p in psutil.process_iter(["pid"]):
        try:
            _procs_cpu_cache[p.info["pid"]] = p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


_gpu_by_pid = {}
_gpu_ok = False  # sudo 免密是否可用
_net_by_pid = {}   # {pid: {"bytes_in": n, "bytes_out": n, "conns": n}} 采样窗口内增量


def poll_net():
    """后台线程：每 ~6s 用 nettop 采样每进程网络流量（bytes in/out）与连接数"""
    global _net_by_pid
    while True:
        try:
            r = subprocess.run(
                ["sudo", "-n", "/usr/bin/nettop", "-P", "-L", "1", "-x"],
                capture_output=True, text=True, timeout=15, close_fds=False)  # posix_spawn
            d = {}
            if r.returncode == 0:
                for line in r.stdout.splitlines()[1:]:  # 跳过表头
                    f = line.split(",")
                    # 格式: time,proc.pid,iface,state,bytes_in,bytes_out,...
                    if len(f) < 6 or "." not in f[1]:
                        continue
                    try:
                        pid = int(f[1].rsplit(".", 1)[1])
                        ib, ob = int(f[4] or 0), int(f[5] or 0)
                    except ValueError:
                        continue
                    prev = _net_by_pid.get(pid)
                    if prev is None:  # 首见：无基准，速率为 0
                        d[pid] = {"rate_in": 0.0, "rate_out": 0.0, "bytes_in": ib, "bytes_out": ob, "conns": 0}
                    else:
                        d[pid] = {
                            "rate_in": max(0, ib - prev["bytes_in"]) / 6.0,
                            "rate_out": max(0, ob - prev["bytes_out"]) / 6.0,
                            "bytes_in": ib, "bytes_out": ob, "conns": prev.get("conns", 0),
                        }
                _net_by_pid = d
        except Exception:
            pass
        time.sleep(6)


def poll_gpu():
    """后台线程：每 ~10s 用 powermetrics 采样每进程 GPU（ms/s，1000ms/s=100%）"""
    global _gpu_by_pid, _gpu_ok
    while True:
        try:
            r = subprocess.run(
                ["sudo", "-n", "/usr/bin/powermetrics", "--samplers", "tasks",
                 "-i", "1500", "-n", "1", "--show-process-gpu"],
                capture_output=True, text=True, timeout=15, close_fds=False)  # posix_spawn
            d = {}
            if "GPU ms/s" in r.stdout:
                _gpu_ok = True
                for line in r.stdout.splitlines():
                    f = line.split()
                    if len(f) >= 9:
                        try:
                            gpu, pid = float(f[-1]), int(f[-8])
                        except ValueError:
                            continue
                        if gpu > 0.5:
                            d[pid] = max(d.get(pid, 0), gpu)
            _gpu_by_pid = d
        except Exception:
            pass
        time.sleep(8)


_stats = {"full_b": b"", "light_b": b"", "ts": 0.0, "sys_d": None, "plist": None, "ts_p": 0.0}


def _build_system():
    vm = psutil.virtual_memory(); sw = psutil.swap_memory()
    return {
        "time": time.time(),
        "system": {
            "cpu": psutil.cpu_percent(interval=None),  # 增量式：由快照线程定时调用
            "cpuCores": psutil.cpu_percent(percpu=True),
            "memTotal": vm.total, "memUsed": vm.total - vm.available,
            "swapTotal": sw.total, "swapUsed": sw.used,
            "load": os.getloadavg(),
        },
        "net": net_rates(),
        "gpus": get_gpus(),
        "gpuPerProc": {"ok": _gpu_ok, "count": len(_gpu_by_pid)},
        "fans": get_fans(),
        "temps": get_temps(),
    }


def _build_stats():
    """兜底：快照线程未就绪时同步现算全量"""
    d = _build_system()
    d["processes"] = collect_processes(True)
    return d


def monitor_loop():
    """统一监控线程：系统指标 3s / 进程列表+CPU 6s / 联动调速 3s（少线程=少 GIL 争抢）"""
    plist_b = b"[]"
    last = {"sys": 0.0, "plist": 0.0, "fan": 0.0}
    while True:
        t0 = time.time()
        try:
            if t0 - last["sys"] >= 3.0:
                sysd = _build_system()
                _stats["sys_d"] = sysd
                _stats["light_b"] = json.dumps(sysd, ensure_ascii=False).encode()
                last["sys"] = t0
            if t0 - last["plist"] >= 6.0:
                poll_cpu_once()
                plist = collect_processes(True)
                _stats["plist"] = plist
                _stats["ts_p"] = t0
                plist_b = json.dumps(plist, ensure_ascii=False).encode()
                last["plist"] = t0
            _stats['full_b'] = _stats["light_b"][:-1] + b',"processes":' + plist_b + b'}'
            _stats["ts"] = t0
            if t0 - last["fan"] >= 3.0:
                fan_control_once()
                last["fan"] = t0
        except Exception:
            pass
        time.sleep(max(0.4, 3.0 - (time.time() - t0)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/stats"):
            light = "light=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
            body = _stats["light_b" if light else "full_b"]
            if body and time.time() - _stats["ts"] < 8:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(_build_stats())  # 兜底：快照线程未就绪时现算
        elif self.path.startswith("/api/sensors"):
            self._json({"sensors": get_sensors()})
        elif self.path.startswith("/api/proc/"):
            pid = int(self.path.split("/")[3])
            try:
                p = psutil.Process(pid)
                with p.oneshot():
                    d = {
                        "pid": pid, "name": p.name(), "cmdline": " ".join(p.cmdline()[:12]),
                        "exe": p.exe(), "cwd": p.cwd(), "created": p.create_time(),
                        "cpu": p.cpu_percent(None), "mem": p.memory_info().rss,
                        "status": p.status(), "user": p.username(),
                        "gpu": round(_gpu_by_pid.get(pid, 0) / 10, 1),
                        "conns": len(p.net_connections()) if hasattr(p, "net_connections") else None,
                    }
                self._json(d)
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif self.path in ("/", "/index.html"):
            body = (ROOT / "web" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/kill":
            pid = int(body.get("pid", 0))
            force = bool(body.get("force"))
            try:
                p = psutil.Process(pid)
                p.kill() if force else p.terminate()
                return self._json({"ok": True, "pid": pid, "force": force,
                                   "msg": "已强制结束" if force else "已发送退出请求"})
            except psutil.NoSuchProcess:
                return self._json({"ok": True, "msg": "进程已不存在"})
            except psutil.AccessDenied:
                return self._json({"ok": False, "error": "权限不足：系统/他人进程，无法结束"}, 403)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
        if self.path == "/api/fan":
            try:
                fans = set_fan(body.get("fan", 0), body.get("mode", "auto"),
                               body.get("rpm"), body.get("sensor"), body.get("target"))
                return self._json({"ok": True, "fans": fans})
            except PermissionError as e:
                return self._json({"ok": False, "error": str(e)}, 403)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
        if self.path == "/api/sensors":
            now = time.time()
            if now - getattr(self.__class__, "_sens_ts", 0) > 6:
                out = []
                for k, name in SENSOR_KEYS.items():
                    v = smc_read(k)
                    if v is not None:
                        out.append({"key": k, "name": name, "value": round(v, 1)})
                Handler._sens_cache, Handler._sens_ts = out, now
            return self._json({"sensors": getattr(self.__class__, "_sens_cache", [])})
        self._json({"error": "not found"}, 404)


def start_embedded(port=8765):
    """供菜单栏进程内嵌启动：后台线程 + HTTP 服务（同进程，省一个解释器）"""
    for t in (monitor_loop, poll_gpu, poll_net):
        threading.Thread(target=t, daemon=True).start()
    from http.server import HTTPServer
    httpd = HTTPServer(("127.0.0.1", port), Handler)  # 单线程：防线程堆积
    httpd.timeout = 1
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    for t in (monitor_loop, poll_gpu, poll_net):
        threading.Thread(target=t, daemon=True).start()
    print(f"资源监控面板: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
