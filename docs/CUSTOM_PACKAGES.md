# 在 WAX206 256M 固件里加自己的软件包：改造说明

> 结论先行：这个仓库**不需要改任何 py 脚本或上游源码**，加包只有一个入口——
> 把包名写进 config / 包清单。真正的坑不在"怎么写"，而在三处会让改动**静默失效**的地方，
> 都在本文里，并且已在本机 OpenWrt v25.12.5 源码上实测复现过。

---

## 一、原仓库是怎么决定"固件里有什么"的

`.github/workflows/build.yml` 的流程：

```
clone openwrt v25.12.5
feeds update -a          # 拉 5 个官方 feed
feeds install -a         # 把全部包"挂"进 package/feeds/（只是链接，不等于编进固件）
python3 scripts/patch-wax206-full256.py   # 只改 DTS 分区
cp config/wax206-25.12.5.config .config
make defconfig           # ← 决定"哪些包真的进固件"的地方
make -j$(nproc)
```

`config/wax206-25.12.5.config` 原本只有 5 行：

```
CONFIG_TARGET_mediatek=y
CONFIG_TARGET_mediatek_mt7622=y
CONFIG_TARGET_mediatek_mt7622_DEVICE_netgear_wax206=y
CONFIG_PACKAGE_luci=y
CONFIG_PACKAGE_luci-i18n-base-zh-cn=y
```

所以**加包 = 在这个 config 里加 `CONFIG_PACKAGE_<包名>=y`**。
`feeds install -a` 已经把几千个包都挂好了，不需要额外操作。

---

## 二、三处会让改动静默失效的坑（实测复现）

### 坑 1：包名写错不会报错，只会"什么也没发生"

`feeds install -a` 之后，写一个不存在的 `CONFIG_PACKAGE_xxx=y`，`make defconfig`
**不会报错**，只是把它丢掉。编译 40 分钟后你才会发现固件里没有。

→ 改造方案在 CI 里加了 `Resolve package list` 步骤：逐行 `grep -qx "Package: $pkg" feeds/*.index`，
不存在就 `::error::` 让 job 直接红。

### 坑 2：中文语言包写了 `=y` 也不生效（原仓库就有这个问题）

LuCI 语言包在 feed 索引里不是普通的包：

```
Package: luci-i18n-base-zh-cn
Default: LUCI_LANG_zh_Hans||(ALL&&m)
```

只有当 `CONFIG_LUCI_LANG_zh_Hans=y`（或开了 `ALL`）时它才是 `=y`；否则退化成 `=m`，
而**目标镜像不输出 m 包**，等于中文界面凭空消失。

实测：原版 config（只写 `CONFIG_PACKAGE_luci-i18n-base-zh-cn=y`）跑完 `defconfig` 后是
`# CONFIG_PACKAGE_luci-i18n-base-zh-cn is not set`。

→ 修法：config 里加一行 `CONFIG_LUCI_LANG_zh_Hans=y`。加上后 5 个中文语言包全部转 `=y`。

### 坑 3：只加包，172MB 空间用不上

25.12.5 默认：

```
CONFIG_TARGET_ROOTFS_SQUASHFS=y
# CONFIG_TARGET_ROOTFS_EXT4FS is not set
```

`mediatek/image/Makefile` 里 `FILESYSTEMS := squashfs erofs`，`include/image.mk` 里
`TARGET_FILESYSTEMS := $(fs-types-y)`，而 `fs-types-$(CONFIG_TARGET_ROOTFS_EXT4FS) += ext4`。
`Device/Build` 对 `(IMAGES × FILESYSTEMS)` 做笛卡尔积生成镜像——

- 不开 ext4：只出 `*-squashfs-sysupgrade.bin`，根文件系统只读，装东西全靠 `/overlay`
- 开 ext4：额外出 `*-ext4-sysupgrade.bin`，`TARGET_ROOTFS_PARTSIZE=448`（MiB 基数）可写

→ 想看效果就得开 `CONFIG_TARGET_ROOTFS_EXT4FS=y`，然后刷 `*-ext4-sysupgrade.bin`。

### 附带坑：25.12.5 用的是 apk，不是 opkg

`CONFIG_USE_APK=y`（默认）。设备上装包是 `apk add xxx`，网上大量 `opkg install` 的教程
在这个版本上直接报 `opkg: not found`。

---

## 三、改造后的文件结构

```
wax206-custom/
├── .github/workflows/build.yml      # 与原版差异仅 4 处（见下）
├── config/wax206-25.12.5.config     # 设备/文件系统/语言开关
├── packages.txt                     # ★ 你日常只需要改这个文件
├── scripts/gen-config.sh            # 本地预演：包名校验 + 生效核对，不编译
└── docs/CUSTOM_PACKAGES.md          # 本文
```

原仓库里的 `scripts/patch-wax206-full256.py` 保持原样，不动。

### 日常改包流程

```bash
vi packages.txt        # 加一行包名，如 luci-app-wol
bash scripts/gen-config.sh /path/to/openwrt   # 本地预演（需要已 update -a / install -a）
git commit -am "add luci-app-wol" && git push  # Actions 自动跑
```

`gen-config.sh` 会打印每个包 OK / ERROR，并统计固件内选中的包总数，
不需要真的编译就能知道"加进去了没有"。

### workflow 相对原版的 4 处改动

| # | 步骤 | 作用 |
|---|---|---|
| 1 | `Resolve package list` | `packages.txt` → `.config`；包名不存在就报错终止 |
| 2 | `FEEDS_INSTALL` 环境变量 | `all` = 全量安装（省事，索引慢）；`list` = 只装清单里的包（快） |
| 3 | `Verify enabled packages` | 从 `.config` 反查，勾了但被依赖裁掉的包会报错 |
| 4 | `Show effective filesystem flavors` | 打印 SQUASHFS/EXT4/APK 实际状态，刷错镜像前能看见 |

---

## 四、常用包名（已在 v25.12.5 官方 feed 中核实存在）

| 包名 | 用途 | 备注 |
|---|---|---|
| `luci-app-sqm` + `sqm-scripts` | 限速/QoS | 只有界面不会工作，必须同时装 sqm-scripts |
| `luci-app-ddns` | 动态域名 | |
| `luci-app-ttyd` | 网页终端 | |
| `luci-app-commands` | 自定义命令按钮 | |
| `luci-app-attendedsysupgrade` | 保留配置升级 | 配合自定义固件不如手动 sysupgrade 稳 |
| `adguardhome` | DNS 广告过滤 | **占 53 端口，和 dnsmasq 冲突**，装完要改其中一个 |
| `zerotier` | 异地组网 | |
| `vnstat` / `iperf3` | 流量统计 / 测速 | |
| `htop` / `nano` / `curl` | 系统工具 | curl 会顶掉 busybox 的 wget 习惯 |

### 明确不在官方 feed 里的（需要自定义 feed）

`luci-app-openclash`、`luci-app-passwall2`、`luci-app-mosdns`、`luci-app-nikki`、
`luci-app-homeproxy`、`luci-theme-argon`、`luci-app-argon-config`、`shellcrash`、
`luci-app-filetransfer`、`luci-app-diskman`。

要加这些，两个办法：

**A. 加第三方 feed（推荐，改动小）**

在 workflow 的 `feeds update` 之前插一步：

```yaml
- name: Add third-party feeds
  working-directory: ${{ env.OPENWRT_DIR }}
  run: |
    # 例子：kenzok8 小包合集（含 argon 主题、filetransfer、diskman 等）
    echo "src-git kenzok8 https://github.com/kenzok8/openwrt-packages.git" >> feeds.conf.default
    echo "src-git small8  https://github.com/kenzok8/small-package.git"    >> feeds.conf.default
```

代价：第三方 feed 的包名**不在 `feeds/*.index` 里就会出现校验误报**，所以
`Resolve package list` 的校验要放宽成"官方 feed 缺失时给 warning 而不是 error"，
或者把第三方包单独放进 `packages-thirdparty.txt`、单独校验。

**B. 直接往 `package/` 里塞源码**

```yaml
- name: Drop in local packages
  run: cp -a local-packages/* "${OPENWRT_DIR}/package/"
```

适合自己改过的包，但要注意第三方包对 kernel/工具链版本很敏感，
25.12.5 是较新的版本，2023 年左右的 luci-app 常有 API 不兼容问题。

---

## 五、验证记录（本机实测，OpenWrt v25.12.5 源码）

```
$ bash scripts/gen-config.sh /tmp/ow
=== 文件系统 / 打包器 ===
  TARGET_ROOTFS_SQUASHFS     CONFIG_TARGET_ROOTFS_SQUASHFS=y
  TARGET_ROOTFS_EXT4FS       CONFIG_TARGET_ROOTFS_EXT4FS=y
  USE_APK                    CONFIG_USE_APK=y
  TARGET_ROOTFS_PARTSIZE     CONFIG_TARGET_ROOTFS_PARTSIZE=448

=== packages.txt 生效情况 ===
  OK  luci / luci-i18n-base-zh-cn / luci-app-firewall / luci-app-sqm /
      luci-app-ddns / luci-app-ttyd / luci-app-commands /
      luci-app-attendedsysupgrade / luci-app-uhttpd /
      luci-i18n-sqm-zh-cn / luci-i18n-ddns-zh-cn / luci-i18n-ttyd-zh-cn /
      luci-i18n-commands-zh-cn / luci-i18n-firewall-zh-cn /
      sqm-scripts / zerotier / adguardhome / vnstat / iperf3 / curl / nano / htop
  固件内选中的包总数：218
  全部生效
```

对照实验（同一个源码目录，只换 config）：

| config | `CONFIG_TARGET_ROOTFS_EXT4FS` | 说明 |
|---|---|---|
| 原版 5 行 | `is not set` | 只出 squashfs 镜像 |
| 加 `LUCI_LANG_zh_Hans=y` 前 | 语言包 `is not set` | 中文界面丢失 |
| 加之后 | 全部 `=y`，共 218 个包 | 正常 |

**未验证**：本机没有跑完整 `make`（需要下载工具链和全部源码，数小时），
所以"ext4 镜像实际能不能刷、刷完 `/overlay` 是否真到 ~143MB"没有实测证据，
只在 make 逻辑层面确认了会生成 ext4 变体。这一步要等 Actions 跑完、你刷机后
`df -h` 才知道。
