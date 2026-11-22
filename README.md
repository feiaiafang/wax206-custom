# WAX206 OpenWrt 256M（自定义软件包版）

基于 [01CreateWorld/wax206-openwrt-256m-byChatGPT](https://github.com/01CreateWorld/wax206-openwrt-256m-byChatGPT)
改造：在保留原仓库"只改 WAX206 NAND 分区"的前提下，把软件包选择做成可在仓库里维护的清单。

## 和原仓库的区别

| | 原仓库 | 本仓库 |
|---|---|---|
| 加包方式 | 手动编辑 `config/*.config` | 编辑 `packages.txt` 一行一个包名 |
| 包名写错 | 静默忽略，编译照跑 | CI 直接报错终止 |
| 勾了但没生效 | 无感知 | CI 逐包核对并报错 |
| 根文件系统 | 只有 squashfs（只读） | 额外产出 ext4 变体可写 |
| 中文语言包 | 写了 `=y` 但实测不生效 | 补上 `CONFIG_LUCI_LANG_zh_Hans=y`，已实测生效 |
| 本地预演 | 无 | `scripts/gen-config.sh`，不编译即可核对 |

完整的改造说明、坑点和验证记录见 **[docs/CUSTOM_PACKAGES.md](docs/CUSTOM_PACKAGES.md)**。

## 用法

```bash
# 1. 改清单
vi packages.txt

# 2. 本地预演（可选，需要一份已 update 过的 OpenWrt 源码）
bash scripts/gen-config.sh /path/to/openwrt

# 3. 推送，在 Actions 里跑 "Build WAX206 OpenWrt 256M (custom packages)"
```

产物里会有两份 sysupgrade：

- `*-squashfs-sysupgrade.bin` — 原方案，根只读，省空间
- `*-ext4-sysupgrade.bin` — 根可写，能真正用上 172MB UBI

## 刷机

沿用原仓库流程（NMRPflash 刷 recovery → `sysupgrade -n`），**但注意**：

- recovery 镜像两版通用
- sysupgrade 要选对 flavor：想用满空间就刷 `*-ext4-sysupgrade.bin`
- 换文件系统属于改分区表语义，**必须 `-n` 不带配置**，刷完重新配置
- 刷完 `df -h` 确认 `/overlay` 大小；25.12.5 用 apk，装包是 `apk add` 不是 `opkg install`

## 不再踩的三个坑（都已实测）

1. `CONFIG_PACKAGE_<不存在的包>=y` 不会报错，`make defconfig` 直接吞掉
2. LuCI 中文语言包需要 `CONFIG_LUCI_LANG_zh_Hans=y`，否则退化成 `=m` 且不进镜像
3. `CONFIG_TARGET_ROOTFS_EXT4FS` 默认关闭，不开就只有只读 squashfs
