# ImmortalWrt 版：扩展分区 + 官方编译流程

> 目标：用 ImmortalWrt 官方源码流程（firmware-selector 背后那套）编出
> **firmware 分区吃到 NAND 末尾**的 WAX206 固件。
> 结论：可行，已在本机 immortalwrt `openwrt-25.12` 分支上实测打补丁 + defconfig 通过。

---

## 一、为什么不能直接用 firmware-selector

[firmware-selector.immortalwrt.org](https://firmware-selector.immortalwrt.org/) 是
**ImageBuilder**：它拿的是官方预编译好的 kernel + DTB，只负责往里塞包。

分区表写在 DTB 里（`mt7622-netgear-wax206.dts` 的 `partition@2c0000` 节点），
DTB 编译时就固化了。所以：

| 需求 | ImageBuilder | 源码编译 |
|---|---|---|
| 加点软件包 | ✅ | ✅ |
| 改 firmware 分区大小 | ❌ DTB 已固化 | ✅ |
| 改 kernel 6MiB 子分区 | ❌ | ✅ |
| 换 ext4 根文件系统 | ❌ | ✅ |

**要扩分区必须源码编译。**

---

## 二、ImmortalWrt 的 WAX206 分区表（比 OpenWrt 多 7 个分区）

实测 `target/linux/mediatek/dts/mt7622-netgear-wax206.dts`：

| 分区 | 起点 | 大小 | 说明 |
|---|---|---|---|
| Preloader | `0x0` | 512 KiB | 只读 |
| ATF | `0x80000` | 256 KiB | 只读 |
| Bootloader | `0xc0000` | 512 KiB | 只读 |
| Config | `0x140000` | 512 KiB | |
| Factory | `0x1c0000` | 1 MiB | 只读，含 MAC |
| **firmware** | `0x2c0000` | **38 MiB** | 内含 kernel 6MiB + ubi 32MiB |
| firmware_backup | `0x28c0000` | 38 MiB | 只读 |
| CFG | `0x4ec0000` | 8 MiB | 只读 |
| RAE | `0x56c0000` | 4 MiB | 只读 |
| POT | `0x5ac0000` | 1 MiB | 只读 |
| Language | `0x5bc0000` | 4 MiB | 只读 |
| Traffic | `0x5fc0000` | 2 MiB | 只读 |
| **Cert** | `0x61c0000` | 1 MiB | 只读 ← OpenWrt 没有 |
| **NTGRcryptK** | `0x62c0000` | 1 MiB | 只读 ← OpenWrt 没有 |
| **NTGRcryptD** | `0x63c0000` | 5 MiB | 只读 ← OpenWrt 没有 |
| **LOG** | `0x68c0000` | 1 MiB | 只读 ← OpenWrt 没有 |
| **User_data** | `0x69c0000` | 6.25 MiB | ← OpenWrt 没有 |
| *未分区* | `0x7000000` | 144 MiB | |

**关键差异**：ImmortalWrt 的分区一直排到 `0x7000000`（112 MiB），
OpenWrt 版脚本只删到 `firmware_backup` 之后那几个，且尺寸写死 178 MiB。
所以**必须重新算**，不能套用 OpenWrt 那份脚本。

---

## 三、脚本做了什么

`scripts/patch-immortalwrt-full256.py`（相对 OpenWrt 版重写）：

1. **尺寸可配**：`--size full` 吃满 NAND（253.2 MiB），或 `--size 178m` 沿用保守值
2. **动态解析**：先扫出所有顶层分区，算真实占用，不硬编码
3. **越界保护**：`--size 300m` 会算到 `0x12ec0000 > 0x10000000` 直接拒绝
4. **幂等**：跑两次第二次识别出"已无分区可删"，安全退出
5. **`--ext4`**：额外把 target 的 `FILESYSTEMS := squashfs erofs` 改成
   `ext4 squashfs erofs`，否则只开 `CONFIG_TARGET_ROOTFS_EXT4FS` 也不出 ext4 镜像
6. **后置校验**：写完重读文件，确认分区数、尺寸、残留都对

两种方案的数值：

| 方案 | firmware | kernel | ubi | IMAGE_SIZE | 尾部余量 |
|---|---|---|---|---|---|
| `--size full`（默认） | `0xfd40000` = 253.2 MiB | 6 MiB | **247.2 MiB** | `259328k` | 0 |
| `--size 178m`（保守，对齐 OpenWrt 版） | `0xb200000` = 178 MiB | 6 MiB | 172 MiB | `182272k` | 75 MiB |

---

## 四、踩过的两个坑（都已修）

### 坑 1：删除节点后留下一串孤立 `;`

DTS 节点写成 `partition@xxx { ... };`——**分号在右大括号外面**。
`node_range()` 只返回到 `}`，直接替换就漏掉分号，第一次运行后 DTS 里
出现了 12 个孤立的 `;` 行（`{`/`}` 仍然平衡，所以括号检查抓不到）。

修法：新增 `_skip_ws_and_semicolon()`，删除和替换都把范围延伸到分号之后。

### 坑 2：用正则猜括号深度会删错节点

第一版 `direct_partition_nodes()` 用 `re.finditer` 遍历然后累计 `{`/`}` 数量
推深度——一旦前面有节点被删或夹了注释，深度就漂移，误判谁是深度 1。

修法：改成"逐个跳节点"——用 `node_range` 拿到一个节点的完整范围后直接
跳到它的末尾继续扫，天然只命中直接子节点。

---

## 五、用哪个脚本

| 场景 | 用这个 |
|---|---|
| 固件来自 `immortalwrt.git` | `scripts/patch-immortalwrt-full256.py` |
| 固件来自 `openwrt.git`（原仓库） | `scripts/patch-wax206-full256.py`（原样保留，未改） |

顺带实测：原 OpenWrt 版脚本**也能**打在 immortalwrt 源码上（路径和锚点一致），
但只扩到 178 MiB，且不会删除 ImmortalWrt 特有的 `Cert`/`LOG`/`User_data` 等
分区——那些会落在扩大后的 firmware 范围里造成重叠。所以 ImmortalWrt 请用新脚本。

---

## 六、CI 流程

`.github/workflows/build-immortalwrt.yml`：

```yaml
workflow_dispatch 输入：
  fw_size: full | 178m | 任意 MiB
  build_ext4: true/false
```

步骤：clone immortalwrt → feeds → **打补丁** → **DTS 结构校验** → defconfig →
包生效核对 → make → **DTB 硬断言**（确认 `partition@28c0000` 和 `label="User_data"`
都不在编译产物里）→ 收集产物。

最后那步 DTB 断言是关键：配置文件改得再对，只要 DTB 里还是老布局，
刷上去就是白干。用 `dtc -I dtb -O dts` 反编译后 grep，能真正验到"编进去的分区表"。

---

## 七、刷机

**必须先刷 recovery**，因为分区表本身变了：

1. 刷 `*-recovery.itb`（NMRPflash 或原厂 web UI）
2. SSH `root@192.168.1.1`，`cat /proc/mtd` 确认 `firmware` 是 `0xb200000`
   或 `0xfd40000`（取决于你选的 size）
3. `scp *-ext4-sysupgrade.bin /tmp/`，然后 `sysupgrade -n /tmp/xxx.bin`
4. 重启后 `df -h` 看 `/overlay`

**不要**从旧的 38MiB 固件直接 sysupgrade，分区表不兼容。

---

## 八、验证记录（本机实测）

环境：Ubuntu，immortalwrt `openwrt-25.12` 分支，本机 clone 后跑通配置阶段。

```
=== 打补丁 (full + ext4) ===
  ubi     0x600000 + 0xf740000  (247.2 MiB)
  firmware 结束于 0x10000000，NAND 末尾 0x10000000，余量 0.0 MiB
  删除的顶层分区: User_data, LOG, NTGRcryptD, NTGRcryptK, Cert,
                  Traffic, Language, POT, RAE, CFG, firmware_backup
  IMAGE_SIZE := 259328k  (253.2 MiB)

=== DTS 结构校验 ===
  ok    括号平衡 (96 对)
  ok    无孤立分号
  ok    所有 partition 节点都带 label 与 reg（共 8 个）
  ok    firmware: 0x2c0000 + 0xfd40000 = 253.2 MiB
  ok    无残留厂商分区
  ok    Preloader/ATF/Bootloader/Config/Factory 都完整保留在 firmware 之前
  结果: 全部通过
```

尺寸分支：

| `--size` | firmware | IMAGE_SIZE | DTS 校验 |
|---|---|---|---|
| `full` | 253.2 MiB | `259328k` | 通过 |
| `178m` | 178.0 MiB | `182272k` | 通过 |
| `128m` | 128.0 MiB | `131072k` | 通过 |
| `300m` | — | — | 越界拒绝 |

**未验证**：
- 完整 `make` 未在本机跑（需数小时下载工具链），因此"ext4 镜像实际尺寸是否
  被 check-size 拦"、"刷完 `/overlay` 是不是 247MiB"没有实测证据
- 本机无内核源码树，`dtc` 无法展开 `#include "mt7622.dtsi"`，所以 DTS 用
  自写结构校验器代替；CI 里有 `device-tree-compiler`，会在编译后对真实 DTB 断言
