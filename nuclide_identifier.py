"""TOT脉宽能量刻度与四种核素的初步识别。

功能：
1. 手动输入已知全能峰的脉宽，建立能量刻度并保存公式；
2. 手动输入脉宽，反推出峰值能量；
3. 从analyze.py输出的CSV自动找峰，或从旧能谱图片手动点峰，给出可能核素。

注意：识别结果只表示与数据库谱线相容，不能替代活度、几何条件、本底和探测
效率分析。当前数据库仅包含Am-241、Co-57、Cs-137和Co-60。
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import webbrowser
from collections import defaultdict
from pathlib import Path

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks


DEFAULT_TIMCLK = 72_000_000

# 能量数据对应主要γ线。强度仅用于说明，不参与当前的定量打分。
NUCLIDE_DATABASE = {
    "Am-241": {
        "lines": [
            {"energy_keV": 59.5409, "label": "γ 59.54 keV"},
        ],
        "note": "低能峰容易受探测器封装吸收和比较器阈值截断影响。",
    },
    "Co-57": {
        "lines": [
            {"energy_keV": 122.06065, "label": "γ 122.06 keV（主线）"},
            {"energy_keV": 136.47356, "label": "γ 136.47 keV（次线）"},
        ],
        "note": "两线可能因TOT分辨率不足而合并，以122.06 keV贡献为主。",
    },
    "Cs-137": {
        "lines": [
            {"energy_keV": 661.657, "label": "γ 661.657 keV"},
        ],
        "note": "优先用于中能区标定。",
    },
    "Co-60": {
        "lines": [
            {"energy_keV": 1173.228, "label": "γ 1173.228 keV"},
            {"energy_keV": 1332.492, "label": "γ 1332.492 keV"},
        ],
        "note": "若两个峰不能明确分开，不得把一个宽峰重复标成两个能量。",
    },
}

CALIBRATION_PROMPTS = [
    ("Am-241", 59.5409, "Am-241 59.5409 keV"),
    ("Co-57", 122.06065, "Co-57 122.06065 keV主峰"),
    ("Co-57", 136.47356, "Co-57 136.47356 keV次峰（未分开则留空）"),
    ("Cs-137", 661.657, "Cs-137 661.657 keV"),
    ("Co-60", 1173.228, "Co-60 1173.228 keV（未分开则留空）"),
    ("Co-60", 1332.492, "Co-60 1332.492 keV（未分开则留空）"),
]


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def parse_width_to_tick(value: float, unit: str, tick_ns: float) -> float:
    if unit == "tick":
        return float(value)
    if unit == "ns":
        return float(value) / tick_ns
    if unit == "us":
        return float(value) * 1000.0 / tick_ns
    raise ValueError(f"未知脉宽单位：{unit}")


def tick_to_unit(value_tick: float, unit: str, tick_ns: float) -> float:
    if unit == "tick":
        return float(value_tick)
    if unit == "ns":
        return float(value_tick) * tick_ns
    if unit == "us":
        return float(value_tick) * tick_ns / 1000.0
    raise ValueError(f"未知脉宽单位：{unit}")


def parse_point_spec(spec: str, unit: str, tick_ns: float) -> dict:
    """格式：ENERGY_KEV:WIDTH[:LABEL]。"""
    fields = spec.split(":", 2)
    if len(fields) < 2:
        raise ValueError(f"校准点格式错误：{spec}")
    energy = float(fields[0])
    width_tick = parse_width_to_tick(float(fields[1]), unit, tick_ns)
    label = fields[2] if len(fields) == 3 else f"{energy:g} keV"
    return {"nuclide": label, "energy_keV": energy, "width_tick": width_tick}


def interactive_calibration_points(unit: str, tick_ns: float) -> list[dict]:
    print("\n依次输入全能峰中心脉宽；没有看到、没有分开或不确定的峰直接回车跳过。")
    print(f"当前输入单位：{unit}；1 tick = {tick_ns:.9f} ns")
    points = []
    for nuclide, energy, prompt in CALIBRATION_PROMPTS:
        while True:
            raw = input(f"{prompt} 的峰中心脉宽 [{unit}]：").strip()
            if not raw:
                break
            try:
                width_tick = parse_width_to_tick(float(raw), unit, tick_ns)
            except ValueError:
                print("请输入数字，或直接回车跳过。")
                continue
            points.append(
                {
                    "nuclide": nuclide,
                    "energy_keV": float(energy),
                    "width_tick": float(width_tick),
                }
            )
            break
    return points


def regression_metrics(observed: np.ndarray, predicted: np.ndarray, parameter_count: int = 2) -> dict:
    residual = observed - predicted
    sse = float(np.sum(residual**2))
    rmse = math.sqrt(sse / len(observed))
    ss_total = float(np.sum((observed - np.mean(observed)) ** 2))
    r_squared = 1.0 - sse / ss_total if ss_total > 0 else float("nan")
    aic = len(observed) * math.log(max(sse / len(observed), 1e-300)) + 2 * parameter_count
    return {
        "r_squared": r_squared,
        "rmse_tick": rmse,
        "aic": aic,
        "residual_tick": residual,
    }


def fit_calibration(points: list[dict], model: str, timclk: int) -> dict:
    if len(points) < 3:
        raise ValueError("至少需要3个可靠且能量不同的全能峰校准点。")
    energy = np.asarray([point["energy_keV"] for point in points], dtype=float)
    width = np.asarray([point["width_tick"] for point in points], dtype=float)
    if len(np.unique(energy)) < 3:
        raise ValueError("至少需要3个不同的已知能量。")
    if np.any(energy <= 0):
        raise ValueError("校准能量必须大于0。")

    log_a, log_b = np.polyfit(np.log(energy), width, 1)
    log_prediction = log_a * np.log(energy) + log_b
    linear_m, linear_c = np.polyfit(energy, width, 1)
    linear_prediction = linear_m * energy + linear_c
    log_metrics = regression_metrics(width, log_prediction)
    linear_metrics = regression_metrics(width, linear_prediction)

    if model == "log":
        coefficients = {"a": float(log_a), "b": float(log_b)}
        selected = log_metrics
    else:
        coefficients = {"m": float(linear_m), "c": float(linear_c)}
        selected = linear_metrics

    tick_ns = 1e9 / timclk
    return {
        "version": 1,
        "model": model,
        "timclk_hz": int(timclk),
        "tick_ns": float(tick_ns),
        "coefficients": coefficients,
        "metrics": {
            "r_squared": selected["r_squared"],
            "rmse_tick": selected["rmse_tick"],
            "rmse_us": selected["rmse_tick"] * tick_ns / 1000.0,
            "aic": selected["aic"],
        },
        "comparison": {
            "log": {
                "a": float(log_a),
                "b": float(log_b),
                "r_squared": log_metrics["r_squared"],
                "rmse_tick": log_metrics["rmse_tick"],
                "aic": log_metrics["aic"],
            },
            "linear": {
                "m": float(linear_m),
                "c": float(linear_c),
                "r_squared": linear_metrics["r_squared"],
                "rmse_tick": linear_metrics["rmse_tick"],
                "aic": linear_metrics["aic"],
            },
        },
        "domain": {
            "energy_min_keV": float(np.min(energy)),
            "energy_max_keV": float(np.max(energy)),
            "width_min_tick": float(np.min(width)),
            "width_max_tick": float(np.max(width)),
        },
        "points": points,
    }


def equations(calibration: dict) -> tuple[str, str, str, str]:
    model = calibration["model"]
    tick_ns = calibration["tick_ns"]
    if model == "log":
        a = calibration["coefficients"]["a"]
        b = calibration["coefficients"]["b"]
        a_us = a * tick_ns / 1000.0
        b_us = b * tick_ns / 1000.0
        forward_tick = f"W_tick = {a:.10g}·ln(E_keV) {b:+.10g}"
        inverse_tick = f"E_keV = exp((W_tick {(-b):+.10g}) / {a:.10g})"
        forward_us = f"W_us = {a_us:.10g}·ln(E_keV) {b_us:+.10g}"
        inverse_us = f"E_keV = exp((W_us {(-b_us):+.10g}) / {a_us:.10g})"
    else:
        m = calibration["coefficients"]["m"]
        c = calibration["coefficients"]["c"]
        m_us = m * tick_ns / 1000.0
        c_us = c * tick_ns / 1000.0
        forward_tick = f"W_tick = {m:.10g}·E_keV {c:+.10g}"
        inverse_tick = f"E_keV = (W_tick {(-c):+.10g}) / {m:.10g}"
        forward_us = f"W_us = {m_us:.10g}·E_keV {c_us:+.10g}"
        inverse_us = f"E_keV = (W_us {(-c_us):+.10g}) / {m_us:.10g}"
    return forward_tick, inverse_tick, forward_us, inverse_us


def predict_energy(width_tick: float, calibration: dict) -> float:
    model = calibration["model"]
    if model == "log":
        a = calibration["coefficients"]["a"]
        b = calibration["coefficients"]["b"]
        if a == 0:
            raise ValueError("校准曲线斜率为0，无法反算能量。")
        return float(math.exp((width_tick - b) / a))
    m = calibration["coefficients"]["m"]
    c = calibration["coefficients"]["c"]
    if m == 0:
        raise ValueError("校准曲线斜率为0，无法反算能量。")
    return float((width_tick - c) / m)


def save_calibration_html(calibration: dict, filename: Path) -> Path:
    points = calibration["points"]
    energy = np.asarray([point["energy_keV"] for point in points], dtype=float)
    width = np.asarray([point["width_tick"] for point in points], dtype=float)
    labels = [point["nuclide"] for point in points]
    domain = calibration["domain"]
    plot_energy = np.geomspace(
        max(domain["energy_min_keV"] * 0.85, 1e-6),
        domain["energy_max_keV"] * 1.15,
        1000,
    )
    if calibration["model"] == "log":
        a = calibration["coefficients"]["a"]
        b = calibration["coefficients"]["b"]
        plot_width = a * np.log(plot_energy) + b
    else:
        m = calibration["coefficients"]["m"]
        c = calibration["coefficients"]["c"]
        plot_width = m * plot_energy + c

    forward_tick, inverse_tick, forward_us, inverse_us = equations(calibration)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=energy,
            y=width,
            mode="markers+text",
            text=labels,
            textposition="top center",
            marker={"size": 11, "color": "#4472C4"},
            name="手动校准点",
            hovertemplate="%{text}<br>E=%{x:.6g} keV<br>W=%{y:.4f} tick<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=plot_energy,
            y=plot_width,
            mode="lines",
            line={"color": "red", "width": 2.5},
            name=f"{calibration['model']}拟合",
        )
    )
    fig.update_layout(
        title=(
            f"脉宽—能量刻度<br><sup>{forward_tick}；{inverse_tick}<br>"
            f"{forward_us}；{inverse_us}<br>"
            f"R²={calibration['metrics']['r_squared']:.6f}，"
            f"RMSE={calibration['metrics']['rmse_tick']:.4g} tick</sup>"
        ),
        xaxis={"title": "能量 / keV", "type": "log"},
        yaxis={"title": "峰中心脉宽 / tick"},
        template="plotly_white",
        hovermode="closest",
    )
    ensure_parent(filename)
    fig.write_html(
        filename,
        include_plotlyjs=True,
        full_html=True,
        auto_open=False,
        config={"responsive": True, "scrollZoom": True},
    )
    return filename


def load_calibration(filename: str | Path) -> dict:
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"找不到刻度文件：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("model") not in {"log", "linear"}:
        raise ValueError("刻度文件格式或模型不受支持。")
    return data


def run_calibrate(args) -> None:
    tick_ns = 1e9 / args.timclk
    if args.point:
        points = [parse_point_spec(spec, args.unit, tick_ns) for spec in args.point]
    else:
        points = interactive_calibration_points(args.unit, tick_ns)
    calibration = fit_calibration(points, args.model, args.timclk)
    output = Path(args.output)
    ensure_parent(output)
    output.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    html_file = output.with_suffix(".html")
    save_calibration_html(calibration, html_file)
    forward_tick, inverse_tick, forward_us, inverse_us = equations(calibration)
    print("\n========== 刻度结果 ==========")
    print(forward_tick)
    print(inverse_tick)
    print(forward_us)
    print(inverse_us)
    print(f"R²={calibration['metrics']['r_squared']:.6f}")
    print(f"RMSE={calibration['metrics']['rmse_tick']:.6g} tick")
    print(
        "模型比较："
        f"log R²={calibration['comparison']['log']['r_squared']:.6f}，"
        f"linear R²={calibration['comparison']['linear']['r_squared']:.6f}"
    )
    print(f"刻度文件：{output.resolve()}")
    print(f"离线刻度图：{html_file.resolve()}")
    if args.open:
        webbrowser.open(html_file.resolve().as_uri())


def candidate_lines(energy_keV: float, tolerance_percent: float) -> list[dict]:
    matches = []
    for nuclide, info in NUCLIDE_DATABASE.items():
        for line in info["lines"]:
            reference = float(line["energy_keV"])
            error_percent = abs(energy_keV - reference) / reference * 100.0
            if error_percent <= tolerance_percent:
                matches.append(
                    {
                        "nuclide": nuclide,
                        "line": line["label"],
                        "reference_keV": reference,
                        "error_percent": error_percent,
                    }
                )
    return sorted(matches, key=lambda item: item["error_percent"])


def domain_warning(width_tick: float, calibration: dict) -> str | None:
    domain = calibration["domain"]
    low = domain["width_min_tick"]
    high = domain["width_max_tick"]
    if width_tick < low or width_tick > high:
        return f"超出校准脉宽范围[{low:.3f}, {high:.3f}] tick，属于外推"
    return None


def identify_widths(
    widths_tick: list[float],
    calibration: dict,
    tolerance_percent: float,
) -> tuple[list[dict], list[str]]:
    results = []
    source_hits: dict[str, set[float]] = defaultdict(set)
    for width_tick in widths_tick:
        energy = predict_energy(width_tick, calibration)
        matches = candidate_lines(energy, tolerance_percent)
        for match in matches:
            source_hits[match["nuclide"]].add(match["reference_keV"])
        results.append(
            {
                "width_tick": float(width_tick),
                "width_us": float(width_tick * calibration["tick_ns"] / 1000.0),
                "energy_keV": float(energy),
                "warning": domain_warning(width_tick, calibration),
                "matches": matches,
            }
        )

    summaries = []
    for nuclide, hits in source_hits.items():
        total_lines = len(NUCLIDE_DATABASE[nuclide]["lines"])
        if nuclide == "Co-60" and len(hits) >= 2:
            level = "较强候选（两条主线同时符合）"
        elif nuclide == "Co-57" and any(abs(value - 122.06065) < 0.01 for value in hits):
            level = "较强候选（122.06 keV主线符合）"
        elif len(hits) >= total_lines:
            level = "较强候选"
        else:
            level = "可能候选"
        summaries.append(f"{nuclide}: {level}，匹配{len(hits)}/{total_lines}条数据库谱线")
    if not summaries:
        summaries.append("当前四核素数据库中没有落入容差范围的候选。")
    return results, summaries


def print_identification(results: list[dict], summaries: list[str]) -> str:
    lines = ["========== 峰能量与候选核素 =========="]
    for index, result in enumerate(results, 1):
        lines.append(
            f"峰{index}: {result['width_tick']:.3f} tick"
            f" = {result['width_us']:.6f} μs"
            f" -> {result['energy_keV']:.3f} keV"
        )
        if result["warning"]:
            lines.append(f"  警告：{result['warning']}")
        if result["matches"]:
            for match in result["matches"]:
                lines.append(
                    f"  候选 {match['nuclide']}，{match['line']}，"
                    f"相对偏差={match['error_percent']:.2f}%"
                )
        else:
            lines.append("  无匹配谱线")
    lines.append("\n========== 源级别汇总 ==========")
    lines.extend(summaries)
    text = "\n".join(lines)
    print(text)
    return text


def run_predict(args) -> None:
    calibration = load_calibration(args.calibration)
    values = args.width
    if not values:
        raw = input(f"输入脉宽，单位{args.unit}：").strip()
        values = [float(raw)]
    widths_tick = [
        parse_width_to_tick(value, args.unit, calibration["tick_ns"])
        for value in values
    ]
    results, summaries = identify_widths(widths_tick, calibration, args.tolerance_percent)
    print_identification(results, summaries)


def load_analyze_csv(filename: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """读取analyze.py组合CSV、原始事件CSV或tick直方图CSV。"""
    rows = list(csv.reader(filename.open("r", newline="", encoding="utf-8-sig")))
    timclk = DEFAULT_TIMCLK

    # analyze.py的组合CSV。
    width_marker = next((i for i, row in enumerate(rows) if row and row[0].strip() == "width_rows"), None)
    if width_marker is not None and width_marker + 1 < len(rows):
        header = [item.strip() for item in rows[width_marker + 1]]
        values = []
        for row in rows[width_marker + 2 :]:
            if not row:
                continue
            item = dict(zip(header, row))
            if item.get("width_ticks"):
                values.append(float(item["width_ticks"]))
        # 从cycle_summary第一条数据读取时钟。
        cycle_marker = next((i for i, row in enumerate(rows) if row and row[0].strip() == "cycle_summary"), None)
        if cycle_marker is not None and cycle_marker + 2 < len(rows):
            cycle_header = [item.strip() for item in rows[cycle_marker + 1]]
            cycle_item = dict(zip(cycle_header, rows[cycle_marker + 2]))
            if cycle_item.get("timclk"):
                timclk = int(float(cycle_item["timclk"]))
        return events_to_histogram(np.asarray(values, dtype=float), None, None) + (timclk,)

    # 普通表格：tick/count直方图或逐事件width_ticks。
    if not rows:
        raise ValueError("CSV为空。")
    header = [item.strip() for item in rows[0]]
    data = [dict(zip(header, row)) for row in rows[1:] if row]
    tick_name = "tick" if "tick" in header else "width_ticks" if "width_ticks" in header else None
    if tick_name is None:
        raise ValueError("CSV中没有tick或width_ticks列。")
    if "count" in header:
        x = np.asarray([float(item[tick_name]) for item in data], dtype=float)
        y = np.asarray([float(item["count"]) for item in data], dtype=float)
        return x, y, timclk
    values = np.asarray([float(item[tick_name]) for item in data], dtype=float)
    return events_to_histogram(values, None, None) + (timclk,)


def events_to_histogram(
    events: np.ndarray,
    tick_min: float | None,
    tick_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    if len(events) == 0:
        raise ValueError("没有脉宽事件。")
    low = int(math.floor(np.min(events) if tick_min is None else tick_min))
    high = int(math.ceil(np.max(events) if tick_max is None else tick_max))
    if high < low:
        low, high = high, low
    centers = np.arange(low, high + 1, dtype=float)
    counts = np.zeros(len(centers), dtype=float)
    rounded = np.rint(events).astype(int)
    values, numbers = np.unique(rounded[(rounded >= low) & (rounded <= high)], return_counts=True)
    counts[values - low] = numbers
    return centers, counts


def crop_histogram(
    ticks: np.ndarray,
    counts: np.ndarray,
    tick_min: float | None,
    tick_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    mask = np.ones(len(ticks), dtype=bool)
    if tick_min is not None:
        mask &= ticks >= tick_min
    if tick_max is not None:
        mask &= ticks <= tick_max
    return ticks[mask], counts[mask]


def detect_peak_ticks(
    ticks: np.ndarray,
    counts: np.ndarray,
    prominence: float | None,
    distance: int,
    max_peaks: int,
    smooth_sigma: float,
    refine_half_width: int,
) -> tuple[list[float], np.ndarray]:
    if len(ticks) < 5 or np.max(counts) <= 0:
        raise ValueError("直方图有效数据不足。")
    smooth = gaussian_filter1d(counts.astype(float), sigma=max(smooth_sigma, 0.0))
    if prominence is None:
        prominence = max(3.0, 0.05 * float(np.max(smooth)))
    indices, properties = find_peaks(smooth, prominence=prominence, distance=max(distance, 1))
    if len(indices) == 0:
        raise ValueError("没有自动找到峰。请降低--prominence或用--peak-ticks手动指定峰位。")
    ranked = sorted(
        zip(indices, properties["prominences"]),
        key=lambda item: item[1],
        reverse=True,
    )[:max_peaks]
    refined = []
    for index, _prominence in ranked:
        left = max(index - refine_half_width, 0)
        right = min(index + refine_half_width + 1, len(ticks))
        local_x = ticks[left:right]
        local_y = counts[left:right].astype(float)
        local_weight = np.maximum(local_y - np.min(local_y), 0.0)
        if np.sum(local_weight) > 0:
            refined_tick = float(np.sum(local_x * local_weight) / np.sum(local_weight))
        else:
            refined_tick = float(ticks[index])
        refined.append(refined_tick)
    return sorted(refined), smooth


def save_identification_html(
    filename: Path,
    ticks: np.ndarray,
    counts: np.ndarray,
    results: list[dict],
    title: str,
) -> Path:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=ticks,
            y=counts,
            name="脉宽直方图",
            hovertemplate="W=%{x:.3f} tick<br>count=%{y:.0f}<extra></extra>",
            marker_color="#4472C4",
        )
    )
    ymax = float(np.max(counts)) if len(counts) else 1.0
    for result in results:
        candidates = "、".join(match["nuclide"] for match in result["matches"]) or "无候选"
        fig.add_vline(
            x=result["width_tick"],
            line_dash="dash",
            line_color="red",
            annotation_text=f"{result['energy_keV']:.1f} keV / {candidates}",
            annotation_position="top",
        )
    fig.update_layout(
        title=title,
        xaxis_title="脉宽 / tick",
        yaxis_title="计数",
        template="plotly_white",
        hovermode="closest",
        bargap=0.04,
        yaxis_range=[0, ymax * 1.16],
    )
    ensure_parent(filename)
    fig.write_html(
        filename,
        include_plotlyjs=True,
        full_html=True,
        auto_open=False,
        config={"responsive": True, "scrollZoom": True},
    )
    return filename


def run_identify_csv(args) -> None:
    calibration = load_calibration(args.calibration)
    ticks, counts, timclk = load_analyze_csv(Path(args.csv))
    ticks, counts = crop_histogram(ticks, counts, args.tick_min, args.tick_max)
    if args.peak_ticks:
        peak_ticks = [float(value) for value in args.peak_ticks]
    else:
        peak_ticks, _smooth = detect_peak_ticks(
            ticks,
            counts,
            args.prominence,
            args.distance,
            args.max_peaks,
            args.smooth_sigma,
            args.refine_half_width,
        )
    results, summaries = identify_widths(peak_ticks, calibration, args.tolerance_percent)
    report = print_identification(results, summaries)
    base = Path(args.output) if args.output else Path(args.csv).with_suffix("").with_name(Path(args.csv).stem + "_identification")
    txt_file = base.with_suffix(".txt")
    json_file = base.with_suffix(".json")
    html_file = base.with_suffix(".html")
    ensure_parent(txt_file)
    txt_file.write_text(report + "\n", encoding="utf-8")
    json_file.write_text(
        json.dumps({"results": results, "source_summary": summaries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_identification_html(
        html_file,
        ticks,
        counts,
        results,
        f"可能核素识别：{Path(args.csv).name}",
    )
    if timclk != calibration["timclk_hz"]:
        print(
            f"提示：CSV记录TIMCLK={timclk} Hz，刻度文件TIMCLK={calibration['timclk_hz']} Hz。"
            "本程序按tick识别；若硬件时钟实际改变，应重新标定。"
        )
    print(f"识别报告：{txt_file.resolve()}")
    print(f"离线识别图：{html_file.resolve()}")
    if args.open:
        webbrowser.open(html_file.resolve().as_uri())


def run_identify_image(args) -> None:
    calibration = load_calibration(args.calibration)
    image = mpimg.imread(args.image)
    fig, axis = plt.subplots(figsize=(13, 8))
    axis.imshow(image)
    axis.set_title(
        f"依次点击横轴值 {args.x_values[0]:g} 和 {args.x_values[1]:g} tick 所在的位置"
    )
    reference_clicks = plt.ginput(2, timeout=-1)
    if len(reference_clicks) != 2 or reference_clicks[0][0] == reference_clicks[1][0]:
        plt.close(fig)
        raise RuntimeError("横轴参考点不足或重合。")
    axis.set_title("依次左键点击所有峰顶；完成后点击鼠标中键")
    peak_clicks = plt.ginput(-1, timeout=0)
    plt.close(fig)
    if not peak_clicks:
        raise RuntimeError("没有点击任何峰。")
    pixel0, pixel1 = reference_clicks[0][0], reference_clicks[1][0]
    tick0, tick1 = args.x_values
    peak_ticks = [
        tick0 + (click[0] - pixel0) * (tick1 - tick0) / (pixel1 - pixel0)
        for click in peak_clicks
    ]
    results, summaries = identify_widths(peak_ticks, calibration, args.tolerance_percent)
    report = print_identification(results, summaries)
    output = (
        Path(args.output)
        if args.output
        else Path(args.image).with_suffix("").with_name(Path(args.image).stem + "_identification.txt")
    )
    ensure_parent(output)
    output.write_text(report + "\n", encoding="utf-8")
    print(f"识别报告：{output.resolve()}")


def print_database() -> None:
    for nuclide, info in NUCLIDE_DATABASE.items():
        energies = ", ".join(f"{line['energy_keV']:.6g}" for line in info["lines"])
        print(f"{nuclide:7s}: {energies} keV")
        print(f"          {info['note']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TOT能量刻度与四核素初步识别")
    sub = parser.add_subparsers(dest="command", required=True)

    database = sub.add_parser("database", help="显示当前四核素数据库")
    database.set_defaults(func=lambda _args: print_database())

    calibrate = sub.add_parser("calibrate", help="手动输入峰位并建立能量刻度")
    calibrate.add_argument("--output", default="calibration.json")
    calibrate.add_argument("--model", choices=["log", "linear"], default="log")
    calibrate.add_argument("--unit", choices=["tick", "ns", "us"], default="tick")
    calibrate.add_argument("--timclk", type=int, default=DEFAULT_TIMCLK)
    calibrate.add_argument(
        "--point",
        action="append",
        help="非交互校准点ENERGY_KEV:WIDTH[:LABEL]；可以重复",
    )
    calibrate.add_argument("--open", action="store_true")
    calibrate.set_defaults(func=run_calibrate)

    predict = sub.add_parser("predict", help="由手动输入的脉宽反推能量")
    predict.add_argument("--calibration", default="calibration.json")
    predict.add_argument("--width", nargs="*", type=float)
    predict.add_argument("--unit", choices=["tick", "ns", "us"], default="tick")
    predict.add_argument("--tolerance-percent", type=float, default=10.0)
    predict.set_defaults(func=run_predict)

    identify_csv = sub.add_parser("identify-csv", help="分析analyze.py输出的CSV并给出可能核素")
    identify_csv.add_argument("--csv", required=True)
    identify_csv.add_argument("--calibration", default="calibration.json")
    identify_csv.add_argument("--peak-ticks", nargs="*", type=float)
    identify_csv.add_argument("--tick-min", type=float)
    identify_csv.add_argument("--tick-max", type=float)
    identify_csv.add_argument("--prominence", type=float)
    identify_csv.add_argument("--distance", type=int, default=5)
    identify_csv.add_argument("--max-peaks", type=int, default=8)
    identify_csv.add_argument("--smooth-sigma", type=float, default=1.0)
    identify_csv.add_argument("--refine-half-width", type=int, default=4)
    identify_csv.add_argument("--tolerance-percent", type=float, default=10.0)
    identify_csv.add_argument("--output")
    identify_csv.add_argument("--open", action="store_true")
    identify_csv.set_defaults(func=run_identify_csv)

    identify_image = sub.add_parser("identify-image", help="从旧能谱图片手动标横轴并点击峰位")
    identify_image.add_argument("--image", required=True)
    identify_image.add_argument("--calibration", default="calibration.json")
    identify_image.add_argument(
        "--x-values",
        nargs=2,
        type=float,
        required=True,
        metavar=("LEFT_TICK", "RIGHT_TICK"),
        help="准备在图片中点击的两个已知横轴tick值",
    )
    identify_image.add_argument("--tolerance-percent", type=float, default=10.0)
    identify_image.add_argument("--output")
    identify_image.set_defaults(func=run_identify_image)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
