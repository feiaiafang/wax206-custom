# 本地编译 WAX206 固件

CI 跑 GitHub Actions 不占你本机资源，但排队、调试、改一行看效果都慢。
下面是在本机直接编的完整做法，流程与 CI 完全一致。

---

## 一、一条命令

```bash
cd ~/wax206-custom
bash scripts/build-local.sh immortalwrt
```

默认参数：ImmortalWrt `openwrt-25.12`、firmware 吃满 253 MiB、产出 ext4 镜像。
脚本会走完 6 步并打印每一步结果：

```
[0/6] 磁盘检查
[1/6] host 依赖检查
[2/6] clone 源码
[3/6] feeds update / install
[4/6] 打扩展分区补丁 + DTS 结构校验
[5/6] 生成 .config + defconfig
[6/6] make -j$(nproc)
```

产物：
- `~/wax206-build/immortalwrt/bin/targets/mediatek/mt7622/*wax206*`
- 汇总副本：`~/wax206-build/release/`（含 `SHA256SUMS`）
- 编译日志：`~/wax206-build/build.log`

### 参数

```bash
bash scripts/build-local.sh <源码> <尺寸> <ext4>
```

| 参数 | 取值 | 说明 |
|---|---|---|
| 源码 | `immortalwrt`（默认） / `openwrt` | OpenWrt 版用 `v25.12.5` |
| 尺寸 | `full`（默认） / `178m` / `128m` … | firmware 分区大小 |
| ext4 | `ext4`（默认） / `noext4` | 是否产出可写根镜像 |

环境变量：

```bash
WORKDIR=/data/build JOBS=8 bash scripts/build-local.sh immortalwrt
```

---

## 二、本机实测环境（这台机器）

```
CPU     : 4 核
内存    : 7.2 GiB
磁盘    : / 剩余 191 GB（/tmp 只有 3.6 GB 且是 tmpfs）
```

**关键约束：不要把源码放 `/tmp`。** 这台机器的 `/tmp` 是 tmpfs（内存盘），
只有 3.6 GB，而一次完整编译需要 **25~35 GB**。脚本会检测并警告，
默认把 `WORKDIR` 放在 `~/wax206-build`。

4 核编译耗时参考：**1.5~3 小时**（首次，含下载工具链）。
第二次改配置重编通常 10~30 分钟。

### host 依赖（已在本机装好）

```bash
sudo apt-get install -y build-essential clang flex bison g++ gawk gcc-multilib \
  g++-multilib gettext git libncurses-dev libssl-dev python3 python3-setuptools \
  rsync unzip zlib1g-dev file wget xsltproc swig time libelf-dev device-tree-compiler
```

脚本 `[1/6]` 会检查 `bison/flex/xsltproc/swig` 和
`ncurses.h/openssl/ssl.h/zlib.h/libelf.h`，缺了直接给出上面这条命令。

---

## 三、脚本做了什么（与 CI 一一对应）

| 步骤 | 本地脚本 | CI |
|---|---|---|
| clone | `git clone --depth=1 -b openwrt-25.12` | 同 |
| 第三方 feed | 追加 `smallpack` 到 `feeds.conf.default` | 同 |
| feeds 顺序 | `update -a` → **单独装 smallpack/oaf** → `install -a` | 同 |
| 分区补丁 | `patch-wax206-full256.py --size full --ext4` | 同 |
| DTS 校验 | `check-dts.py` | 同 |
| 配置 | config + `LUCI_LANG_zh_Hans` + packages.txt → `defconfig` | 同 |

**为什么要单独先装 smallpack 的包**：`install -a` 要遍历 2000+ 个包，
smallpack 排在 `feeds.conf` 最后，CI 上出现过它被跳过、随后单独 install 又报
`No feed for package` 的情况。先单独装掉最稳（详见 `docs/ADD_OAF_FRPC.md`）。

---

## 四、常见操作

### 只改 packages.txt 后重编

```bash
# 编辑 packages.txt
bash scripts/build-local.sh immortalwrt    # 源码已存在，会跳过 clone
```

`[2/6]` 检测到 `$SRC/.git` 就跳过克隆，`[3/6]` 重新装 feeds。
改动小的话整个流程 10 分钟出镜像。

### 编译失败后续编

直接重跑同一条命令。OpenWrt 的 make 是增量的，已编译的包不会重来。
单包失败时可以先单独编那一个看详细错误：

```bash
cd ~/wax206-build/immortalwrt
make package/feeds/smallpack/luci-app-oaf/compile V=s
```

### 想改某个包的源码

```bash
cd ~/wax206-build/immortalwrt
rm -rf build_dir/target-*/luci-app-oaf*      # 清掉那一个包的构建目录
make package/feeds/smallpack/luci-app-oaf/compile V=s
```

### 只生成配置、不编译（看菜单）

```bash
cd ~/wax206-build/immortalwrt
make menuconfig
```

⚠️ `menuconfig` 里**不要动** `Target Images → ext4` 和
`Image Configuration → Root filesystem partition size`，
它们由补丁和脚本设定，改了会导致镜像超尺寸或被 check-size 拦。

### 清理

```bash
cd ~/wax206-build/immortalwrt
make clean       # 清产物，保留工具链（省时间）
make dirclean    # 清工具链，回到刚 clone 的状态
```

---

## 五、本机验证记录

`bash scripts/build-local.sh immortalwrt full ext4` 实际跑通前 5 步：

```
[0/6] 磁盘可用 191 GB
[1/6] 依赖齐全
[2/6] 版本: openwrt-25.12
[3/6] feeds OK（smallpack 单独装 oaf 成功）
[4/6] firmware 0x2c0000 + 0xfd40000 (253.2 MiB)
      删除 11 个顶层分区
      DTS 校验：全部通过
[5/6] TARGET_ROOTFS_EXT4FS=y  PARTSIZE=247  IMAGE_SIZE=259328k
      FILESYSTEMS: ext4 squashfs erofs   包总数 292
[6/6] 进入 make，正常编译中
```

**未验证**：本机 4 核跑完整编译需 1.5~3 小时，没有跑到底，
所以"最终镜像大小是否被 check-size 接受""ext4 刷机后 /overlay 是否 247 MiB"
仍无实测证据。这两点要等你本机编完或 CI 跑完、刷机后 `df -h` 才能确认。

---

## 六、本机 vs CI 该怎么选

| | 本地 | GitHub Actions |
|---|---|---|
| 耗时 | 1.5~3 小时（4 核，占满 CPU） | 1~1.5 小时（4 核 runner），要排队 |
| 调试 | 快，日志就在手边 | 改一次推一次，一轮十几分钟 |
| 占用 | 磁盘 30 GB + CPU 满载 | 不占本机 |
| 适合 | 反复试包、看报错细节 | 出正式固件、留档 |

建议：**先用 CI 编出能刷的固件确认方案可行**，之后再本地迭代微调。
