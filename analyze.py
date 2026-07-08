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
    parser.add_argument(
        "--verbose-dump",
        action="store_true",
        help="Print every width row received from STM32. Default: only print metadata and summary.",
    )

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

        print(line)

        if (
            "Acquisition finished" in line
            or "Acquisition stopped" in line
            or "Buffer full" in line
        ):
            return

    raise TimeoutError("等待采集完成超时")


def parse_int_field(line, name):
    match = re.search(rf"{name}=(\d+)", line)
    if match:
        return int(match.group(1))
    return None


def read_dump(ser, verbose_dump=False):
    """
    推荐 STM32 输出格式：

        N=9998,N_SAVED=4096,TIMCLK=72000000,ETR_COUNT=10000,BUF_OVERWRITTEN=1
        index,width_ticks,width_ns
        0,70,972
        ...
        END
    """
    metadata = {
        "n_total": None,
        "n_saved_reported": None,
        "timclk": None,
        "etr_count": None,
        "buf_overwritten": None,
    }

    rows = []
    reading_table = False

    while True:
        line = safe_decode(ser.readline())
        if not line:
            continue

        if verbose_dump or not reading_table or line == "END":
            print(line)

        if line.startswith("N=") and "TIMCLK=" in line:
            metadata["n_total"] = parse_int_field(line, "N")
            metadata["n_saved_reported"] = parse_int_field(line, "N_SAVED")
            metadata["timclk"] = parse_int_field(line, "TIMCLK")
            metadata["etr_count"] = parse_int_field(line, "ETR_COUNT")

            continue

        if line.startswith("WIDTH_TOTAL="):
            metadata["n_total"] = parse_int_field(line, "WIDTH_TOTAL")

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

        writer.writerow(["n_total", metadata["n_total"]])
        writer.writerow(["n_saved_reported", metadata["n_saved_reported"]])
        writer.writerow(["timclk", metadata["timclk"]])
        writer.writerow(["etr_count", metadata["etr_count"]])
        writer.writerow([])

        writer.writerow(["index", "width_ticks", "width_ns"])
        for row in rows:
            writer.writerow([row["index"], row["width_ticks"], row["width_ns"]])

    return path


def print_collection_info(metadata, n_rows, acquisition_time_s):
    print("\n========== 采集结果 ==========")

    if metadata["etr_count"] is not None:
        etr_cps = metadata["etr_count"] / acquisition_time_s
        print(f"ETR_COUNT    : {metadata['etr_count']}")
        print(f"ETR CPS      : {etr_cps:.0f} CPS")

    if metadata["n_total"] is not None:
        width_cps = metadata["n_total"] / acquisition_time_s
        print(f"TIM2实际N    : {metadata['n_total']}")
        print(f"TIM2脉宽CPS  : {width_cps:.0f} CPS")

    if metadata["n_saved_reported"] is not None:
        print(f"STM32保存N   : {metadata['n_saved_reported']}")

    print(f"实际解析N    : {n_rows}")

    if metadata["n_total"] is not None:
        overwritten_old = max(metadata["n_total"] - n_rows, 0)
        print(f"被覆盖旧脉宽 : {overwritten_old}")

    if metadata["etr_count"] is not None and metadata["n_total"] is not None:
        print(f"ETR-TIM2差值 : {metadata['etr_count'] - metadata['n_total']}")
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
        if expected_ns != 0:
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

    return fig_path


def main():
    args = parse_args()
    print_params(args)

    ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    metadata = {}
    try:
        send_cmd(ser, f"start_{args.time}")
        wait_until_finished(ser, max_wait_s=args.time + 10)

        send_cmd(ser, "dump")
        metadata, rows = read_dump(ser, verbose_dump=args.verbose_dump)
    finally:
        ser.close()

    if not rows:
        print("没有解析到脉宽数据。请检查 STM32 是否输出 index,width_ticks,width_ns。")
        if metadata.get("etr_count") is not None:
            print(f"但已解析到 ETR_COUNT={metadata['etr_count']}。这说明计数通道可能正常，脉宽通道没有保存数据。")
        return

    widths_ns = np.array([row["width_ns"] for row in rows], dtype=float)

    csv_path = save_csv(rows, metadata, args.out)
    fig_path = plot_histogram(widths_ns, args.fig, expected_ns=args.expected)

    print_collection_info(metadata, len(rows), args.time)
    print_statistics(widths_ns, expected_ns=args.expected)

    print(f"\n原始数据已保存至 {csv_path}")
    print(f"直方图已保存至 {fig_path}")

    webbrowser.open(str(fig_path))


if __name__ == "__main__":
    main()
