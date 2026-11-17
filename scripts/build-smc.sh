#!/bin/bash
# 编译 SMC 读写工具（来自 smcFanControl，GPL）
# 产物：bin/smc —— 风扇手动/联动模式需要它（setuid root）
set -e
cd "$(dirname "$0")/.."
mkdir -p bin /tmp/smcbld
for f in smc.c smc.h; do
  curl -sL "https://raw.githubusercontent.com/hholtmann/smcFanControl/master/smc-command/$f" -o "/tmp/smcbld/$f"
done
clang -O2 -DCMD_TOOL_BUILD -I/tmp/smcbld /tmp/smcbld/smc.c -o bin/smc -framework IOKit -framework Foundation
sudo chown root:wheel bin/smc && sudo chmod 4755 bin/smc
echo "已编译并授权 bin/smc"
