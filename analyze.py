import argparse
import csv
import re
import threading
import time
import webbrowser
from pathlib import Path

import serial
import numpy as np
import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(description="STM32 live pulse width histogram analyzer")

    parser.add_argument("--port", default="COM6", help="Serial port, e.g. COM6")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--expected", type=float, default=None, help="Expected pulse width in ns")
    parser.add_argument("--timeout", type=float, default=3.0, help="Serial timeout in seconds")

    parser.add_argument("--out", default="width_live_data.csv", help="Output CSV filename")
    parser.add_argument("--fig", default="width_live_histogram.png", help="Output histogram filename")
    parser.add_argument("--bins", type=int, default=80, help="Histogram bin count")
    parser.add_argument("--cycle-time", type=int, default=1, help="Acquisition time per cycle in seconds")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional maximum number of cycles.")
    parser.add_argument("--verbose-dump", action="store_true", help="Print every width row received from STM32.")
    parser.add_argument("--no-live-plot", action="store_true", help="Only save final figure.")

    return parser.parse_args()


def print_params(args):
    print("========== 实时采集参数 ==========")
    print(f"串口       : {args.port}")
    print(f"波特率     : {args.baud}")
    print(f"每轮采集   : {args.cycle_time} s")
    if args.max_cycles is None:
        print("停止方式   : 在此窗口输入 stop 后回车")
    else:
        print(f"最大轮数   : {args.max_cycles}")
    if args.expected is not None:
        print(f"设定脉宽   : {args.expected:.1f} ns")
    else:
        print("设定脉宽   : 未指定")
    print()


def safe_decode(raw):
    return raw.decode(errors="ignore").strip()


def send_cmd(ser, cmd):
    ser.write((cmd + "\r\n").encode("ascii"))


def parse_int_field(line, name):
    match = re.search(rf"{name}=(\d+)", line)
    if match:
        return int(match.group(1))
    return None


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


def read_dump(ser, verbose_dump=False):
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
            metadata["buf_overwritten"] = parse_int_field(line, "BUF_OVERWRITTEN")
            continue

        if line.startswith("WIDTH_TOTAL="):
            metadata["n_total"] = parse_int_field(line, "WIDTH_TOTAL")
            metadata["buf_overwritten"] = parse_int_field(line, "BUF_OVERWRITTEN")
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


def save_combined_csv(cycle_rows, cycle_infos, output_file):
    path = Path(output_file)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow(["cycle_summary"])
        writer.writerow(["cycle", "duration_s", "etr_count", "tim2_n_total", "n_saved_reported", "n_parsed", "buf_overwritten"])

        for info in cycle_infos:
            writer.writerow([
                info["cycle"],
                info["duration_s"],
                info.get("etr_count"),
                info.get("n_total"),
                info.get("n_saved_reported"),
                info.get("n_parsed"),
                info.get("buf_overwritten"),
            ])

        writer.writerow([])
        writer.writerow(["width_rows"])
        writer.writerow(["cycle", "index", "width_ticks", "width_ns"])

        for row in cycle_rows:
            writer.writerow([row["cycle"], row["index"], row["width_ticks"], row["width_ns"]])

    return path


def print_running_summary(cycle_infos, total_width_samples):
    total_duration = sum(info["duration_s"] for info in cycle_infos)
    total_etr = sum(info.get("etr_count") or 0 for info in cycle_infos)
    total_tim2 = sum(info.get("n_total") or 0 for info in cycle_infos)

    avg_etr_cps = total_etr / total_duration if total_duration > 0 else 0.0

    last = cycle_infos[-1]
    last_etr = last.get("etr_count")
    last_tim2 = last.get("n_total")
    last_saved = last.get("n_parsed")

    print(
        f"[第 {last['cycle']} 轮] "
        f"ETR={last_etr}, TIM2_N={last_tim2}, saved={last_saved}, "
        f"累计ETR={total_etr}, 平均ETR CPS={avg_etr_cps:.2f}, "
        f"累计脉宽样本={total_width_samples}"
    )

    if last_etr is not None and last_tim2 is not None:
        diff = last_etr - last_tim2
        if diff != 0:
            print(f"  注意：本轮 ETR-TIM2 差值 = {diff}")


def print_final_statistics(widths_ns, cycle_infos):
    total_duration = sum(info["duration_s"] for info in cycle_infos)
    total_etr = sum(info.get("etr_count") or 0 for info in cycle_infos)
    total_tim2 = sum(info.get("n_total") or 0 for info in cycle_infos)

    print("\n========== 最终采集结果 ==========")
    print(f"累计时间       : {total_duration:.0f} s")
    print(f"累计 ETR_COUNT : {total_etr}")
    print(f"平均 ETR CPS   : {total_etr / total_duration:.2f} CPS" if total_duration > 0 else "平均 ETR CPS   : 0 CPS")
    print(f"累计 TIM2_N    : {total_tim2}")
    print(f"平均 TIM2 CPS  : {total_tim2 / total_duration:.2f} CPS" if total_duration > 0 else "平均 TIM2 CPS  : 0 CPS")
    print(f"累计脉宽样本   : {len(widths_ns)}")
    print(f"ETR-TIM2差值   : {total_etr - total_tim2}")

    if len(widths_ns) == 0:
        return

    print("\n========== 脉宽统计 ==========")
    print(f"平均值         : {np.mean(widths_ns):.2f} ns")
    print(f"中位数         : {np.median(widths_ns):.2f} ns")
    print(f"标准差         : {np.std(widths_ns, ddof=1) if len(widths_ns) >= 2 else 0.0:.2f} ns")
    print(f"最小值         : {np.min(widths_ns):.2f} ns")
    print(f"最大值         : {np.max(widths_ns):.2f} ns")


def init_live_plot(args):
    if args.no_live_plot:
        return None, None

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    return fig, ax


def update_histogram(fig, ax, widths_ns, args, cycle_count):
    if args.no_live_plot or fig is None or ax is None or len(widths_ns) == 0:
        return

    ax.clear()
    ax.hist(widths_ns, bins=args.bins)

    if args.expected is not None:
        ax.axvline(args.expected, linestyle="--", linewidth=2, label=f"Expected = {args.expected:.0f} ns")
        ax.legend()

    ax.set_xlabel("Pulse width / ns")
    ax.set_ylabel("Counts")
    ax.set_title(f"Live Pulse Width Distribution, cycles={cycle_count}, N={len(widths_ns)}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.canvas.draw()
    fig.canvas.flush_events()
    plt.pause(0.01)


def save_final_histogram(widths_ns, args):
    fig_path = Path(args.fig)

    if len(widths_ns) == 0:
        return None

    plt.ioff()
    plt.figure(figsize=(10, 5))
    plt.hist(widths_ns, bins=args.bins)

    if args.expected is not None:
        plt.axvline(args.expected, linestyle="--", linewidth=2, label=f"Expected = {args.expected:.0f} ns")
        plt.legend()

    plt.xlabel("Pulse width / ns")
    plt.ylabel("Counts")
    plt.title(f"Pulse Width Distribution, N={len(widths_ns)}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(fig_path, dpi=200)
    plt.close()

    return fig_path


def stop_input_thread(stop_event):
    print("输入 stop 后回车即可停止；也可以 Ctrl+C。")
    while not stop_event.is_set():
        try:
            text = input().strip().lower()
        except EOFError:
            return

        if text in {"stop", "q", "quit", "exit"}:
            stop_event.set()
            return


def main():
    args = parse_args()
    print_params(args)

    stop_event = threading.Event()
    thread = threading.Thread(target=stop_input_thread, args=(stop_event,), daemon=True)
    thread.start()

    all_rows = []
    cycle_infos = []
    all_widths = []

    fig, ax = init_live_plot(args)

    ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    cycle = 0

    try:
        while not stop_event.is_set():
            if args.max_cycles is not None and cycle >= args.max_cycles:
                break

            cycle += 1

            send_cmd(ser, f"start_{args.cycle_time}")
            wait_until_finished(ser, max_wait_s=args.cycle_time + 10)

            send_cmd(ser, "dump")
            metadata, rows = read_dump(ser, verbose_dump=args.verbose_dump)

            for row in rows:
                all_rows.append({
                    "cycle": cycle,
                    "index": row["index"],
                    "width_ticks": row["width_ticks"],
                    "width_ns": row["width_ns"],
                })
                all_widths.append(row["width_ns"])

            cycle_info = {
                "cycle": cycle,
                "duration_s": args.cycle_time,
                "etr_count": metadata.get("etr_count"),
                "n_total": metadata.get("n_total"),
                "n_saved_reported": metadata.get("n_saved_reported"),
                "n_parsed": len(rows),
                "buf_overwritten": metadata.get("buf_overwritten"),
            }
            cycle_infos.append(cycle_info)

            print_running_summary(cycle_infos, len(all_widths))

            widths_np = np.array(all_widths, dtype=float)
            update_histogram(fig, ax, widths_np, args, cycle)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止采集。")

    finally:
        ser.close()

    widths_np = np.array(all_widths, dtype=float)

    csv_path = save_combined_csv(all_rows, cycle_infos, args.out)
    fig_path = save_final_histogram(widths_np, args)

    print_final_statistics(widths_np, cycle_infos)

    print(f"\n原始数据已保存至 {csv_path}")
    if fig_path is not None:
        print(f"直方图已保存至 {fig_path}")
        webbrowser.open(str(fig_path))


if __name__ == "__main__":
    main()
