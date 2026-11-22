#!/bin/bash
# 本地预演：只用本机已克隆的 OpenWrt 源码验证 packages.txt → .config 的解析、
# 包名校验、以及最终哪些包真的生效。不编译固件（那只在 GitHub Actions 跑）。
#
# 用法：
#   bash scripts/gen-config.sh /path/to/openwrt
#
# 前提：该源码目录已执行过 ./scripts/feeds update -a 与 ./scripts/feeds install -a，
# 且已执行过 ./scripts/feeds update -i 生成 feeds/*.index
# （feeds install / index 都需要 host python 与 libncurses、gawk；见 docs）

set -euo pipefail

OW="${1:-/tmp/ow}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$OW" ] || { echo "源码目录不存在: $OW"; exit 1; }
[ -f "$OW/feeds/luci.index" ] || { echo "缺少 $OW/feeds/luci.index，先跑 ./scripts/feeds update -a && ./scripts/feeds update -i"; exit 1; }

cd "$OW"

cp "$ROOT/config/wax206-25.12.5.config" .config

# LuCI 语言包的 Default 是 "LUCI_LANG_zh_Hans||(ALL&&m)"：
# 不开 LUCI_LANG_zh_Hans 也不开 ALL 时，defconfig 会把它们判成 =m 甚至
# not set——而目标镜像不出 m 包，等于中文界面直接消失。
# 这个坑原版 config 也有（它只写了包名，没写语言开关）。
echo 'CONFIG_LUCI_LANG_zh_Hans=y' >> .config

echo '### 追加的软件包选择' >> .config

missing=0
while IFS= read -r line; do
  pkg="$(echo "$line" | sed -e 's/#.*//' -e 's/[[:space:]]//g')"
  [ -z "$pkg" ] && continue
  name="${pkg%%=*}"
  case "$pkg" in
    *=y|*=n|*=m) echo "CONFIG_PACKAGE_${name}=${pkg#*=}" >> .config ;;
    *)           echo "CONFIG_PACKAGE_${name}=y" >> .config ;;
  esac
  if ! grep -qx "Package: ${name}" feeds/*.index; then
    echo "  MISSING  ${name}  (不在任何官方 feed 中)"
    missing=1
  fi
done < "$ROOT/packages.txt"

[ "$missing" -eq 0 ] || { echo; echo "有包名不存在，先修正 packages.txt"; exit 1; }

make defconfig > /tmp/genconfig.log 2>&1 || { tail -5 /tmp/genconfig.log; exit 1; }

echo
echo "=== 文件系统 / 打包器 ==="
for o in TARGET_ROOTFS_SQUASHFS TARGET_ROOTFS_EXT4FS TARGET_EXT4_JOURNAL USE_APK TARGET_ROOTFS_PARTSIZE; do
  printf '  %-26s %s\n' "$o" "$(grep -E "^CONFIG_${o}=" .config || echo '(未设置)')"
done

echo
echo "=== packages.txt 生效情况 ==="
fail=0
while IFS= read -r line; do
  pkg="$(echo "$line" | sed -e 's/#.*//' -e 's/[[:space:]]//g' -e 's/=.*//')"
  [ -z "$pkg" ] && continue
  if grep -qx "CONFIG_PACKAGE_${pkg}=y" .config; then
    printf '  OK       %s\n' "$pkg"
  else
    printf '  ERROR    %s 没有生效（依赖不满足被裁掉了）\n' "$pkg"
    fail=1
  fi
done < "$ROOT/packages.txt"

echo
echo "  固件内选中的包总数：$(grep -c '^CONFIG_PACKAGE_.*=y$' .config)"
[ "$fail" -eq 0 ] && echo "  全部生效" || echo "  有包未生效，见上"
exit "$fail"
