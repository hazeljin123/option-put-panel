#!/usr/bin/env python3
# CME 公开 FTP 探针：列出候选目录 + 下载最新综合文件样本，用于校准解析格式。
#
# 重要：
#   本执行环境(沙箱)的 FTP 被挡（连接超时），请在【能正常出网的本机】运行本脚本：
#       python cme_ftp_probe.py
#   运行后会把 CME 目录列表和最新综合文件前若干行存到
#       data/incoming/_cme_sample.txt
#   请把该文件内容（或前 80 行）发回来，我据此写精确解析器并产出真实数据。
import ftplib, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "incoming")
os.makedirs(OUT, exist_ok=True)
SAMPLE = os.path.join(OUT, "_cme_sample.txt")

HOST = "ftp.cmegroup.com"
CANDIDATE_DIRS = ["/pub/settle/", "/pub/fo/", "/pub/", "/"]
MAX_LINES = 120


def main():
    try:
        ftp = ftplib.FTP(HOST, timeout=25)
        ftp.set_pasv(True)
        ftp.login()  # 匿名
        print("已连接到", HOST)
    except Exception as e:
        print("FTP 连接失败：", repr(e))
        print(">>> 当前环境无法访问 CME FTP（被代理/防火墙挡）。")
        print(">>> 请在能正常出网的本机（非当前沙箱）运行： python cme_ftp_probe.py")
        sys.exit(1)

    target_file = None
    listing = []
    for d in CANDIDATE_DIRS:
        try:
            files = ftp.nlst(d)
        except Exception as ex:
            print(f"  nlst {d} 失败: {ex}")
            continue
        listing.append(f"\n=== 目录 {d} 文件数: {len(files)} ===")
        cme_files = [f for f in files if ("cme." in f) or ("options" in f.lower())
                     or ("voi" in f.lower()) or ("fo" in f.lower())]
        for f in sorted(cme_files)[-20:]:
            listing.append("   " + f)
        dated = sorted([f for f in files if f.startswith("cme.") and f[4:].isdigit()])
        if dated and target_file is None:
            target_file = (d, dated[-1])

    print("\n".join(listing))

    if not target_file:
        print("\n未找到 cme.YYYYMMDD 综合文件，请手动查看上面列出的目录。")
        ftp.quit()
        return

    d, fname = target_file
    print(f"\n下载样本：{d}{fname} 前 {MAX_LINES} 行 -> {SAMPLE}")
    lines = []
    def cb(line):
        lines.append(line)
    try:
        ftp.retrlines(f"RETR {d}{fname}", cb)
    except Exception as e:
        print("  下载中断（可能文件过大，仅取到部分）：", repr(e))
    # 只保留前 MAX_LINES 行
    lines = lines[:MAX_LINES]
    with open(SAMPLE, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(lines))
    print(f"样本已保存 {len(lines)} 行。请把 {SAMPLE} 内容发给我校准格式。")
    ftp.quit()


if __name__ == "__main__":
    main()
