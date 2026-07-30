import argparse
import csv
import re
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go

try:
    import serial
except Exception:
    serial = None

try:
    from scipy.optimize import curve_fit
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False

FWHM_FACTOR = 2.354820045
DEFAULT_TIMCLK = 72_000_000


def safe_decode(raw):
    return raw.decode(errors="ignore").strip()


def send_cmd(ser, cmd):
    ser.write((cmd + "\r\n").encode("ascii"))


def parse_int_field(line, name):
    m = re.search(rf"{name}=(\d+)", line)
    return int(m.group(1)) if m else None


def wait_until_finished(ser, max_wait_s):
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        line = safe_decode(ser.readline())
        if not line:
            continue
        print(line)
        if "Acquisition finished" in line or "Acquisition stopped" in line:
            return
    raise TimeoutError("等待采集完成超时")


def read_dump(ser, verbose_dump=False):
    """当前 STM32 dump 格式：
    N=223,N_SAVED=223,TIMCLK=72000000,ETR_COUNT=223.
    index,width_ticks,width_ns
    0,169,2347
    END
    """
    meta = {"n_total": None, "n_saved_reported": None, "timclk": None, "etr_count": None}
    rows = []
    reading = False
    while True:
        line = safe_decode(ser.readline())
        if not line:
            continue
        if verbose_dump or not reading or line == "END":
            print(line)
        if line.startswith("N=") and "TIMCLK=" in line:
            meta["n_total"] = parse_int_field(line, "N")
            meta["n_saved_reported"] = parse_int_field(line, "N_SAVED")
            meta["timclk"] = parse_int_field(line, "TIMCLK")
            meta["etr_count"] = parse_int_field(line, "ETR_COUNT")
            continue
        if line == "index,width_ticks,width_ns":
            reading = True
            continue
        if line == "END":
            break
        if reading:
            p = line.split(",")
            if len(p) != 3:
                continue
            try:
                rows.append({"index": int(p[0]), "width_ticks": int(p[1]), "width_ns": float(p[2])})
            except ValueError:
                continue
    return meta, rows


def ensure_dir(p):
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def paths_for(out_dir, name):
    out_dir = ensure_dir(out_dir)
    base = out_dir / name
    return {
        "csv": base.with_suffix(".csv"),
        "tick_html": base.with_name(base.name + "_tick_histogram.html"),
        "tick_csv": base.with_name(base.name + "_tick_histogram.csv"),
        "ns_html": base.with_name(base.name + "_ns_histogram.html"),
        "ns_csv": base.with_name(base.name + "_ns_histogram.csv"),
        "fit_all": base.with_name(base.name + "_fit_results.txt"),
    }


def save_combined_csv(cycle_rows, cycle_infos, csv_file):
    with Path(csv_file).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cycle_summary"])
        w.writerow(["cycle", "duration_s", "etr_count", "tim2_n_total", "n_saved_reported", "n_parsed", "timclk"])
        for i in cycle_infos:
            w.writerow([i["cycle"], i["duration_s"], i["etr_count"], i["n_total"], i["n_saved_reported"], i["n_parsed"], i["timclk"]])
        w.writerow([])
        w.writerow(["width_rows"])
        w.writerow(["cycle", "index", "width_ticks", "width_ns"])
        for r in cycle_rows:
            w.writerow([r["cycle"], r["index"], r["width_ticks"], r["width_ns"]])
    return Path(csv_file)


def load_latest_csv(csv_file):
    """只读取本脚本最新 CSV。若旧 CSV 没有 timclk 列，默认 72 MHz。"""
    csv_file = Path(csv_file)
    if not csv_file.exists():
        raise FileNotFoundError(f"找不到 CSV：{csv_file}")
    cycle_infos, rows = [], []
    mode, header = None, None
    with csv_file.open("r", newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or all(x.strip() == "" for x in r):
                continue
            first = r[0].strip()
            if first == "cycle_summary":
                mode, header = "cycle_summary", None
                continue
            if first == "width_rows":
                mode, header = "width_rows", None
                continue
            if mode == "cycle_summary":
                if header is None:
                    header = [x.strip() for x in r]
                    continue
                item = dict(zip(header, r))
                cycle_infos.append({
                    "cycle": int(item["cycle"]),
                    "duration_s": float(item["duration_s"]),
                    "etr_count": int(float(item["etr_count"])),
                    "n_total": int(float(item["tim2_n_total"])),
                    "n_saved_reported": int(float(item["n_saved_reported"])),
                    "n_parsed": int(float(item["n_parsed"])),
                    "timclk": int(float(item.get("timclk") or DEFAULT_TIMCLK)),
                })
                continue
            if mode == "width_rows":
                if header is None:
                    header = [x.strip() for x in r]
                    continue
                item = dict(zip(header, r))
                rows.append({
                    "cycle": int(item["cycle"]),
                    "index": int(item["index"]),
                    "width_ticks": int(float(item["width_ticks"])),
                    "width_ns": float(item["width_ns"]),
                })
    if not rows:
        raise ValueError("CSV 中没有 width_rows。请确认文件由新版 analyze.py 生成。")
    ticks = np.array([r["width_ticks"] for r in rows], dtype=int)
    ns = np.array([r["width_ns"] for r in rows], dtype=float)
    timclk = cycle_infos[0]["timclk"] if cycle_infos else DEFAULT_TIMCLK
    return ticks, ns, cycle_infos, timclk


def tick_histogram(ticks, tick_min=None, tick_max=None):
    if len(ticks) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    lo = int(np.min(ticks)) if tick_min is None else int(tick_min)
    hi = int(np.max(ticks)) if tick_max is None else int(tick_max)
    if hi < lo:
        lo, hi = hi, lo
    centers = np.arange(lo, hi + 1, dtype=int)
    counts = np.zeros(len(centers), dtype=int)
    vals, nums = np.unique(ticks[(ticks >= lo) & (ticks <= hi)], return_counts=True)
    for v, n in zip(vals, nums):
        counts[int(v - lo)] = int(n)
    return centers, counts


def write_plotly_hist_html(x, y, html_file, title, xlabel, x_unit="", notes=None, fit=None, vlines=None):
    """输出可交互 HTML。鼠标悬停可读坐标；不生成 PNG。"""
    if notes is None:
        notes = []
    if vlines is None:
        vlines = []
    traces = [{
        "type": "bar",
        "x": list(map(float, x)),
        "y": list(map(float, y)),
        "name": "histogram",
        "hovertemplate": f"%{{x}} {x_unit}<br>counts=%{{y}}<extra></extra>",
    }]
    if fit is not None:
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "x": list(map(float, fit["x"])),
            "y": list(map(float, fit["y"])),
            "name": fit.get("name", "fit"),
            "line": {"color": "red", "width": 2},
            "hovertemplate": f"x=%{{x:.3f}} {x_unit}<br>fit=%{{y:.2f}}<extra></extra>",
        })
    shapes = []
    annotations = []
    for vl in vlines:
        shapes.append({
            "type": "line",
            "xref": "x", "yref": "paper",
            "x0": float(vl["x"]), "x1": float(vl["x"]),
            "y0": 0, "y1": 1,
            "line": {"color": vl.get("color", "black"), "width": 1.5, "dash": "dash"},
        })
        annotations.append({
            "x": float(vl["x"]), "y": 1.02, "xref": "x", "yref": "paper",
            "text": vl.get("label", ""), "showarrow": False,
            "font": {"size": 12, "color": vl.get("color", "black")},
        })
    layout = {
        "title": title,
        "xaxis": {"title": xlabel, "showspikes": True, "spikemode": "across", "spikesnap": "cursor"},
        "yaxis": {"title": "Counts"},
        "hovermode": "closest",
        "bargap": 0.05,
        "shapes": shapes,
        "annotations": annotations,
    }
    figure = go.Figure(data=traces, layout=layout)
    plot_div = figure.to_html(
        full_html=False,
        include_plotlyjs=True,
        config={"responsive": True, "scrollZoom": True},
    )
    body_notes = "".join(f"<li>{n}</li>" for n in notes)
    html_text = f'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:Arial,'Microsoft YaHei',sans-serif;margin:20px;">
<h2>{title}</h2>
<ul>{body_notes}</ul>
{plot_div}
</body></html>'''
    Path(html_file).write_text(html_text, encoding="utf-8")
    return Path(html_file)


def save_tick_outputs(ticks, html_file, csv_file, tick_min=None, tick_max=None, open_html=False):
    centers, counts = tick_histogram(ticks, tick_min, tick_max)
    with Path(csv_file).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tick", "count"])
        for x, y in zip(centers, counts):
            w.writerow([int(x), int(y)])
    out = write_plotly_hist_html(centers, counts, html_file, f"Pulse Width Distribution in Ticks, N={len(ticks)}", "Pulse width / tick", "tick")
    if open_html:
        webbrowser.open(str(out))
    return out, Path(csv_file)


def save_ns_outputs_from_ticks(ticks, html_file, csv_file, tick_ns, tick_min=None, tick_max=None, open_html=False):
    centers_tick, counts = tick_histogram(ticks, tick_min, tick_max)
    centers_ns = centers_tick.astype(float) * tick_ns
    with Path(csv_file).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["width_ns_center", "tick", "count"])
        for ns, t, c in zip(centers_ns, centers_tick, counts):
            w.writerow([float(ns), int(t), int(c)])
    out = write_plotly_hist_html(
        centers_ns, counts, html_file,
        f"Pulse Width Distribution in ns, N={len(ticks)}",
        "Pulse width / ns", "ns",
        notes=[f"由 tick 直方图按 1 tick = {tick_ns:.6f} ns 换算；没有重新用任意 ns bin 分箱，因此不会引入额外锯齿。"],
    )
    if open_html:
        webbrowser.open(str(out))
    return out, Path(csv_file)


def init_live_plot(no_live_plot):
    if no_live_plot:
        return None, None
    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 5))
    return fig, ax


def update_live_plot(fig, ax, ticks, tick_min=None, tick_max=None):
    if fig is None or ax is None or len(ticks) == 0:
        return
    centers, counts = tick_histogram(ticks, tick_min, tick_max)
    ax.clear()
    ax.bar(centers, counts, width=0.9)
    ax.set_xlabel("Pulse width / tick")
    ax.set_ylabel("Counts")
    ax.set_title(f"Live Tick Distribution, N={len(ticks)}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.canvas.draw()
    fig.canvas.flush_events()
    plt.pause(0.01)


def print_summary(ticks, ns, cycle_infos):
    total_time = sum(i["duration_s"] for i in cycle_infos)
    total_etr = sum(i["etr_count"] for i in cycle_infos)
    total_tim2 = sum(i["n_total"] for i in cycle_infos)
    print("\n========== 文件/采集统计 ==========")
    print(f"累计时间       : {total_time:.0f} s")
    print(f"累计 ETR_COUNT : {total_etr}")
    print(f"平均 ETR CPS   : {total_etr / total_time:.2f} CPS" if total_time > 0 else "平均 ETR CPS   : 0 CPS")
    print(f"累计 TIM2_N    : {total_tim2}")
    print(f"平均 TIM2 CPS  : {total_tim2 / total_time:.2f} CPS" if total_time > 0 else "平均 TIM2 CPS  : 0 CPS")
    print(f"ETR-TIM2差值   : {total_etr - total_tim2}")
    print(f"脉宽样本数     : {len(ticks)}")
    print("\n========== tick 统计 ==========")
    print(f"平均值         : {np.mean(ticks):.2f} tick")
    print(f"中位数         : {np.median(ticks):.2f} tick")
    print(f"标准差         : {np.std(ticks, ddof=1) if len(ticks) >= 2 else 0.0:.2f} tick")
    print(f"最小值         : {np.min(ticks)} tick")
    print(f"最大值         : {np.max(ticks)} tick")
    print("\n========== ns 统计 ==========")
    print(f"平均值         : {np.mean(ns):.2f} ns")
    print(f"中位数         : {np.median(ns):.2f} ns")
    print(f"标准差         : {np.std(ns, ddof=1) if len(ns) >= 2 else 0.0:.2f} ns")
    print(f"最小值         : {np.min(ns):.2f} ns")
    print(f"最大值         : {np.max(ns):.2f} ns")


def gaussian_const(x, amp, mu, sigma, bg):
    return bg + amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def roi_hist(ticks, lo, hi):
    roi = ticks[(ticks >= lo) & (ticks <= hi)]
    centers = np.arange(lo, hi + 1, dtype=float)
    counts = np.zeros(len(centers), dtype=float)
    vals, nums = np.unique(roi, return_counts=True)
    for v, n in zip(vals, nums):
        if lo <= v <= hi:
            counts[int(v - lo)] = n
    return roi, centers, counts


def fit_single_roi(ticks, lo, hi, tick_ns, out_dir, name):
    if not SCIPY_OK:
        raise RuntimeError("未安装 scipy，无法做高斯拟合。请先运行：pip install scipy")
    if hi < lo:
        lo, hi = hi, lo
    roi, x, y = roi_hist(ticks, lo, hi)
    if len(roi) < 10:
        print("ROI 内事件太少，无法可靠拟合。")
        return None
    bg0 = float(np.percentile(y, 10))
    amp0 = max(float(np.max(y) - bg0), 1.0)
    mu0 = float(x[np.argmax(y)])
    sigma0 = max(float(np.std(roi, ddof=1)), 1.0)
    popt, _ = curve_fit(
        gaussian_const, x, y,
        p0=[amp0, mu0, sigma0, bg0],
        bounds=([0, lo, 0.5, 0], [np.inf, hi, max(hi - lo, 1), np.inf]),
        maxfev=20000,
    )
    amp, mu, sigma, bg = popt
    sigma = abs(float(sigma))
    fwhm_tick = FWHM_FACTOR * sigma
    left_half = float(mu - fwhm_tick / 2)
    right_half = float(mu + fwhm_tick / 2)
    resolution = fwhm_tick / mu * 100 if mu != 0 else float("nan")
    mu_ns = float(mu * tick_ns)
    sigma_ns = float(sigma * tick_ns)
    fwhm_ns = float(fwhm_tick * tick_ns)
    lines = [
        f"--- 单高斯拟合 ROI=[{lo},{hi}] ---",
        f"峰位           : {mu:.3f} tick = {mu_ns:.2f} ns",
        f"σ              : {sigma:.3f} tick = {sigma_ns:.2f} ns",
        f"FWHM           : {fwhm_tick:.3f} tick = {fwhm_ns:.2f} ns",
        f"相对分辨率     : {resolution:.2f} %",
    ]
    print()
    print("\n".join(lines))
    dense_x = np.linspace(lo, hi, 1000)
    dense_y = gaussian_const(dense_x, *popt)
    out_dir = ensure_dir(out_dir)
    fit_html = out_dir / f"{name}_roi_{lo}_{hi}_fit.html"
    fit_txt = out_dir / f"{name}_roi_{lo}_{hi}_fit.txt"
    all_fit_txt = out_dir / f"{name}_fit_results.txt"
    notes = [
        f"ROI = [{lo}, {hi}] tick",
        f"Peak = {mu:.3f} tick = {mu_ns:.2f} ns",
        f"Sigma = {sigma:.3f} tick = {sigma_ns:.2f} ns",
        f"FWHM = {fwhm_tick:.3f} tick = {fwhm_ns:.2f} ns",
        f"Resolution = {resolution:.2f} %",
    ]
    write_plotly_hist_html(
        x, y, fit_html,
        f"Gaussian Fit: {name}, ROI=[{lo},{hi}]",
        "Pulse width / tick", "tick",
        notes=notes,
        fit={"x": dense_x.tolist(), "y": dense_y.tolist(), "name": "Gaussian + constant background"},
        vlines=[
            {"x": float(mu), "label": f"Peak {mu:.2f}", "color": "red"},
            {"x": left_half, "label": f"FWHM-L {left_half:.2f}", "color": "green"},
            {"x": right_half, "label": f"FWHM-R {right_half:.2f}", "color": "green"},
        ],
    )
    fit_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with all_fit_txt.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    print(f"拟合图已保存至 {fit_html}")
    print(f"拟合结果已保存至 {fit_txt}")
    webbrowser.open(str(fit_html))
    return mu, sigma, fwhm_tick, resolution


def roi_loop(ticks, tick_ns, out_dir, name, make_ns_callback=None):
    print("\n========== ROI 高斯拟合 ==========")
    print("命令：")
    print("  fit  起始tick  结束tick       单高斯拟合")
    print("  ns                          生成 ns 横轴直方图")
    print("  done                        结束")
    print("提示：HTML 图可用鼠标查看坐标，先人工看峰位再选 ROI。")
    while True:
        cmd = input("ROI> ").strip().lower()
        if cmd in {"done", "q", "quit", "exit", ""}:
            break
        if cmd == "ns":
            if make_ns_callback:
                make_ns_callback()
            continue
        p = cmd.split()
        if len(p) != 3 or p[0] != "fit":
            print("格式错误。示例：fit 140 210；生成 ns 图：ns；结束：done")
            continue
        try:
            fit_single_roi(ticks, int(p[1]), int(p[2]), tick_ns, out_dir, name)
        except Exception as e:
            print(f"拟合失败：{e}")


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


def run_acquire(args):
    if serial is None:
        raise RuntimeError("未安装 pyserial。请运行：pip install pyserial")
    out_dir = ensure_dir(args.out_dir)
    paths = paths_for(out_dir, args.name)
    print("========== 采集模式 ==========")
    print(f"串口       : {args.port}")
    print(f"波特率     : {args.baud}")
    print(f"每轮采集   : {args.cycle_time} s")
    print(f"输出目录   : {out_dir}")
    print(f"文件名     : {args.name}")
    print("停止方式   : 输入 stop 后回车，或 Ctrl+C\n")
    stop_event = threading.Event()
    threading.Thread(target=stop_input_thread, args=(stop_event,), daemon=True).start()
    all_rows, cycle_infos, all_ticks, all_ns = [], [], [], []
    last_timclk = DEFAULT_TIMCLK
    fig, ax = init_live_plot(args.no_live_plot)
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
            wait_until_finished(ser, args.cycle_time + 10)
            send_cmd(ser, "dump")
            meta, rows = read_dump(ser, args.verbose_dump)
            timclk = meta["timclk"] or DEFAULT_TIMCLK
            last_timclk = timclk
            for row in rows:
                all_rows.append({"cycle": cycle, "index": row["index"], "width_ticks": row["width_ticks"], "width_ns": row["width_ns"]})
                all_ticks.append(row["width_ticks"])
                all_ns.append(row["width_ns"])
            info = {
                "cycle": cycle,
                "duration_s": args.cycle_time,
                "etr_count": meta["etr_count"] or 0,
                "n_total": meta["n_total"] or 0,
                "n_saved_reported": meta["n_saved_reported"] or len(rows),
                "n_parsed": len(rows),
                "timclk": timclk,
            }
            cycle_infos.append(info)
            total_time = sum(x["duration_s"] for x in cycle_infos)
            total_etr = sum(x["etr_count"] for x in cycle_infos)
            print(f"[第 {cycle} 轮] ETR={info['etr_count']}, TIM2_N={info['n_total']}, saved={len(rows)}, 累计ETR={total_etr}, 平均CPS={total_etr / total_time:.2f}, 累计样本={len(all_ticks)}")
            update_live_plot(fig, ax, np.array(all_ticks, dtype=int), args.tick_min, args.tick_max)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止采集。")
    finally:
        ser.close()
    ticks = np.array(all_ticks, dtype=int)
    ns = np.array(all_ns, dtype=float)
    tick_ns = 1e9 / last_timclk
    save_combined_csv(all_rows, cycle_infos, paths["csv"])
    tick_html, tick_csv = save_tick_outputs(ticks, paths["tick_html"], paths["tick_csv"], args.tick_min, args.tick_max, args.open_html)
    ns_html = ns_csv = None
    if args.make_ns:
        ns_html, ns_csv = save_ns_outputs_from_ticks(ticks, paths["ns_html"], paths["ns_csv"], tick_ns, args.tick_min, args.tick_max, args.open_html)
    print_summary(ticks, ns, cycle_infos)
    print(f"\nCSV 已保存至 {paths['csv']}")
    print(f"tick 直方图已保存至 {tick_html}")
    print(f"tick 直方图数据已保存至 {tick_csv}")
    if args.make_ns:
        print(f"ns 直方图已保存至 {ns_html}")
        print(f"ns 直方图数据已保存至 {ns_csv}")
    print(f"\nROI 拟合换算：1 tick = {tick_ns:.6f} ns")
    if args.roi:
        fit_single_roi(ticks, args.roi[0], args.roi[1], tick_ns, out_dir, args.name)
    if args.interactive_roi:
        def make_ns():
            out_html, out_csv = save_ns_outputs_from_ticks(ticks, paths["ns_html"], paths["ns_csv"], tick_ns, args.tick_min, args.tick_max, True)
            print(f"ns 直方图已保存至 {out_html}")
            print(f"ns 直方图数据已保存至 {out_csv}")
        roi_loop(ticks, tick_ns, out_dir, args.name, make_ns)


def run_analyze(args):
    out_dir = ensure_dir(args.out_dir)
    csv_file = Path(args.csv)
    if not csv_file.is_absolute():
        csv_file = out_dir / csv_file
    name = args.name if args.name else csv_file.stem
    paths = paths_for(out_dir, name)
    print("========== 文件分析模式 ==========")
    print(f"读取 CSV    : {csv_file}")
    print(f"输出目录    : {out_dir}")
    print(f"输出前缀    : {name}\n")
    ticks, ns, cycle_infos, timclk = load_latest_csv(csv_file)
    tick_ns = 1e9 / timclk
    tick_html, tick_csv = save_tick_outputs(ticks, paths["tick_html"], paths["tick_csv"], args.tick_min, args.tick_max, args.open_html)
    ns_html = ns_csv = None
    if args.make_ns:
        ns_html, ns_csv = save_ns_outputs_from_ticks(ticks, paths["ns_html"], paths["ns_csv"], tick_ns, args.tick_min, args.tick_max, args.open_html)
    print_summary(ticks, ns, cycle_infos)
    print(f"\ntick 直方图已保存至 {tick_html}")
    print(f"tick 直方图数据已保存至 {tick_csv}")
    if args.make_ns:
        print(f"ns 直方图已保存至 {ns_html}")
        print(f"ns 直方图数据已保存至 {ns_csv}")
    print(f"\nROI 拟合换算：1 tick = {tick_ns:.6f} ns")
    if args.roi:
        fit_single_roi(ticks, args.roi[0], args.roi[1], tick_ns, out_dir, name)
    if args.interactive_roi:
        def make_ns():
            out_html, out_csv = save_ns_outputs_from_ticks(ticks, paths["ns_html"], paths["ns_csv"], tick_ns, args.tick_min, args.tick_max, True)
            print(f"ns 直方图已保存至 {out_html}")
            print(f"ns 直方图数据已保存至 {out_csv}")
        roi_loop(ticks, tick_ns, out_dir, name, make_ns)


def build_parser():
    parser = argparse.ArgumentParser(description="STM32 pulse width analyzer: acquire and offline analyze")
    sub = parser.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", help="串口采集并保存 CSV + tick HTML")
    acq.add_argument("--port", default="COM6")
    acq.add_argument("--baud", type=int, default=115200)
    acq.add_argument("--timeout", type=float, default=3.0)
    acq.add_argument("--cycle-time", type=int, default=1)
    acq.add_argument("--max-cycles", type=int, default=None)
    acq.add_argument("--verbose-dump", action="store_true")
    acq.add_argument("--no-live-plot", action="store_true")
    acq.add_argument("--out-dir", default=".")
    acq.add_argument("--name", default="width_live")
    acq.add_argument("--tick-min", type=int, default=None)
    acq.add_argument("--tick-max", type=int, default=None)
    acq.add_argument("--open-html", action="store_true")
    acq.add_argument("--make-ns", action="store_true")
    acq.add_argument("--roi", nargs=2, type=int, default=None)
    acq.add_argument("--interactive-roi", action="store_true")
    acq.set_defaults(func=run_acquire)

    an = sub.add_parser("analyze", help="读取已有 CSV，离线画图与 ROI 拟合")
    an.add_argument("--csv", required=True)
    an.add_argument("--out-dir", default=".")
    an.add_argument("--name", default=None)
    an.add_argument("--tick-min", type=int, default=None)
    an.add_argument("--tick-max", type=int, default=None)
    an.add_argument("--open-html", action="store_true")
    an.add_argument("--make-ns", action="store_true")
    an.add_argument("--roi", nargs=2, type=int, default=None)
    an.add_argument("--interactive-roi", action="store_true", default=True)
    an.set_defaults(func=run_analyze)
    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
