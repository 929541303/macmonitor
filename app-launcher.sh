#!/bin/bash
# MacMonitor 启动器：完全自包含（内置 Python 运行时+依赖，无需网络/系统 Python）
DIR="$(cd "$(dirname "$0")/../Resources/resmon" && pwd)"

# 从 DMG/挂载卷直接运行：引导安装到 /Applications
case "$DIR" in
/Volumes/*)
  APP_ROOT="$(cd "$DIR/../../.." && pwd)"
  R=$(osascript -e 'display dialog "MacMonitor 还未安装到「应用程序」。\n\n是否现在安装并运行？" buttons {"安装并运行", "直接运行"} default button 1 with icon note' 2>/dev/null | awk -F: '/button/{print $NF}')
  if [ "$R" = "安装并运行" ]; then
    rm -rf /Applications/MacMonitor.app 2>/dev/null
    if cp -R "$APP_ROOT" /Applications/ 2>/dev/null; then
      open /Applications/MacMonitor.app
      exit 0
    fi
  fi
  ;;
esac
cd "$DIR"
export PYTHONHOME="$DIR/runtime"
export PYTHONPATH="$DIR/site-pkgs"
PY="$DIR/runtime/bin/MacMonitor"
NOTIFY() { osascript -e "display notification \"$2\" with title \"MacMonitor\"" 2>/dev/null; }

# 1) 风扇写入授权（SMC setuid）
if [ "$(ls -l bin/smc 2>/dev/null | awk '{print $3}')" != "root" ]; then
  (osascript -e "do shell script \"chown root:wheel '$DIR/bin/smc' && chmod 4755 '$DIR/bin/smc'\" with administrator privileges" \
    2>/dev/null && NOTIFY "已授权" "风扇控制已可用" || NOTIFY "未授权" "风扇控制不可用（只读）") &
fi
# 2) 每进程 GPU/网络 采样授权（powermetrics/nettop 免密）
WHO=$(whoami)
if ! sudo -n /usr/bin/powermetrics --samplers tasks -i 200 -n 1 >/dev/null 2>&1; then
  (osascript -e "do shell script \"mkdir -p /etc/sudoers.d && echo '$WHO ALL=(root) NOPASSWD: /usr/bin/powermetrics, /usr/bin/nettop' > /etc/sudoers.d/resmon && chmod 440 /etc/sudoers.d/resmon\" with administrator privileges" \
    2>/dev/null || NOTIFY "未授权" "每进程GPU/网络监控不可用") &
fi

# 3) 启动（清旧实例，单进程）
for _n in MacMonitor MacMonSrv MacMonBar; do pkill -9 -x "$_n" 2>/dev/null; done
lsof -tiTCP:8765 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
nohup "$PY" app.py > /tmp/resmon.log 2>&1 &
NOTIFY "已启动" "菜单栏可见实时指标，点“打开完整面板”查看全部进程"
