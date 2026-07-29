#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SiPM / CsI(Tl) sigN 双指数波形拟合与 Multisim PWL 导出工具

模型（t >= t0）：
    V(t) = V0 + K * (1-exp(-(t-t0)/tau_r)) *
                    [a*exp(-(t-t0)/tau_fast)
                     +(1-a)*exp(-(t-t0)/tau_slow)]

这里拟合的是示波器测得的 sigN 电压，不是 SiPM 电流。
导出的 PWL 电压适合直接送到比较器输入端，先验证“sigN 波形 -> TTL 脉宽”。

依赖：
    python -m pip install numpy scipy matplotlib

交互运行：
    python sipm_double_exp_fit.py

快速运行内置起始数据（仅用于检查环境，不能代替真实测量）：
    python sipm_double_exp_fit.py --demo --no-show

从 CSV/TXT 读取两列数据（时间单位 us，电压单位 mV）：
    python sipm_double_exp_fit.py --points measured_points.csv
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    from scipy.optimize import least_squares
except ImportError as exc:
    raise SystemExit(
        "缺少 scipy。请先执行：\n"
        "python -m pip install numpy scipy matplotlib"
    ) from exc


# 这些点只是根据目前已知的 70 mV、0.88 us、2.4 us、15 us 等特征
# 制作的“软件试运行数据”。其中上升交点和中间尾部点是估计值，不是实测值。
DEMO_POINTS = np.array(
    [
        [0.000, 0.0],
        [0.350, 57.2],
        [0.880, 70.0],
        [2.750, 33.2],  # 0.350 us + 2.4 us
        [4.000, 21.0],
        [8.000, 7.0],
        [12.000, 2.3],
        [15.000, 0.7],
    ],
    dtype=float,
)


def ask_float(prompt: str, default: float) -> float:
    """带默认值的浮点数输入。"""
    while True:
        text = input(f"{prompt} [{default:g}]：").strip()
        if not text:
            return float(default)
        try:
            return float(text)
        except ValueError:
            print("请输入数字。")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        text = input(f"{prompt} [{hint}]：").strip().lower()
        if not text:
            return default
        if text in {"y", "yes", "1", "是"}:
            return True
        if text in {"n", "no", "0", "否"}:
            return False
        print("请输入 y 或 n。")


def parse_point_line(text: str) -> tuple[float, float]:
    fields = text.replace("，", ",").replace("\t", ",").split(",")
    fields = [x.strip() for x in fields if x.strip()]
    if len(fields) != 2:
        fields = text.split()
    if len(fields) != 2:
        raise ValueError("每行必须有两个数字：时间(us), 电压(mV)")
    return float(fields[0]), float(fields[1])


def add_extra_points(points: list[tuple[float, float]]) -> None:
    print(
        "\n可继续输入示波器光标读到的中间点，例如：4,21\n"
        "建议至少补充 4 us、8 us 附近的尾部点；直接回车结束。"
    )
    while True:
        text = input("附加点 t_us,v_mV：").strip()
        if not text:
            return
        try:
            points.append(parse_point_line(text))
        except ValueError as exc:
            print(exc)


def feature_wizard() -> tuple[np.ndarray, float]:
    print(
        "\n特征参数向导\n"
        "请用示波器光标读数。时间以脉冲开始时刻为 0 us。\n"
        "如果比较器有迟滞，上升阈值和下降阈值可以不同。"
    )
    baseline = ask_float("基线电压 / mV", 0.0)
    peak_v = ask_float("峰值 / mV", 70.0)
    peak_t = ask_float("达峰时间 / us", 0.88)
    rise_v = ask_float("比较器起跳时 sigN 电压 / mV", 57.2)
    rise_t = ask_float("sigN 上升到该电压的时间 / us（必须尽量实测）", 0.35)
    fall_v = ask_float("比较器恢复时 sigN 电压 / mV", 33.2)
    ttl_width = ask_float("对应 TTL 脉宽 / us", 2.4)
    end_t = ask_float("观察到接近基线的时间 / us", 15.0)
    end_v = ask_float("该时刻相对基线的剩余电压 / mV", 0.7)

    points: list[tuple[float, float]] = [
        (0.0, baseline),
        (rise_t, rise_v),
        (peak_t, peak_v),
        (rise_t + ttl_width, fall_v),
        (end_t, baseline + end_v),
    ]
    add_extra_points(points)
    return clean_points(np.asarray(points, dtype=float)), baseline


def manual_points() -> tuple[np.ndarray, float]:
    print(
        "\n逐点输入模式\n"
        "以脉冲开始为 t=0；每行输入：时间(us), 电压(mV)。\n"
        "至少需要 5 点，推荐 8～15 点，并覆盖上升、峰值和长尾。\n"
        "输入示例：0.88,70；直接回车结束。"
    )
    points: list[tuple[float, float]] = []
    while True:
        text = input("测量点 t_us,v_mV：").strip()
        if not text:
            break
        try:
            points.append(parse_point_line(text))
        except ValueError as exc:
            print(exc)
    if len(points) < 5:
        raise SystemExit("测量点少于 5 个，无法可靠拟合。")
    arr = clean_points(np.asarray(points, dtype=float))
    baseline = ask_float("固定基线电压 / mV", float(arr[0, 1]))
    return arr, baseline


def load_points(path: Path) -> tuple[np.ndarray, float]:
    rows: list[tuple[float, float]] = []
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        try:
            rows.append(parse_point_line(line))
        except ValueError:
            # 允许第一行为英文/中文表头，其他坏行则明确报错。
            if not rows:
                continue
            raise SystemExit(f"{path} 第 {line_no} 行无法解析：{raw}")
    if len(rows) < 5:
        raise SystemExit("文件中的有效测量点少于 5 个。")
    arr = clean_points(np.asarray(rows, dtype=float))
    return arr, float(arr[0, 1])


def clean_points(points: np.ndarray) -> np.ndarray:
    if points.ndim != 2 or points.shape[1] != 2:
        raise SystemExit("测量点格式必须是两列：t_us, v_mV。")
    if not np.all(np.isfinite(points)):
        raise SystemExit("测量点包含无穷大或非数字。")
    points = points[np.argsort(points[:, 0])]
    if np.any(points[:, 0] < 0):
        raise SystemExit("时间不能小于 0。请把脉冲开始时刻平移到 0 us。")

    # 同一时刻重复输入时取电压平均值。
    unique_t = np.unique(points[:, 0])
    merged = np.array(
        [[t, np.mean(points[points[:, 0] == t, 1])] for t in unique_t],
        dtype=float,
    )
    if len(merged) < 5:
        raise SystemExit("去除重复时间后少于 5 个有效点。")
    return merged


def sigmoid(x: float | np.ndarray) -> float | np.ndarray:
    # 稳定的 logistic，避免 exp 溢出。
    x_arr = np.asarray(x)
    out = np.empty_like(x_arr, dtype=float)
    pos = x_arr >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x_arr[pos]))
    exp_x = np.exp(x_arr[~pos])
    out[~pos] = exp_x / (1.0 + exp_x)
    return float(out) if np.ndim(x) == 0 else out


def unpack(q: np.ndarray) -> tuple[float, float, float, float, float]:
    """
    优化变量转物理参数。
    tau_slow = tau_fast + delta，天然保证 tau_slow > tau_fast。
    """
    k = math.exp(float(q[0]))
    tau_r = math.exp(float(q[1]))
    tau_fast = math.exp(float(q[2]))
    tau_slow = tau_fast + math.exp(float(q[3]))
    fraction_fast = float(sigmoid(q[4]))
    return k, tau_r, tau_fast, tau_slow, fraction_fast


def waveform(
    t_us: np.ndarray | float,
    q: np.ndarray,
    baseline_mv: float,
) -> np.ndarray:
    t = np.maximum(np.asarray(t_us, dtype=float), 0.0)
    k, tau_r, tau_fast, tau_slow, fraction_fast = unpack(q)
    rise = 1.0 - np.exp(-t / tau_r)
    decay = (
        fraction_fast * np.exp(-t / tau_fast)
        + (1.0 - fraction_fast) * np.exp(-t / tau_slow)
    )
    return baseline_mv + k * rise * decay


def fit_model(
    points: np.ndarray,
    baseline_mv: float,
    seed: int = 20260728,
) -> tuple[np.ndarray, dict[str, float]]:
    t = points[:, 0]
    y = points[:, 1]
    y_span = max(float(np.ptp(y)), float(np.max(np.abs(y - baseline_mv))), 1.0)
    peak_idx = int(np.argmax(y))
    peak_t = max(float(t[peak_idx]), 1e-3)
    t_end = max(float(t[-1]), peak_t * 2.0)

    # 峰值点权重大一些；第一个和最后一个点稍微加权。
    weights = np.ones_like(y)
    weights[peak_idx] = 3.0
    weights[0] = max(weights[0], 1.5)
    weights[-1] = max(weights[-1], 1.5)

    # 物理上较宽、但有限的搜索范围，单位均为 us。
    lower = np.array(
        [
            math.log(max(y_span * 0.05, 1e-6)),  # K
            math.log(0.001),                    # tau_r: 1 ns
            math.log(0.005),                    # tau_fast: 5 ns
            math.log(0.010),                    # slow-fast 差值
            -9.0,                               # fast fraction
        ]
    )
    upper = np.array(
        [
            math.log(max(y_span * 100.0, 10.0)),
            math.log(max(t_end * 2.0, 2.0)),
            math.log(max(t_end * 5.0, 10.0)),
            math.log(max(t_end * 20.0, 50.0)),
            9.0,
        ]
    )

    def residual(q: np.ndarray) -> np.ndarray:
        voltage_res = weights * (waveform(t, q, baseline_mv) - y) / y_span

        # 峰值处导数应接近零，使“达峰时间”参与拟合，而不只是一个电压点。
        h = max(peak_t * 1e-4, 1e-6)
        yp = waveform(peak_t + h, q, baseline_mv)
        ym = waveform(max(peak_t - h, 0.0), q, baseline_mv)
        derivative = float((yp - ym) / (2.0 * h))
        peak_constraint = 2.0 * derivative * peak_t / y_span
        return np.append(voltage_res, peak_constraint)

    rng = np.random.default_rng(seed)
    best = None
    base_guesses: Iterable[tuple[float, float, float, float, float]] = [
        (y_span * 1.5, peak_t / 3.0, peak_t, t_end / 3.0, 0.75),
        (y_span * 2.0, peak_t / 5.0, peak_t * 2.0, t_end / 2.0, 0.50),
        (y_span * 1.2, peak_t / 2.0, peak_t / 2.0, t_end, 0.85),
    ]

    guesses: list[np.ndarray] = []
    for k, tr, tf, ts, frac in base_guesses:
        ts = max(ts, tf + 0.01)
        guesses.append(
            np.array(
                [
                    math.log(max(k, 1e-6)),
                    math.log(max(tr, 0.001)),
                    math.log(max(tf, 0.005)),
                    math.log(max(ts - tf, 0.010)),
                    math.log(frac / (1.0 - frac)),
                ]
            )
        )
    # 多起点降低稀疏数据下陷入局部最优的概率。
    for _ in range(24):
        guesses.append(lower + rng.random(5) * (upper - lower))

    for q0 in guesses:
        q0 = np.minimum(np.maximum(q0, lower + 1e-10), upper - 1e-10)
        result = least_squares(
            residual,
            q0,
            bounds=(lower, upper),
            max_nfev=20000,
            x_scale="jac",
        )
        if best is None or np.sum(result.fun**2) < np.sum(best.fun**2):
            best = result

    assert best is not None
    q = best.x
    pred = waveform(t, q, baseline_mv)
    errors = pred - y
    rmse = float(np.sqrt(np.mean(errors**2)))
    ss_res = float(np.sum(errors**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    dense_t = np.linspace(0.0, max(t_end, peak_t * 2.0), 10001)
    dense_y = waveform(dense_t, q, baseline_mv)
    dense_peak_idx = int(np.argmax(dense_y))
    metrics = {
        "rmse_mv": rmse,
        "r2": r2,
        "fitted_peak_t_us": float(dense_t[dense_peak_idx]),
        "fitted_peak_mv": float(dense_y[dense_peak_idx]),
        "n_points": float(len(points)),
    }
    return q, metrics


def formula_text(q: np.ndarray, baseline_mv: float) -> str:
    k, tau_r, tau_fast, tau_slow, fraction_fast = unpack(q)
    slow_fraction = 1.0 - fraction_fast
    return (
        "令 x = t_us - t0_us。\n"
        "当 x < 0：V_sigN = V0。\n"
        "当 x >= 0：\n"
        f"V_sigN[mV] = {baseline_mv:.9g} + {k:.9g}"
        f" * (1 - exp(-x/{tau_r:.9g}))\n"
        f"             * ({fraction_fast:.9g}*exp(-x/{tau_fast:.9g})"
        f" + {slow_fraction:.9g}*exp(-x/{tau_slow:.9g}))\n"
    )


def export_results(
    points: np.ndarray,
    baseline_mv: float,
    q: np.ndarray,
    metrics: dict[str, float],
    output_dir: Path,
    end_us: float,
    dt_ns: float,
    delay_us: float,
    show_plot: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    end_us = max(end_us, float(points[-1, 0]))
    dt_us = dt_ns / 1000.0
    if dt_us <= 0:
        raise SystemExit("PWL 采样间隔必须大于 0。")

    # 拟合图使用更密的网格；PWL 使用用户指定采样间隔。
    fit_t = np.linspace(0.0, end_us, 5000)
    fit_y = waveform(fit_t, q, baseline_mv)
    pwl_local_t = np.arange(0.0, end_us + dt_us * 0.5, dt_us)
    if pwl_local_t[-1] < end_us:
        pwl_local_t = np.append(pwl_local_t, end_us)
    pwl_y_mv = waveform(pwl_local_t, q, baseline_mv)

    # Multisim PWL 常用 SI 单位：第一列秒、第二列伏。
    pwl_t_s = (pwl_local_t + delay_us) * 1e-6
    pwl_v = pwl_y_mv * 1e-3
    pwl_path = output_dir / "sigN_pwl_voltage.txt"
    with pwl_path.open("w", encoding="utf-8", newline="\n") as file:
        # 文件只保留数字，兼容不接受注释行的 Multisim 版本。
        file.write(f"{0.0:.12e} {baseline_mv * 1e-3:.12e}\n")
        start_idx = 1 if abs(float(pwl_t_s[0])) < 1e-18 else 0
        for ts, vv in zip(pwl_t_s[start_idx:], pwl_v[start_idx:]):
            file.write(f"{ts:.12e} {vv:.12e}\n")
        # 末端再保持 1 us，避免仿真器在文件末端产生不明确状态。
        file.write(f"{(delay_us + end_us + 1.0) * 1e-6:.12e} {pwl_v[-1]:.12e}\n")

    sampled_path = output_dir / "fitted_samples_us_mV.csv"
    np.savetxt(
        sampled_path,
        np.column_stack((pwl_local_t, pwl_y_mv)),
        delimiter=",",
        header="time_us,voltage_mV",
        comments="",
        fmt="%.12g",
    )

    input_path = output_dir / "input_points_us_mV.csv"
    np.savetxt(
        input_path,
        points,
        delimiter=",",
        header="time_us,voltage_mV",
        comments="",
        fmt="%.12g",
    )

    k, tau_r, tau_fast, tau_slow, fraction_fast = unpack(q)
    report = (
        "SiPM / CsI(Tl) sigN 双指数拟合结果\n"
        "====================================\n\n"
        + formula_text(q, baseline_mv)
        + "\n参数：\n"
        + f"V0              = {baseline_mv:.9g} mV\n"
        + f"K               = {k:.9g} mV\n"
        + f"tau_r           = {tau_r:.9g} us\n"
        + f"tau_fast        = {tau_fast:.9g} us\n"
        + f"tau_slow        = {tau_slow:.9g} us\n"
        + f"fast_fraction   = {fraction_fast:.9g}\n"
        + f"slow_fraction   = {1.0 - fraction_fast:.9g}\n\n"
        + "拟合质量：\n"
        + f"输入点数        = {int(metrics['n_points'])}\n"
        + f"RMSE            = {metrics['rmse_mv']:.6g} mV\n"
        + f"R^2             = {metrics['r2']:.8g}\n"
        + f"拟合峰值        = {metrics['fitted_peak_mv']:.6g} mV\n"
        + f"拟合达峰时间    = {metrics['fitted_peak_t_us']:.6g} us\n\n"
        + "PWL 文件单位：第一列 s，第二列 V。\n"
        + "该 PWL 表示 sigN 电压，默认直接接比较器输入端。\n"
        + "若保留 SiPM 等效电容、R4、C31，则需要以该曲线为目标，另行反求电流源参数；\n"
        + "不能简单把这里的电压数值原样填入 PWL_CURRENT。\n"
    )
    (output_dir / "fit_result.txt").write_text(report, encoding="utf-8")

    try:
        import matplotlib

        if not show_plot:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit(
            "已完成数值拟合，但缺少 matplotlib，无法画图。\n"
            "请执行：python -m pip install matplotlib"
        ) from exc

    fig, (ax, ax_res) = plt.subplots(
        2,
        1,
        figsize=(10.5, 7.2),
        gridspec_kw={"height_ratios": [3.2, 1.0]},
        sharex=True,
    )
    ax.plot(fit_t, fit_y, color="#1464F4", lw=2.0, label="Double-exponential fit")
    ax.scatter(
        points[:, 0],
        points[:, 1],
        s=52,
        color="#E84855",
        edgecolor="white",
        linewidth=0.8,
        zorder=3,
        label="Oscilloscope landmark points",
    )
    ax.axvline(
        metrics["fitted_peak_t_us"],
        color="#666666",
        ls="--",
        lw=1.0,
        alpha=0.8,
    )
    ax.set_ylabel("sigN voltage / mV")
    ax.set_title("SiPM + CsI(Tl) sigN double-exponential fit")
    ax.grid(True, alpha=0.25)
    ax.legend()
    info = (
        f"tau_r={tau_r:.4g} us\n"
        f"tau_fast={tau_fast:.4g} us\n"
        f"tau_slow={tau_slow:.4g} us\n"
        f"fast fraction={fraction_fast:.3f}\n"
        f"RMSE={metrics['rmse_mv']:.3g} mV, R²={metrics['r2']:.4f}"
    )
    ax.text(
        0.985,
        0.965,
        info,
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9.5,
        bbox={"boxstyle": "round,pad=0.45", "fc": "white", "ec": "#BBBBBB", "alpha": 0.9},
    )

    point_pred = waveform(points[:, 0], q, baseline_mv)
    residual_mv = point_pred - points[:, 1]
    ax_res.axhline(0.0, color="#333333", lw=1.0)
    ax_res.vlines(points[:, 0], 0.0, residual_mv, color="#7B61A8", lw=1.5)
    ax_res.scatter(points[:, 0], residual_mv, color="#7B61A8", s=28)
    ax_res.set_xlabel("Time after pulse onset / us")
    ax_res.set_ylabel("Residual\n/ mV")
    ax_res.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "sigN_double_exp_fit.png", dpi=180)
    if show_plot:
        plt.show()
    plt.close(fig)

    print("\n拟合完成：")
    print(formula_text(q, baseline_mv))
    print(f"RMSE = {metrics['rmse_mv']:.6g} mV，R² = {metrics['r2']:.8g}")
    print(f"拟合峰值 = {metrics['fitted_peak_mv']:.6g} mV")
    print(f"拟合达峰时间 = {metrics['fitted_peak_t_us']:.6g} us")
    print(f"\n输出目录：{output_dir.resolve()}")
    print("  fit_result.txt               拟合公式、参数和误差")
    print("  sigN_double_exp_fit.png      拟合曲线、输入点和残差")
    print("  sigN_pwl_voltage.txt         Multisim PWL：秒、伏")
    print("  fitted_samples_us_mV.csv     便于检查的微秒、毫伏采样表")
    print("  input_points_us_mV.csv       本次实际使用的输入点")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="根据稀疏示波器特征点拟合 SiPM/CsI(Tl) 双指数 sigN 波形。"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--demo",
        action="store_true",
        help="使用内置试运行数据；这些数据不能代替真实示波器读数。",
    )
    source.add_argument(
        "--points",
        type=Path,
        help="读取两列 t_us,v_mV 的 CSV/TXT 文件。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sipm_fit_output"),
        help="输出目录，默认 ./sipm_fit_output",
    )
    parser.add_argument("--end-us", type=float, default=None, help="PWL 结束时间 / us")
    parser.add_argument("--dt-ns", type=float, default=10.0, help="PWL 采样间隔 / ns")
    parser.add_argument(
        "--delay-us",
        type=float,
        default=1.0,
        help="PWL 中脉冲开始前的基线延迟 / us",
    )
    parser.add_argument("--no-show", action="store_true", help="只保存图片，不弹出窗口")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.demo:
        points = DEMO_POINTS.copy()
        baseline_mv = 0.0
        print("警告：正在使用估计的试运行数据，不代表真实 SiPM 波形。")
    elif args.points:
        points, baseline_mv = load_points(args.points)
    else:
        print(
            "SiPM / CsI(Tl) 双指数波形拟合\n"
            "================================\n"
            "1：按峰值、阈值、TTL 脉宽等特征输入（推荐首次使用）\n"
            "2：逐点输入示波器光标数据（数据多时更可靠）\n"
            "3：使用内置估计数据检查软件环境"
        )
        mode = input("请选择 [1]：").strip() or "1"
        if mode == "1":
            points, baseline_mv = feature_wizard()
        elif mode == "2":
            points, baseline_mv = manual_points()
        elif mode == "3":
            points = DEMO_POINTS.copy()
            baseline_mv = 0.0
            print("警告：内置数据含估计点，只用于检查程序。")
        else:
            raise SystemExit("无效选择。")

    if len(points) < 7:
        print(
            "\n提示：当前有效点少于 7 个。双指数包含多个相关参数，"
            "结果可能不唯一；建议补测 2～4 个尾部点。"
        )

    q, metrics = fit_model(points, baseline_mv)
    default_end = max(float(points[-1, 0]) + 5.0, 20.0)
    end_us = args.end_us if args.end_us is not None else default_end

    if not (args.demo or args.points):
        end_us = ask_float("PWL 总波形时长 / us", end_us)
        args.dt_ns = ask_float("PWL 采样间隔 / ns", args.dt_ns)
        args.delay_us = ask_float("PWL 脉冲前基线延迟 / us", args.delay_us)
        show_plot = ask_yes_no("运行完成后弹出拟合图", True)
    else:
        show_plot = not args.no_show

    export_results(
        points=points,
        baseline_mv=baseline_mv,
        q=q,
        metrics=metrics,
        output_dir=args.output_dir,
        end_us=end_us,
        dt_ns=args.dt_ns,
        delay_us=args.delay_us,
        show_plot=show_plot,
    )


if __name__ == "__main__":
    main()
