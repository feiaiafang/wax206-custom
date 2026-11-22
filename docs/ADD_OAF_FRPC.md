# 添加软件包：OAF 与 frpc（含中文界面）

> 实测环境：ImmortalWrt `openwrt-25.12` 分支，本机跑通 feeds + defconfig。
> 结论：frpc 全在官方 feed，直接加包名即可；OAF 的界面在第三方 feed，
> 需要额外加一个源，但它的内核模块和用户态程序反而在官方 feed 里。

---

## 一、先分清每个包从哪来

这一节是重点。OAF 看起来是一个"包"，实际拆成 5 个，分属两个源：

| 包名 | 来源 | 作用 |
|---|---|---|
| `kmod-oaf` | 官方 `packages` | netfilter 内核模块（oaf.ko） |
| `appfilter` | 官方 `packages` | 用户态主程序 + UCI 配置名 |
| `luci-app-oaf` | **第三方** `kenzok8/small-package` | LuCI 界面 |
| `luci-i18n-oaf-zh-cn` | **第三方** 同上 | 中文界面 |
| `luci-compat` | 官方 `luci` | 旧 Lua 版 LuCI 兼容层 |
| `luci-lib-jsonc` | 官方 `luci` | JSON 库依赖 |

**关键判断**：`luci-app-oaf` 在 small-package 里是 **旧版 Lua 界面**
（目录结构是 `luasrc/controller/*.lua`，不是新版的 `htdocs/luci-static/resources/*.js`），
所以它必须靠 `luci-compat` 才能在 OpenWrt 25.x 的 JS 版 LuCI 上运行。
`luci-compat` 在官方 luci feed 里有，所以这条链走得通——已实测。

frpc 就简单了：

| 包名 | 来源 | 作用 |
|---|---|---|
| `frpc` | 官方 `packages` | 客户端本体（v0.66.0-r1） |
| `luci-app-frpc` | 官方 `luci` | LuCI 界面 |
| `luci-i18n-frpc-zh-cn` | 官方 `luci` | 中文界面 |

三个都在官方源，**不需要第三方 feed**。

---

## 二、packages.txt 里怎么加

已经写好了，照抄即可：

```
# ---- 内网穿透：frpc（官方 feed，自带中文界面）----
frpc
luci-app-frpc
luci-i18n-frpc-zh-cn

# ---- 上网行为管理：OAF ----
appfilter
kmod-oaf
luci-app-oaf
luci-i18n-oaf-zh-cn
luci-compat
luci-lib-jsonc
```

---

## 三、OAF 需要额外加 feed

CI 里加的这一步（`build-immortalwrt.yml` 的 `Add third-party feed (OAF)`）：

```yaml
- name: Add third-party feed (OAF)
  if: ${{ inputs.thirdparty_oaf != false }}
  working-directory: ${{ env.OPENWRT_DIR }}
  run: |
    echo "src-git smallpack https://github.com/kenzok8/small-package.git" \
      >> feeds.conf.default
```

**磁盘坑**：small-package 有 **17352 个文件**，全量 clone 会撑爆 GitHub
runner。本机实测第一次就撞上了 `fatal: write error: 超出磁盘配额`。

CI 里用 `feeds install -a` 是能过的（runner 有 14GB），但如果想省时间可以：

```bash
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/kenzok8/small-package.git feeds/smallpack
cd feeds/smallpack && git sparse-checkout set luci-app-oaf
```

本机就是这么做的，只拉了一个目录，423MB 的源码树里只多了几 MB。

---

## 四、一个会静默失败的坑（已加防护）

`scripts/feeds install -p smallpack luci-app-oaf` 时，**如果 feed 索引还没生成，
它只打印一行 WARNING 就返回 0**：

```
WARNING: No feed for package 'luci-app-oaf' found
```

然后 `package/feeds/smallpack/` 根本不存在，`make defconfig` 又会把
`CONFIG_PACKAGE_luci-app-oaf=y` 当不存在的包静默丢弃——最后编出来的固件
里没有 OAF，而且**全流程没有一个报错**。

本机第一次跑就是这样：8 个包活了 7 个，`luci-app-oaf` 显示"(根本没出现)"。

CI 里加了显式复核：

```bash
if [ ! -d package/feeds/smallpack/luci-app-oaf ]; then
  echo "::warning::luci-app-oaf 未通过 feeds install 安装，手动补装"
  ./scripts/feeds install -f -p smallpack luci-app-oaf
fi
test -d package/feeds/smallpack/luci-app-oaf \
  || { echo "::error::luci-app-oaf 仍然不在 package/feeds"; exit 1; }
```

---

## 五、中文界面怎么保证

两条独立的路，缺一不可：

1. **`CONFIG_LUCI_LANG_zh_Hans=y`** —— 语言包的 `Default` 是
   `LUCI_LANG_zh_Hans||(ALL&&m)`，不开这个开关它们只会是 `=m`，
   而目标镜像不输出 m 包（这个坑在 `CUSTOM_PACKAGES.md` 里详述）
2. **包名显式写进 packages.txt** —— 不依赖 Default 规则碰巧选中

实测最终 `.config` 里的中文包：

```
luci-i18n-base-zh-cn
luci-i18n-attendedsysupgrade-zh-cn
luci-i18n-commands-zh-cn
luci-i18n-ddns-zh-cn
luci-i18n-firewall-zh-cn
luci-i18n-frpc-zh-cn          ← frpc
luci-i18n-oaf-zh-cn           ← OAF（small-package 自带 po/zh_Hans）
luci-i18n-package-manager-zh-cn
luci-i18n-sqm-zh-cn
luci-i18n-ttyd-zh-cn
luci-i18n-uhttpd-zh-cn
```

`luci-app-oaf/po/zh_Hans` 是 small-package 里真实存在的目录（`po/zh-cn`
是指向它的符号链接），所以 OAF 菜单能显示中文。

---

## 六、验证记录（本机实测）

```
=== 包名校验 ===
  全部包名都在 feed 索引中

=== make defconfig 后逐包核对 ===
  OK  luci / luci-i18n-base-zh-cn / luci-app-firewall / luci-app-sqm
  OK  luci-app-ddns / luci-app-ttyd / luci-app-commands
  OK  luci-app-attendedsysupgrade / luci-app-uhttpd
  OK  luci-i18n-sqm-zh-cn / luci-i18n-ddns-zh-cn / luci-i18n-ttyd-zh-cn
  OK  luci-i18n-commands-zh-cn / luci-i18n-firewall-zh-cn
  OK  sqm-scripts / zerotier / adguardhome / vnstat / iperf3 / curl
  OK  frpc / luci-app-frpc / luci-i18n-frpc-zh-cn          ← 新增
  OK  appfilter / kmod-oaf / luci-app-oaf / luci-compat / luci-lib-jsonc  ← 新增
  OK  nano / htop

=== 关键项 ===
  TARGET_ROOTFS_EXT4FS    CONFIG_TARGET_ROOTFS_EXT4FS=y
  TARGET_ROOTFS_PARTSIZE  CONFIG_TARGET_ROOTFS_PARTSIZE=247
  IMAGE_SIZE := 259328k
  包总数: 282

全部通过
```

**未验证**：
- 没有真正 `make` 编译（需数小时），所以"oaf.ko 能否在 25.12 内核上编过"
  没有实测证据。`luci-app-oaf` 是 2023 年前后的旧 Lua 应用，25.12 内核较新，
  如果 kernel 编译阶段 oaf.ko 报错，需要打 small-package 里的
  `.github/diy/patches/oaf.patch`
- 界面能否正常打开未验证（需要刷机后进 LuCI 看菜单）

---

## 七、刷完怎么用

**frpc**：LuCI → 服务 → frp 客户端，填服务端地址/端口/token，
或用配置文件模式。命令行：`/etc/init.d/frpc start`。

**OAF**：LuCI → 网络 → 上网行为管理（OAF 菜单）。
首次启用要先在"高级设置"里开启，它会接管 netfilter 的流量识别。
注意它和 SQM、AdGuardHome 都动 netfilter/防火墙，装一起时留意规则顺序。
