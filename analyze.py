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

try:
    from scipy.optimize import curve_fit
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False


FWHM_FACTOR = 2.354820045


def parse_args():
    parser = argparse.ArgumentParser(description="STM32 live pulse width tick histogram analyzer with ROI Gaussian fit")
    parser.add_argument("--port", default="COM6", help="Serial port, e.g. COM6")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--timeout", type=float, default=3.0, help="Serial timeout in seconds")
    parser.add_argument("--cycle-time", type=int, default=1, help="Acquisition time per cycle in seconds")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional maximum number of cycles. Default: run until stop.")
    parser.add_argument("--verbose-dump", action="store_true", help="Print every width row received from STM32.")
    parser.add_argument("--no-live-plot", action="store_true", help="Do not show live plot; only save final figures.")
    parser.add_argument("--out", default="width_live_data.csv", help="Output CSV filename")
    parser.add_argument("--fig-prefix", default="width_live", help="Output figure filename prefix")
    parser.add_argument("--bins-ns", type=int, default=80, help="Bin count for final ns histogram")
    parser.add_argument("--tick-min", type=int, default=None, help="Optional tick histogram lower limit")
    parser.add_argument("--tick-max", type=int, default=None, help="Optional tick histogram upper limit")
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
    print("实时图     : tick 直方图")
    print()


def safe_decode(raw):
    return raw.decode(errors="ignore").strip()


def send_cmd(ser, cmd):
    ser.write((cmd + "\r\n").encode("ascii"))


def parse_int_field(line, name):
    m = re.search(rf"{name}=(\d+)", line)
    return int(m.group(1)) if m else None


def wait_until_finished(ser, max_wait_s):
    start_time = time.time()
    while time.time() - start_time < max_wait_s:
        line = safe_decode(ser.readline())
        if not line:
            continue
        print(line)
        if ("Acquisition finished" in line or
            "Acquisition stopped" in line or
            "Buffer full" in line):
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
        writer.writerow(["cycle", "duration_s", "etr_count", "tim2_n_total",
                         "n_saved_reported", "n_parsed", "buf_overwritten"])
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


def tick_hist_edges(ticks, tick_min=None, tick_max=None):
    if len(ticks) == 0:
        return np.array([0, 1], dtype=float)
    lo = int(np.min(ticks)) if tick_min is None else int(tick_min)
    hi = int(np.max(ticks)) if tick_max is None else int(tick_max)
    if hi <= lo:
        hi = lo + 1
    return np.arange(lo - 0.5, hi + 1.5, 1.0)


def print_running_summary(cycle_infos, total_width_samples):
    total_duration = sum(info["duration_s"] for info in cycle_infos)
    total_etr = sum(info.get("etr_count") or 0 for info in cycle_infos)
    last = cycle_infos[-1]
    avg_etr_cps = total_etr / total_duration if total_duration > 0 else 0.0
    print(
        f"[第 {last['cycle']} 轮] "
        f"ETR={last.get('etr_count')}, TIM2_N={last.get('n_total')}, saved={last.get('n_parsed')}, "
        f"累计ETR={total_etr}, 平均ETR CPS={avg_etr_cps:.2f}, "
        f"累计脉宽样本={total_width_samples}"
    )
    if last.get("etr_count") is not None and last.get("n_total") is not None:
        diff = last["etr_count"] - last["n_total"]
        if diff != 0:
            print(f"  注意：本轮 ETR-TIM2 差值 = {diff}")


def print_final_statistics(ticks, ns, cycle_infos):
    total_duration = sum(info["duration_s"] for info in cycle_infos)
    total_etr = sum(info.get("etr_count") or 0 for info in cycle_infos)
    total_tim2 = sum(info.get("n_total") or 0 for info in cycle_infos)

    print("\n========== 最终采集结果 ==========")
    print(f"累计时间       : {total_duration:.0f} s")
    print(f"累计 ETR_COUNT : {total_etr}")
    print(f"平均 ETR CPS   : {total_etr / total_duration:.2f} CPS" if total_duration > 0 else "平均 ETR CPS   : 0 CPS")
    print(f"累计 TIM2_N    : {total_tim2}")
    print(f"平均 TIM2 CPS  : {total_tim2 / total_duration:.2f} CPS" if total_duration > 0 else "平均 TIM2 CPS  : 0 CPS")
    print(f"累计脉宽样本   : {len(ticks)}")
    print(f"ETR-TIM2差值   : {total_etr - total_tim2}")

    if len(ticks) == 0:
        return

    print("\n========== 脉宽统计：tick ==========")
    print(f"平均值         : {np.mean(ticks):.2f} tick")
    print(f"中位数         : {np.median(ticks):.2f} tick")
    print(f"标准差         : {np.std(ticks, ddof=1) if len(ticks) >= 2 else 0.0:.2f} tick")
    print(f"最小值         : {np.min(ticks)} tick")
    print(f"最大值         : {np.max(ticks)} tick")

    print("\n========== 脉宽统计：ns ==========")
    print(f"平均值         : {np.mean(ns):.2f} ns")
    print(f"中位数         : {np.median(ns):.2f} ns")
    print(f"标准差         : {np.std(ns, ddof=1) if len(ns) >= 2 else 0.0:.2f} ns")
    print(f"最小值         : {np.min(ns):.2f} ns")
    print(f"最大值         : {np.max(ns):.2f} ns")


def init_live_plot(args):
    if args.no_live_plot:
        return None, None
    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    return fig, ax


def update_live_tick_histogram(fig, ax, ticks, args, cycle_count):
    if args.no_live_plot or fig is None or ax is None or len(ticks) == 0:
        return
    ax.clear()
    ax.hist(ticks, bins=tick_hist_edges(ticks, args.tick_min, args.tick_max))
    ax.set_xlabel("Pulse width / tick")
    ax.set_ylabel("Counts")
    ax.set_title(f"Live Tick Distribution, cycles={cycle_count}, N={len(ticks)}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.canvas.draw()
    fig.canvas.flush_events()
    plt.pause(0.01)


def save_final_histograms(ticks, ns, args):
    if len(ticks) == 0:
        return None, None

    tick_fig = Path(f"{args.fig_prefix}_tick_histogram.png")
    ns_fig = Path(f"{args.fig_prefix}_ns_histogram.png")

    plt.ioff()

    plt.figure(figsize=(10, 5))
    plt.hist(ticks, bins=tick_hist_edges(ticks, args.tick_min, args.tick_max))
    plt.xlabel("Pulse width / tick")
    plt.ylabel("Counts")
    plt.title(f"Pulse Width Distribution in Ticks, N={len(ticks)}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(tick_fig, dpi=200)
    plt.close()

    plt.figure(figsize=(10, 5))
    plt.hist(ns, bins=args.bins_ns)
    plt.xlabel("Pulse width / ns")
    plt.ylabel("Counts")
    plt.title(f"Pulse Width Distribution in ns, N={len(ns)}")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(ns_fig, dpi=200)
    plt.close()

    return tick_fig, ns_fig


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


def gaussian_const(x, amp, mu, sigma, bg):
    return bg + amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def double_gaussian_const(x, amp1, mu1, sigma1, amp2, mu2, sigma2, bg):
    return (
        bg
        + amp1 * np.exp(-0.5 * ((x - mu1) / sigma1) ** 2)
        + amp2 * np.exp(-0.5 * ((x - mu2) / sigma2) ** 2)
    )


def roi_histogram(ticks, lo, hi):
    roi_ticks = ticks[(ticks >= lo) & (ticks <= hi)]
    centers = np.arange(lo, hi + 1, dtype=float)
    counts = np.zeros(len(centers), dtype=float)
    if len(roi_ticks) > 0:
        values, cnts = np.unique(roi_ticks, return_counts=True)
        for v, c in zip(values, cnts):
            if lo <= v <= hi:
                counts[int(v - lo)] = c
    return roi_ticks, centers, counts


def estimate_single_from_moments(roi_ticks):
    mu = float(np.mean(roi_ticks))
    sigma = float(np.std(roi_ticks, ddof=1)) if len(roi_ticks) >= 2 else 0.0
    return mu, sigma


def report_peak(label, mu_tick, sigma_tick, tick_ns):
    fwhm_tick = FWHM_FACTOR * sigma_tick
    mu_ns = mu_tick * tick_ns
    sigma_ns = sigma_tick * tick_ns
    fwhm_ns = fwhm_tick * tick_ns
    resolution = fwhm_tick / mu_tick * 100.0 if mu_tick != 0 else float("nan")

    print(f"\n--- {label} ---")
    print(f"峰位           : {mu_tick:.3f} tick = {mu_ns:.2f} ns")
    print(f"σ              : {sigma_tick:.3f} tick = {sigma_ns:.2f} ns")
    print(f"FWHM           : {fwhm_tick:.3f} tick = {fwhm_ns:.2f} ns")
    print(f"相对分辨率     : {resolution:.2f} %")
    return {
        "mu_tick": mu_tick,
        "sigma_tick": sigma_tick,
        "fwhm_tick": fwhm_tick,
        "mu_ns": mu_ns,
        "sigma_ns": sigma_ns,
        "fwhm_ns": fwhm_ns,
        "resolution_percent": resolution,
    }


def fit_single_roi(ticks, lo, hi, tick_ns, fig_prefix):
    roi_ticks, x, y = roi_histogram(ticks, lo, hi)
    if len(roi_ticks) < 10:
        print("ROI 内事件太少，无法可靠拟合。")
        return None

    if not SCIPY_OK:
        print("未检测到 scipy：使用 ROI 内样本均值/标准差估计。若要高斯拟合，请运行 pip install scipy。")
        mu, sigma = estimate_single_from_moments(roi_ticks)
        return report_peak(f"单峰估计 ROI=[{lo},{hi}]", mu, sigma, tick_ns)

    bg0 = float(np.percentile(y, 10))
    amp0 = max(float(np.max(y) - bg0), 1.0)
    mu0 = float(x[np.argmax(y)])
    sigma0 = max(float(np.std(roi_ticks, ddof=1)), 1.0)

    lower = [0.0, lo, 0.5, 0.0]
    upper = [np.inf, hi, max(hi - lo, 1), np.inf]

    try:
        popt, _ = curve_fit(
            gaussian_const, x, y,
            p0=[amp0, mu0, sigma0, bg0],
            bounds=(lower, upper),
            maxfev=20000,
        )
    except Exception as e:
        print(f"单高斯拟合失败：{e}")
        mu, sigma = estimate_single_from_moments(roi_ticks)
        return report_peak(f"单峰矩估计 ROI=[{lo},{hi}]", mu, sigma, tick_ns)

    amp, mu, sigma, bg = popt
    sigma = abs(float(sigma))
    result = report_peak(f"单高斯拟合 ROI=[{lo},{hi}]", float(mu), sigma, tick_ns)

    fit_fig = Path(f"{fig_prefix}_roi_{lo}_{hi}_single_fit.png")
    dense_x = np.linspace(lo, hi, 1000)
    dense_y = gaussian_const(dense_x, *popt)

    plt.figure(figsize=(10, 5))
    plt.step(x, y, where="mid", label="ROI histogram")
    plt.plot(dense_x, dense_y, label="single Gaussian + const bg")
    plt.axvline(mu, linestyle="--", label=f"peak={mu:.2f} tick")
    plt.xlabel("Pulse width / tick")
    plt.ylabel("Counts")
    plt.title(f"Single Gaussian Fit, ROI=[{lo},{hi}]")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(fit_fig, dpi=200)
    plt.close()
    print(f"拟合图已保存至 {fit_fig}")
    return result


def choose_two_initial_peaks(x, y, lo, hi):
    sorted_idx = np.argsort(y)[::-1]
    mu1 = float(x[sorted_idx[0]])
    min_sep = max(3.0, (hi - lo) / 6.0)
    mu2 = None
    for idx in sorted_idx[1:]:
        candidate = float(x[idx])
        if abs(candidate - mu1) >= min_sep:
            mu2 = candidate
            break
    if mu2 is None:
        mu2 = float((lo + hi) / 2.0)
        if mu2 <= mu1:
            mu2 = min(float(hi), mu1 + min_sep)
    if mu1 > mu2:
        mu1, mu2 = mu2, mu1
    return mu1, mu2


def fit_double_roi(ticks, lo, hi, tick_ns, fig_prefix):
    roi_ticks, x, y = roi_histogram(ticks, lo, hi)
    if len(roi_ticks) < 30:
        print("ROI 内事件太少，无法可靠双峰拟合。")
        return None
    if not SCIPY_OK:
        print("双高斯拟合需要 scipy。请运行：pip install scipy")
        return None

    bg0 = float(np.percentile(y, 10))
    mu1_0, mu2_0 = choose_two_initial_peaks(x, y, lo, hi)
    amp_default = max(float(np.max(y) - bg0), 1.0)
    amp1_0 = amp_default
    amp2_0 = amp_default / 2.0
    sigma0 = max((hi - lo) / 10.0, 1.0)

    lower = [0.0, lo, 0.5, 0.0, lo, 0.5, 0.0]
    upper = [np.inf, hi, max(hi - lo, 1), np.inf, hi, max(hi - lo, 1), np.inf]

    try:
        popt, _ = curve_fit(
            double_gaussian_const, x, y,
            p0=[amp1_0, mu1_0, sigma0, amp2_0, mu2_0, sigma0, bg0],
            bounds=(lower, upper),
            maxfev=50000,
        )
    except Exception as e:
        print(f"双高斯拟合失败：{e}")
        return None

    amp1, mu1, sig1, amp2, mu2, sig2, bg = popt
    peaks = [
        {"amp": float(amp1), "mu": float(mu1), "sigma": abs(float(sig1))},
        {"amp": float(amp2), "mu": float(mu2), "sigma": abs(float(sig2))},
    ]
    peaks.sort(key=lambda p: p["mu"])

    print("\n双高斯拟合结果。注意：若两个峰间距小于约 1–2 个 σ，结果可能不唯一。")
    r1 = report_peak("双峰-低 tick 峰", peaks[0]["mu"], peaks[0]["sigma"], tick_ns)
    r2 = report_peak("双峰-高 tick 峰", peaks[1]["mu"], peaks[1]["sigma"], tick_ns)

    separation = abs(peaks[1]["mu"] - peaks[0]["mu"])
    avg_sigma = 0.5 * (peaks[0]["sigma"] + peaks[1]["sigma"])
    print(f"\n峰间距         : {separation:.3f} tick = {separation * tick_ns:.2f} ns")
    print(f"峰间距/平均σ   : {separation / avg_sigma:.2f}" if avg_sigma > 0 else "峰间距/平均σ   : nan")
    if avg_sigma > 0 and separation / avg_sigma < 2:
        print("警告           : 两峰严重重叠，双高斯参数相关性很强，只能作为经验分解。")

    fit_fig = Path(f"{fig_prefix}_roi_{lo}_{hi}_double_fit.png")
    dense_x = np.linspace(lo, hi, 1000)
    y1 = peaks[0]["amp"] * np.exp(-0.5 * ((dense_x - peaks[0]["mu"]) / peaks[0]["sigma"]) ** 2)
    y2 = peaks[1]["amp"] * np.exp(-0.5 * ((dense_x - peaks[1]["mu"]) / peaks[1]["sigma"]) ** 2)
    total = bg + y1 + y2

    plt.figure(figsize=(10, 5))
    plt.step(x, y, where="mid", label="ROI histogram")
    plt.plot(dense_x, total, label="double Gaussian + const bg")
    plt.plot(dense_x, bg + y1, linestyle="--", label="component 1 + bg")
    plt.plot(dense_x, bg + y2, linestyle="--", label="component 2 + bg")
    plt.axvline(peaks[0]["mu"], linestyle="--", label=f"peak1={peaks[0]['mu']:.2f} tick")
    plt.axvline(peaks[1]["mu"], linestyle="--", label=f"peak2={peaks[1]['mu']:.2f} tick")
    plt.xlabel("Pulse width / tick")
    plt.ylabel("Counts")
    plt.title(f"Double Gaussian Fit, ROI=[{lo},{hi}]")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(fit_fig, dpi=200)
    plt.close()
    print(f"拟合图已保存至 {fit_fig}")
    return r1, r2


def interactive_roi_fit(ticks, tick_ns, fig_prefix):
    if len(ticks) == 0:
        return
    print("\n========== ROI 高斯拟合 ==========")
    print("输入格式：")
    print("  fit  起始tick  结束tick       单高斯拟合，例如：fit 100 160")
    print("  fit2 起始tick  结束tick       双高斯拟合，例如：fit2 100 200")
    print("  done                         结束")
    print("建议先看 tick 直方图，再按峰附近范围选 ROI。")

    while True:
        cmd = input("ROI> ").strip().lower()
        if cmd in {"done", "q", "quit", "exit", ""}:
            break

        parts = cmd.split()
        if len(parts) != 3 or parts[0] not in {"fit", "fit2"}:
            print("格式错误。示例：fit 100 160 或 fit2 100 220")
            continue

        try:
            lo = int(parts[1])
            hi = int(parts[2])
        except ValueError:
            print("tick 范围必须是整数。")
            continue

        if hi < lo:
            lo, hi = hi, lo

        if parts[0] == "fit":
            fit_single_roi(ticks, lo, hi, tick_ns, fig_prefix)
        else:
            fit_double_roi(ticks, lo, hi, tick_ns, fig_prefix)


def main():
    args = parse_args()
    print_params(args)

    stop_event = threading.Event()
    thread = threading.Thread(target=stop_input_thread, args=(stop_event,), daemon=True)
    thread.start()

    all_rows = []
    cycle_infos = []
    all_ticks = []
    all_ns = []
    last_timclk = None

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

            if metadata.get("timclk"):
                last_timclk = metadata["timclk"]

            for row in rows:
                all_rows.append({
                    "cycle": cycle,
                    "index": row["index"],
                    "width_ticks": row["width_ticks"],
                    "width_ns": row["width_ns"],
                })
                all_ticks.append(row["width_ticks"])
                all_ns.append(row["width_ns"])

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
            print_running_summary(cycle_infos, len(all_ticks))
            update_live_tick_histogram(fig, ax, np.array(all_ticks, dtype=int), args, cycle)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止采集。")
    finally:
        ser.close()

    ticks_np = np.array(all_ticks, dtype=int)
    ns_np = np.array(all_ns, dtype=float)

    csv_path = save_combined_csv(all_rows, cycle_infos, args.out)
    tick_fig, ns_fig = save_final_histograms(ticks_np, ns_np, args)
    print_final_statistics(ticks_np, ns_np, cycle_infos)

    print(f"\n原始数据已保存至 {csv_path}")
    if tick_fig is not None:
        print(f"tick 直方图已保存至 {tick_fig}")
        webbrowser.open(str(tick_fig))
    if ns_fig is not None:
        print(f"ns 直方图已保存至 {ns_fig}")
        webbrowser.open(str(ns_fig))

    if last_timclk is None:
        print("\n警告：未从 STM32 解析到 TIMCLK，默认使用 72000000 Hz 做 tick-ns 换算。")
        last_timclk = 72000000

    tick_ns = 1e9 / last_timclk
    print(f"\n用于拟合换算：1 tick = {tick_ns:.6f} ns")
    interactive_roi_fit(ticks_np, tick_ns, args.fig_prefix)


if __name__ == "__main__":
    main()
