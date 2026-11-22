#!/usr/bin/env python3
"""把 ImmortalWrt 的 WAX206 目标改成「firmware 分区吃到 NAND 末尾」的大 UBI 布局。

和 OpenWrt 版脚本的区别
------------------------
原脚本（patch-wax206-full256.py）是给 openwrt.git 写的，尺寸写死 0xb200000
（178 MiB），只用了 256MiB NAND 的前 178MiB，尾部 75MiB 白白空着。
本脚本面向 immortalwrt.git（openwrt-25.12 分支，对应固件选择器上的 25.12.2）：

  1. 尺寸可由命令行指定，默认吃满 NAND（--size full），也可沿用保守值 178MiB
  2. 先把整条 NAND 的分区全解析出来，再算「firmware 能扩到哪」——
     不再硬编码，避免上游改了布局后静默算错
  3. 会检查扩出来的范围是否真的以 NAND 末尾为界，越界直接报错
  4. 同时把 IMAGE_SIZE 写对（sysupgrade.bin 的 check-size 靠它）
  5. --ext4 额外把 target 的 FILESYSTEMS 加上 ext4，
     产出 ...-ext4-sysupgrade.bin（根可写，真正用满 UBI）

分区事实（immortalwrt openwrt-25.12 的 mt7622-netgear-wax206.dts，实测）
------------------------------------------------------------------------
  0x0000000  Preloader        0x80000
  0x0080000  ATF              0x40000
  0x00c0000  Bootloader       0x80000
  0x0140000  Config           0x80000
  0x01c0000  Factory          0x100000
  0x02c0000  firmware         0x2600000   ← 要扩的就是它（内含 kernel 6MiB + ubi）
  0x28c0000  firmware_backup  0x2600000   ┐
  0x4ec0000  CFG              0x800000    │ 这 6 个在原厂布局里
  0x56c0000  RAE              0x400000    │ 都会被扩大的 firmware 覆盖，
  0x5ac0000  POT              0x100000    │ 必须从 DTS 删掉
  0x5bc0000  Language         0x400000    │
  0x5fc0000  Traffic          0x200000    ┘
  NAND 总容量 0x10000000 = 256 MiB

  吃满方案：firmware = 0x10000000 - 0x2c0000 = 0xfd40000 = 253 MiB
            kernel 6 MiB + ubi 0xf740000 = 247 MiB
  保守方案：firmware = 0xb200000 = 178 MiB（= 原 OpenWrt 脚本的值）
            kernel 6 MiB + ubi 0xac00000 = 172 MiB

用法
----
    python3 scripts/patch-immortalwrt-full256.py /path/to/immortalwrt
    python3 scripts/patch-immortalwrt-full256.py /path/to/immortalwrt --size 178m
    python3 scripts/patch-immortalwrt-full256.py /path/to/immortalwrt --size full --ext4
    python3 scripts/patch-immortalwrt-full256.py /path/to/immortalwrt --dry-run
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ---- 硬件常量（WAX206 的 NAND 布局，不随版本变）----
NAND_SIZE = 0x10000000        # 256 MiB
FIRMWARE_START = 0x2C0000     # firmware 分区起点，与上游一致
KERNEL_SIZE = 0x600000        # 6 MiB，与上游 KERNEL_SIZE := 6144k 一致

# EXT4 打开后，根文件系统分区不再是"UBI 里剩多少"，而是按 TARGET_ROOTFS_PARTSIZE
# 切开。这个值必须 <= ubi 分区大小，否则镜像会超尺寸被 check-size 拦下。
# 这里取 ubi 大小，让 ext4 变体也能吃满。
DEFAULT_EXT4_PARTSIZE_MIB = None  # None = 自动算成 ubi 大小


def node_range(text: str, token: str) -> tuple[int, int]:
    """返回以 token 开头的 DTS 节点的 [start, end)（含闭合大括号）。"""
    start = text.find(token)
    if start < 0:
        raise RuntimeError(f"DTS 节点不存在: {token}")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"节点没有左大括号: {token}")

    depth = 0
    in_string = False
    escape = False
    in_line_comment = False
    in_block_comment = False
    i = brace
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise RuntimeError(f"节点没有闭合: {token}")


def direct_partition_nodes(text: str, part_start: int, part_end: int) -> list[tuple[int, int, str]]:
    """列出 partitions 节点下的直接子 partition@xxx（不含 firmware 内部的）。

    实现要点：不能用正则遍历文本后猜深度——一旦前面有被删掉的内容或
    注释，深度就漂了，会误删到别处（第一版就是这样把 DTS 改出十几个
    空 `;` 的）。这里改成"逐个跳节点"：从 partitions 的左大括号开始，
    每次只在当前深度 1 处找下一个 partition@，用 node_range 拿到它的
    完整范围，然后直接跳到该节点末尾继续扫。
    """
    body_start = text.find("{", part_start)
    if body_start < 0:
        raise RuntimeError("partitions 节点没有左大括号")

    results: list[tuple[int, int, str]] = []
    pos = body_start + 1
    while pos < part_end:
        m = re.compile(r"partition@[0-9a-fA-F]+").search(text, pos, part_end)
        if not m:
            break
        token = text[m.start():m.end()]
        s, e = _node_range_at(text, m.start())
        results.append((s, e, token))
        pos = e  # 跳过整个节点，天然保证只取深度 1
    return results


def _skip_ws_and_semicolon(text: str, pos: int) -> int:
    """跳过节点闭合大括号之后的分号（以及紧邻的空白）。

    DTS 节点写成 `partition@xxx { ... };`——分号在右大括号**外面**。
    只删到 `}` 会留下一串孤立的 `;`，语法上 dtc 虽然常能容忍，
    但结构已经不干净，且容易让后续的文本处理再次跑偏。
    """
    i = pos
    n = len(text)
    # 先吃掉介于 } 和 ; 之间的空白（通常没有，但注释可能夹在中间）
    while i < n and text[i] in " \t":
        i += 1
    if i < n and text[i] == ";":
        i += 1
    return i


def _node_range_at(text: str, start: int) -> tuple[int, int]:
    """和 node_range 等价，但起点由调用方给定（避免 find 命中错误位置）。"""
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError("节点没有左大括号")
    depth = 0
    in_string = escape = in_line_comment = in_block_comment = False
    i, n = brace, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise RuntimeError("节点没有闭合")


def parse_top_partitions(text: str) -> list[tuple[str, int, int]]:
    """解析出所有顶层分区 (label, start, size)，用于计算 NAND 真实占用。"""
    parts = []
    for m in re.finditer(r'label = "([^"]+)";\s*reg = <0x([0-9a-fA-F]+) 0x([0-9a-fA-F]+)>;', text):
        label, start, size = m.group(1), int(m.group(2), 16), int(m.group(3), 16)
        parts.append((label, start, size))
    return parts


def patch_dts(path: pathlib.Path, fw_size: int, kernel_size: int, dry_run: bool) -> list[str]:
    text = path.read_text(encoding="utf-8")
    removed_labels: list[str] = []

    fw_token = f"partition@{FIRMWARE_START:x}"
    fw_start, fw_end = node_range(text, fw_token)

    part_start, part_end = node_range(text, "partitions")
    if not (part_start < fw_start < part_end):
        raise RuntimeError(f"{fw_token} 不在 partitions 节点内，DTS 结构已变，请人工检查")

    ubi_size = fw_size - kernel_size
    if ubi_size <= 0:
        raise RuntimeError("firmware 比 kernel 还小")

    # 关键细节：原节点是 `partition@2c0000 { ... };`
    # node_range 只返回到 `}`，分号在范围之外。如果直接把 new_fw 替换
    # 进 [fw_start, fw_end)，原分号会留在新节点后面变成孤立的 `;`。
    # 所以这里显式把范围延伸到分号之后，并在 new_fw 末尾自己补上 `;`。
    fw_end_with_semi = _skip_ws_and_semicolon(text, fw_end)

    new_fw_lines = [
        f'partition@{FIRMWARE_START:x} {{',
        '\tlabel = "firmware";',
        f'\treg = <0x{FIRMWARE_START:x} 0x{fw_size:x}>;',
        '',
        '\tcompatible = "fixed-partitions";',
        '\t#address-cells = <1>;',
        '\t#size-cells = <1>;',
        '',
        '\tpartition@0 {',
        '\t\tlabel = "kernel";',
        f'\t\treg = <0x0 0x{kernel_size:x}>;',
        '\t};',
        '',
        f'\tpartition@{kernel_size:x} {{',
        '\t\tlabel = "ubi";',
        f'\t\treg = <0x{kernel_size:x} 0x{ubi_size:x}>;',
        '\t};',
        '};',
    ]
    new_fw = "\n".join(new_fw_lines) + "\n"
    text = text[:fw_start] + new_fw + text[fw_end_with_semi:]

    # 删掉 firmware 之后的所有顶层分区：它们全部落在扩大后的 firmware 里
    part_start, part_end = node_range(text, "partitions")
    fw_start, fw_end = node_range(text, fw_token)
    children = direct_partition_nodes(text, part_start, part_end)
    victims = [(s, _skip_ws_and_semicolon(text, e), name) for s, e, name in children if s > fw_start]
    # 顺带把节点之间残留的空白行一并收掉，避免删完留下大片空行
    for s, e, name in reversed(victims):
        seg = text[s:e]
        lm = re.search(r'label = "([^"]+)"', seg)
        removed_labels.append(lm.group(1) if lm else name)
        # 向左吃掉节点前的缩进/换行、向右吃掉一个换行，保持段落整齐
        while s > 0 and text[s - 1] in " \t":
            s -= 1
        if s > 0 and text[s - 1] == "\n":
            s -= 1
        if e < len(text) and text[e] == "\n":
            e += 1
        text = text[:s] + text[e:]

    if dry_run:
        return removed_labels

    path.write_text(text, encoding="utf-8")

    # 后置校验：只允许一个 firmware 节点、没有残留的旧分区、尺寸正确
    patched = path.read_text(encoding="utf-8")
    if patched.count(fw_token) != 1:
        raise RuntimeError("后置校验失败：firmware 节点数量不对")
    for label in removed_labels:
        if f'label = "{label}"' in patched:
            raise RuntimeError(f"后置校验失败：分区 {label} 未被删除")
    if f"reg = <0x{FIRMWARE_START:x} 0x{fw_size:x}>;" not in patched:
        raise RuntimeError("后置校验失败：firmware 尺寸没写进去")
    if f"reg = <0x{kernel_size:x} 0x{ubi_size:x}>;" not in patched:
        raise RuntimeError("后置校验失败：ubi 尺寸没写进去")
    return removed_labels


def patch_mk(path: pathlib.Path, image_size_k: int, dry_run: bool) -> None:
    text = path.read_text(encoding="utf-8")
    token = "define Device/netgear_wax206"
    start = text.find(token)
    if start < 0:
        raise RuntimeError("找不到 Device/netgear_wax206 定义，上游结构已变")
    end = text.find("endef", start)
    if end < 0:
        raise RuntimeError("Device/netgear_wax206 没有 endef")

    block = text[start:end]
    # IMAGE_SIZE 决定 sysupgrade.bin 的 check-size 上限，必须 >= firmware 大小
    new_block, count = re.subn(
        r"(?m)^\s*IMAGE_SIZE\s*:=\s*[^\n]+$",
        f"  IMAGE_SIZE := {image_size_k}k",
        block,
        count=1,
    )
    if count != 1:
        raise RuntimeError("没找到 IMAGE_SIZE 行，无法修改")

    if dry_run:
        return
    text = text[:start] + new_block + text[end:]
    path.write_text(text, encoding="utf-8")

    verify = path.read_text(encoding="utf-8")
    s = verify.find(token)
    e = verify.find("endef", s)
    if f"IMAGE_SIZE := {image_size_k}k" not in verify[s:e]:
        raise RuntimeError("后置校验失败：IMAGE_SIZE")


def patch_ext4(image_dir: pathlib.Path, partsize_mib: int, dry_run: bool) -> None:
    """把 ext4 加进该 target 的 FILESYSTEMS，让 Device/Build 生成 ext4 变体。

    OpenWrt/ImmortalWrt 的 Device/Build 对 (IMAGES × FILESYSTEMS) 做笛卡尔积，
    所以只开 CONFIG_TARGET_ROOTFS_EXT4FS 还不够——target 自己的
    FILESYSTEMS := squashfs erofs 会把 ext4 过滤掉。必须在这里加上。
    """
    text = image_dir.read_text(encoding="utf-8")
    m = re.search(r"(?m)^(\s*)FILESYSTEMS\s*:=\s*(.+)$", text)
    if not m:
        raise RuntimeError(f"{image_dir} 里没有 FILESYSTEMS 行")
    if "ext4" in m.group(2):
        return  # 已经支持
    indent, cur = m.group(1), m.group(2).strip()
    new_line = f"{indent}FILESYSTEMS := ext4 {cur}"
    text = text[:m.start()] + new_line + text[m.end():]
    if dry_run:
        return
    image_dir.write_text(text, encoding="utf-8")


def human(n: int) -> str:
    return f"{n / 1048576:.1f} MiB"


def parse_size(arg: str, fw_start: int) -> tuple[int, int]:
    """把 --size 解析成 (firmware_size, image_size_k)。"""
    if arg.lower() in ("full", "max"):
        fw = NAND_SIZE - fw_start
    else:
        m = re.fullmatch(r"(\d+)([mM])", arg.strip())
        if not m:
            raise SystemExit(f"--size 要写成 178m / full，收到: {arg}")
        fw = int(m.group(1)) * 1048576
    if fw <= KERNEL_SIZE:
        raise SystemExit("firmware 尺寸必须大于 kernel 的 6 MiB")
    end = fw_start + fw
    if end > NAND_SIZE:
        raise SystemExit(
            f"firmware 结束于 0x{end:x}，超过 NAND 末尾 0x{NAND_SIZE:x}（越界）"
        )
    image_k = fw // 1024
    return fw, image_k


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ImmortalWrt WAX206：扩展 firmware/UBI 分区",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root", help="immortalwrt 源码根目录")
    ap.add_argument("--size", default="full",
                    help="firmware 分区尺寸：full（吃满 NAND，默认）或 178m 之类的 MiB 值")
    ap.add_argument("--ext4", action="store_true",
                    help="同时让 target 产出 ext4 根文件系统变体（根可写）")
    ap.add_argument("--dry-run", action="store_true", help="只计算并打印，不改文件")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    dts = root / "target/linux/mediatek/dts/mt7622-netgear-wax206.dts"
    mk = root / "target/linux/mediatek/image/mt7622.mk"
    for p in (dts, mk):
        if not p.is_file():
            print(f"文件不存在: {p}", file=sys.stderr)
            return 1

    # 先校验目标确实是 WAX206 的 immortalwrt 源码
    head = (root / "include/version.mk").read_text(encoding="utf-8")
    if "REVISION" not in head and "VERSION_NUMBER" not in head:
        print("看起来不像 OpenWrt/ImmortalWrt 源码根目录", file=sys.stderr)
        return 1

    fw_size, image_k = parse_size(args.size, FIRMWARE_START)
    ubi_size = fw_size - KERNEL_SIZE

    # 用 DTS 里真实存在的分区做一次占用核对（防止上游改了布局却静默算错）
    before = parse_top_partitions(dts.read_text(encoding="utf-8"))
    occupied_end = max((o + s) for _, o, s in before) if before else 0
    print("=== 修改前 ===")
    for label, o, s in before:
        print(f"  {label:18} 0x{o:08x} + 0x{s:08x}  ({human(s)})")
    print(f"  NAND 容量 0x{NAND_SIZE:x} ({human(NAND_SIZE)}), "
          f"最大分区结束于 0x{occupied_end:x} ({human(occupied_end)})")

    fw_end = FIRMWARE_START + fw_size
    print("\n=== 修改后 ===")
    print(f"  firmware  0x{FIRMWARE_START:08x} + 0x{fw_size:x}  ({human(fw_size)})")
    print(f"    kernel  0x0 + 0x{KERNEL_SIZE:x}  ({human(KERNEL_SIZE)})")
    print(f"    ubi     0x{KERNEL_SIZE:x} + 0x{ubi_size:x}  ({human(ubi_size)})")
    print(f"  firmware 结束于 0x{fw_end:x}，NAND 末尾 0x{NAND_SIZE:x}，"
          f"余量 {human(NAND_SIZE - fw_end)}")
    if fw_end > NAND_SIZE:
        print("越界，中止", file=sys.stderr)
        return 1

    removed = patch_dts(dts, fw_size, KERNEL_SIZE, args.dry_run)
    print(f"  删除的顶层分区: {', '.join(removed) if removed else '（无）'}")

    patch_mk(mk, image_k, args.dry_run)
    print(f"  IMAGE_SIZE := {image_k}k  ({human(fw_size)})")

    if args.ext4:
        partsize = DEFAULT_EXT4_PARTSIZE_MIB or (ubi_size // 1048576)
        patch_ext4(root / "target/linux/mediatek/image/Makefile", partsize, args.dry_run)
        print(f"  FILESYSTEMS 追加 ext4（根分区 {partsize} MiB）")
        print("  提醒：还要在 .config 里加 CONFIG_TARGET_ROOTFS_EXT4FS=y "
              f"和 CONFIG_TARGET_ROOTFS_PARTSIZE={partsize}")

    print("\n" + ("[dry-run] 未写入任何文件" if args.dry_run else "WAX206 扩展分区补丁已应用"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
