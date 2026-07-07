import argparse
import csv
import re
import time
import webbrowser
from pathlib import Path

import serial
import numpy as np
import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(description="STM32 pulse width analyzer")

    parser.add_argument("--port", default="COM6", help="Serial port, e.g. COM6")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--time", type=int, default=1, help="Acquisition time in seconds")
    parser.add_argument("--expected", type=float, default=None, help="Expected pulse width in ns")
    parser.add_argument("--timeout", type=float, default=3.0, help="Serial timeout in seconds")
    parser.add_argument("--out", default="width_data.csv", help="Output CSV filename")
    parser.add_argument("--fig", default="width_histogram.png", help="Output histogram filename")

    return parser.parse_args()


def print_params(args):
    print("========== 参数 ==========")
    print(f"串口       : {args.port}")
    print(f"波特率     : {args.baud}")
    print(f"采集时间   : {args.time} s")
    if args.expected is not None:
        print(f"设定脉宽   : {args.expected:.1f} ns")
    else:
        print("设定脉宽   : 未指定")
    print()


def send_cmd(ser, cmd):
    print(f"发送 {cmd} ...")
    ser.write((cmd + "\r\n").encode("ascii"))


def safe_decode(raw):
    return raw.decode(errors="ignore").strip()


def wait_until_finished(ser, max_wait_s):
    start_time = time.time()
    while time.time() - start_time < max_wait_s:
        line = safe_decode(ser.readline())
        if not line:
            continue
        print(line)                     # 输出采集过程中的提示
        if "Acquisition finished" in line or "Buffer full" in line:
            return
    raise TimeoutError("等待采集完成超时")


def read_dump(ser):
    metadata = {
        "n_reported": None,
        "timclk": None,
        "etr_count": None,
    }
    rows = []
    reading_table = False

    while True:
        line = safe_decode(ser.readline())
        if not line:
            continue

        print(line)                     # 实时输出所有 dump 数据

        if line.startswith("N=") and "TIMCLK=" in line:
            n_match = re.search(r"N=(\d+)", line)
            clk_match = re.search(r"TIMCLK=(\d+)", line)
            etr_match = re.search(r"ETR_COUNT=(\d+)", line)
            if n_match:
                metadata["n_reported"] = int(n_match.group(1))
            if clk_match:
                metadata["timclk"] = int(clk_match.group(1))
            if etr_match:
                metadata["etr_count"] = int(etr_match.group(1))
            continue

        if line == "index,width_ticks,width_ns":
            reading_table = True
            continue

        if line == "END":
            break

        if reading_table:
            parts = line.split(",")
            if len(parts) != 3:
                continue
            try:
                rows.append({
                    "index": int(parts[0]),
                    "width_ticks": int(parts[1]),
                    "width_ns": int(parts[2]),
                })
            except ValueError:
                continue

    return metadata, rows


def save_csv(rows, metadata, output_file):
    path = Path(output_file)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["n_reported", metadata["n_reported"]])
        writer.writerow(["timclk", metadata["timclk"]])
        writer.writerow(["etr_count", metadata["etr_count"]])
        writer.writerow([])
        writer.writerow(["index", "width_ticks", "width_ns"])
        for row in rows:
            writer.writerow([row["index"], row["width_ticks"], row["width_ns"]])
    return path


def print_statistics(widths_ns, expected_ns=None):
    print("\n========== 脉宽统计 ==========")
    print(f"平均值       : {np.mean(widths_ns):.2f} ns")
    print(f"中位数       : {np.median(widths_ns):.2f} ns")
    if len(widths_ns) >= 2:
        std_value = np.std(widths_ns, ddof=1)
    else:
        std_value = 0.0
    print(f"标准差       : {std_value:.2f} ns")
    print(f"最小值       : {np.min(widths_ns):.2f} ns")
    print(f"最大值       : {np.max(widths_ns):.2f} ns")

    if expected_ns is not None:
        err = widths_ns - expected_ns
        abs_err = np.abs(err)
        print("\n========== 与设定脉宽对比 ==========")
        print(f"设定脉宽     : {expected_ns:.2f} ns")
        print(f"平均误差     : {np.mean(err):.2f} ns")
        print(f"中位误差     : {np.median(err):.2f} ns")
        print(f"平均绝对误差 : {np.mean(abs_err):.2f} ns")
        print(f"最大绝对误差 : {np.max(abs_err):.2f} ns")
        print(f"相对误差均值 : {np.mean(err) / expected_ns * 100:.3f} %")


def plot_histogram(widths_ns, fig_file, expected_ns=None):
    fig_path = Path(fig_file)
    plt.figure(figsize=(10, 5))
    plt.hist(widths_ns, bins=80, edgecolor="black", alpha=0.75)
    if expected_ns is not None:
        plt.axvline(
            expected_ns,
            linestyle="--",
            linewidth=2,
            label=f"Expected = {expected_ns:.0f} ns",
        )
        plt.legend()
    plt.xlabel("Pulse width / ns")
    plt.ylabel("Counts")
    plt.title(f"Pulse Width Distribution, N={len(widths_ns)}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(fig_path, dpi=200)
    plt.close()
    print(f"\n原始数据已保存至 {Path('width_data.csv')}")
    print(f"直方图已保存至 {fig_path}")
    return fig_path


def main():
    args = parse_args()
    print_params(args)

    ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    try:
        send_cmd(ser, f"start_{args.time}")
        wait_until_finished(ser, max_wait_s=args.time + 10)

        send_cmd(ser, "dump")
        metadata, rows = read_dump(ser)
    finally:
        ser.close()

    if not rows:
        print("没有解析到脉宽数据。请检查 STM32 是否输出 index,width_ticks,width_ns。")
        return

    widths_ns = np.array([row["width_ns"] for row in rows], dtype=float)

    save_csv(rows, metadata, args.out)
    print_statistics(widths_ns, expected_ns=args.expected)

    fig_path = plot_histogram(widths_ns, args.fig, expected_ns=args.expected)

    if metadata["etr_count"] is not None:
        n_width = len(rows)
        etr_count = metadata["etr_count"]
        print("\n========== ETR计数 ==========")
        print(f"ETR_COUNT    : {etr_count}")
        print(f"脉宽记录数   : {n_width}")
        print(f"ETR-N差值    : {etr_count - n_width}")

    webbrowser.open(str(fig_path))


if __name__ == "__main__":
    main()