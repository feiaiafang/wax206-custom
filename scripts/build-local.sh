#!/bin/bash
# ============================================================================
# WAX206 本地编译脚本（OpenWrt / ImmortalWrt 通用）
#
# 与 GitHub Actions 里的流程完全一致，只是跑在本机：
#   clone 源码 → feeds → 打扩展分区补丁 → DTS 校验 → 配置 → 编译
#
# 用法：
#   bash scripts/build-local.sh immortalwrt          # ImmortalWrt 25.12（默认）
#   bash scripts/build-local.sh openwrt              # 上游 OpenWrt v25.12.5
#   bash scripts/build-local.sh immortalwrt 178m     # 指定 firmware 尺寸
#   bash scripts/build-local.sh immortalwrt full noext4   # 不出 ext4 镜像
#
# 环境变量：
#   WORKDIR   源码与产物目录，默认 ~/wax206-build
#   JOBS      并行数，默认 nproc
#
# ⚠️ 不要把 WORKDIR 放在 /tmp —— 很多机器上 /tmp 是 tmpfs（内存盘），
#    源码树 + 工具链 + 编译中间产物要 25~35 GB，会直接把内存写爆。
# ============================================================================

set -euo pipefail

FLAVOR="${1:-immortalwrt}"
SIZE="${2:-full}"
EXT4="${3:-ext4}"          # ext4 | noext4

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKDIR="${WORKDIR:-$HOME/wax206-build}"
JOBS="${JOBS:-$(nproc)}"

case "$FLAVOR" in
  immortalwrt)
    REPO="https://github.com/immortalwrt/immortalwrt.git"
    BRANCH="openwrt-25.12"
    SRC="$WORKDIR/immortalwrt"
    CONFIG="$ROOT/config/wax206-immortalwrt.config"
    PART_MIB=247
    ;;
  openwrt)
    REPO="https://git.openwrt.org/openwrt/openwrt.git"
    BRANCH="v25.12.5"
    SRC="$WORKDIR/openwrt"
    CONFIG="$ROOT/config/wax206-25.12.5.config"
    PART_MIB=247
    ;;
  *)
    echo "用法: $0 {immortalwrt|openwrt} [full|178m|...] [ext4|noext4]" >&2
    exit 2
    ;;
esac

# 尺寸 → ext4 根分区大小（必须 <= ubi 分区）
if [ "$SIZE" != "full" ]; then
  MIB="${SIZE%[mM]}"
  PART_MIB=$(( MIB - 6 ))
fi

PATCH_ARGS=(--size "$SIZE")
[ "$EXT4" = "ext4" ] && PATCH_ARGS+=(--ext4)

echo "=========================================================="
echo " 源码     : $REPO ($BRANCH)"
echo " 目录     : $SRC"
echo " 分区     : firmware=$SIZE  (ext4 根分区 ${PART_MIB} MiB)"
echo " 并行     : $JOBS"
echo " ext4 镜像: $EXT4"
echo "=========================================================="

# ---------- 0. 磁盘检查 ----------
mkdir -p "$WORKDIR"
AVAIL_GB=$(df -BG --output=avail "$WORKDIR" | tail -1 | tr -dc '0-9')
echo "[0/6] 磁盘可用 ${AVAIL_GB} GB（首次编译约需 30 GB）"
if [ "$AVAIL_GB" -lt 25 ]; then
  echo "!! 磁盘不足 25 GB，编译很可能中途失败" >&2
fi
case "$WORKDIR" in
  /tmp/*) echo "!! WORKDIR 在 /tmp 下，若 /tmp 是 tmpfs 会写爆内存，请换目录" >&2 ;;
esac

# ---------- 1. 依赖检查 ----------
echo "[1/6] 检查 host 依赖"
MISSING=()
for c in gcc g++ make git rsync unzip wget gawk bison flex python3 perl; do
  command -v "$c" >/dev/null 2>&1 || MISSING+=("$c")
done
for h in ncurses.h openssl/ssl.h zlib.h libelf.h; do
  [ -f "/usr/include/$h" ] || MISSING+=("/usr/include/$h")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "缺少: ${MISSING[*]}" >&2
  echo "Ubuntu/Debian 安装命令：" >&2
  echo "  sudo apt-get install -y build-essential clang flex bison g++ gawk gcc-multilib \\" >&2
  echo "    g++-multilib gettext git libncurses-dev libssl-dev python3 python3-setuptools \\" >&2
  echo "    rsync unzip zlib1g-dev file wget xsltproc swig time libelf-dev device-tree-compiler" >&2
  exit 1
fi
echo "    依赖齐全"

# ---------- 2. 拉源码 ----------
if [ -d "$SRC/.git" ]; then
  echo "[2/6] 源码已存在，跳过 clone: $SRC"
else
  echo "[2/6] 克隆源码（浅克隆，只取 $BRANCH）"
  git clone --branch "$BRANCH" --single-branch --depth=1 "$REPO" "$SRC"
fi
cd "$SRC"
echo "    版本: $(git log --oneline -1)"

# ---------- 3. feeds ----------
echo "[3/6] feeds update / install"
if [ "$FLAVOR" = "immortalwrt" ] && [ "$EXT4" != "skip-oaf" ]; then
  grep -q 'smallpack' feeds.conf.default 2>/dev/null || \
    echo "src-git smallpack https://github.com/kenzok8/small-package.git" >> feeds.conf.default
fi
./scripts/feeds update -a
# 第三方包单独先装（install -a 遍历 2000+ 包时排在末尾的 feed 状态不稳）
if grep -q 'smallpack' feeds.conf.default 2>/dev/null; then
  ./scripts/feeds install -f -p smallpack luci-app-oaf || true
  [ -e package/feeds/smallpack/luci-app-oaf ] || {
    echo "!! luci-app-oaf 未装上，可忽略（不影响其他包）" >&2
  }
fi
./scripts/feeds install -a

# ---------- 4. 补丁 + 校验 ----------
echo "[4/6] 打扩展分区补丁"
python3 "$ROOT/scripts/patch-wax206-full256.py" . "${PATCH_ARGS[@]}"
python3 "$ROOT/scripts/check-dts.py" \
  target/linux/mediatek/dts/mt7622-netgear-wax206.dts

# ---------- 5. 配置 ----------
echo "[5/6] 生成 .config"
cp "$CONFIG" .config
echo "CONFIG_LUCI_LANG_zh_Hans=y" >> .config

# ext4 根分区大小必须跟着 firmware 尺寸走，否则镜像超尺寸被 check-size 拦
if grep -q '^CONFIG_TARGET_ROOTFS_EXT4FS=y' "$CONFIG"; then
  sed -i "/^CONFIG_TARGET_ROOTFS_PARTSIZE=/d" .config
  echo "CONFIG_TARGET_ROOTFS_PARTSIZE=$PART_MIB" >> .config
fi

# packages.txt → CONFIG_PACKAGE_*
echo '### packages.txt' >> .config
while IFS= read -r line; do
  pkg="$(echo "$line" | sed -e 's/#.*//' -e 's/[[:space:]]//g')"
  [ -z "$pkg" ] && continue
  case "$pkg" in
    *=y|*=n|*=m) echo "CONFIG_PACKAGE_${pkg%%=*}=${pkg#*=}" >> .config ;;
    *)           echo "CONFIG_PACKAGE_${pkg}=y" >> .config ;;
  esac
done < "$ROOT/packages.txt"

make defconfig
echo "--- 关键配置 ---"
for o in TARGET_ROOTFS_SQUASHFS TARGET_ROOTFS_EXT4FS TARGET_ROOTFS_PARTSIZE USE_APK LUCI_LANG_zh_Hans; do
  printf '  %-28s %s\n' "$o" "$(grep -E "^CONFIG_${o}=" .config | head -1 || echo '(未设置)')"
done
sed -n '/define Device\/netgear_wax206/,/endef/p' target/linux/mediatek/image/mt7622.mk \
  | grep -E '^\s+(KERNEL_SIZE|IMAGE_SIZE)' | sed 's/^/  /'
echo "  FILESYSTEMS: $(grep -oP '^\s*FILESYSTEMS := \K.+' target/linux/mediatek/image/Makefile)"
echo "  包总数: $(grep -c '^CONFIG_PACKAGE_.*=y$' .config)"

# ---------- 6. 编译 ----------
echo "[6/6] 开始编译（4 核约 1.5~3 小时，失败可直接重跑本脚本续编）"
echo "     日志: $WORKDIR/build.log"
make -j"$JOBS" V=s 2>&1 | tee "$WORKDIR/build.log"

echo
echo "=========================================================="
echo " 编译完成，产物在:"
ls -lh bin/targets/mediatek/mt7622/*wax206* 2>/dev/null | awk '{print "   "$9"  "$5}'
echo "=========================================================="
rm -rf "$WORKDIR/release" 2>/dev/null || true
mkdir -p "$WORKDIR/release"
find bin/targets/mediatek/mt7622 -maxdepth 1 -type f \
  \( -name '*netgear_wax206*' -o -name '*wax206*' \) -exec cp {} "$WORKDIR/release/" \;
cd "$WORKDIR/release" && sha256sum * > SHA256SUMS
echo " 已收集到: $WORKDIR/release"
