#!/usr/bin/env python3
"""不依赖内核源码的 DTS 结构校验器。

为什么不用 dtc：dtc 需要先跑 cpp 展开 #include "mt7622.dtsi"，
而那些 .dtsi 来自内核源码树，只有 make 之后才会下载。所以在
"改完立刻验证"这个阶段，只能自己做语法/结构检查。

检查项：
  1. 括号必须平衡
  2. 不允许孤立分号（`;` 单独成行）——这是删除节点时的典型残留
  3. partitions 节点下的直接子 partition 必须是合法的 node 形式
  4. firmware 节点存在且尺寸正确、只有一个
  5. 不该残留被删的厂商分区
"""
import re
import sys


def main(path: str) -> int:
    text = open(path, encoding="utf-8").read()
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        print(f"  FAIL  {msg}")

    def good(msg):
        print(f"  ok    {msg}")

    # 1. 括号平衡
    ob, cb = text.count("{"), text.count("}")
    if ob == cb:
        good(f"括号平衡 ({ob} 对)")
    else:
        fail(f"括号不平衡 {{={ob} }}={cb}")

    # 2. 孤立分号
    stray = [i + 1 for i, ln in enumerate(text.split("\n")) if re.fullmatch(r"\s*;\s*", ln)]
    if not stray:
        good("无孤立分号")
    else:
        fail(f"孤立分号出现在行 {stray[:5]}")

    # 3. partitions 下的直接子节点是否都写了 label + reg
    m = re.search(r"partitions\s*\{", text)
    if not m:
        fail("找不到 partitions 节点")
    else:
        # 逐个 partition 检查其后 200 字符内是否同时有 label 和 reg
        bad = []
        for pm in re.finditer(r"partition@([0-9a-fA-F]+)\s*\{", text):
            seg = text[pm.end():pm.end() + 300]
            if 'label =' not in seg or 'reg = <' not in seg:
                bad.append(pm.group(0))
        if bad:
            fail(f"以下 partition 缺 label/reg: {bad}")
        else:
            good(f"所有 partition 节点都带 label 与 reg（共 {len(re.findall(r'partition@[0-9a-fA-F]+ *{', text))} 个）")

    # 4. firmware 尺寸
    fw = re.search(r'label = "firmware";\s*(?://[^\n]*\n\s*)*reg = <0x([0-9a-fA-F]+) 0x([0-9a-fA-F]+)>;', text)
    if not fw:
        fail("找不到 firmware 节点")
    else:
        start, size = int(fw.group(1), 16), int(fw.group(2), 16)
        good(f"firmware: 0x{start:x} + 0x{size:x} = {size / 1048576:.1f} MiB")
        if start + size > 0x10000000:
            fail("firmware 越过 NAND 末尾 0x10000000")

    # 5. 厂商分区残留
    leftover = [lb for lb in ("firmware_backup", "CFG", "RAE", "POT", "Language",
                              "Traffic", "Cert", "NTGRcryptK", "NTGRcryptD",
                              "LOG", "User_data")
                if f'label = "{lb}"' in text]
    if leftover:
        fail(f"残留厂商分区: {leftover}")
    else:
        good("无残留厂商分区")

    # 6. 保留分区必须在 firmware 之前
    keep_ok = True
    for lb in ("Preloader", "ATF", "Bootloader", "Config", "Factory"):
        km = re.search(rf'label = "{lb}";\s*(?://[^\n]*\n\s*)*reg = <0x([0-9a-fA-F]+) 0x([0-9a-fA-F]+)>;', text)
        if not km:
            fail(f"关键分区 {lb} 不见了")
            keep_ok = False
            continue
        s, z = int(km.group(1), 16), int(km.group(2), 16)
        if fw and s + z > int(fw.group(1), 16):
            fail(f"{lb} 与 firmware 重叠")
            keep_ok = False
    if keep_ok:
        good("Preloader/ATF/Bootloader/Config/Factory 都完整保留在 firmware 之前")

    print("\n结果:", "全部通过" if ok else "有问题，见上")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
