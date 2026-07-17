import argparse
import csv
import struct
import time
from pathlib import Path

import matplotlib.pyplot as plt
import serial


INTER_FRAME_DELAY_S = 0.005
TIM2_CLOCK_HZ = 72_000_000

CMD_REGISTER = 0
CMD_START = 1
CMD_STOP = 2
CMD_CLEAR = 3

STATUS_REGISTER_START = 0
STATUS_REGISTER_COUNT = 20

HISTOGRAM_READ_CHUNK_REGS = 120


def modbus_crc(data: bytes) -> int:
    """计算Modbus RTU CRC16。"""
    crc = 0xFFFF

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

    return crc & 0xFFFF


def append_crc(data: bytes) -> bytes:
    crc = modbus_crc(data)

    # Modbus CRC在线路上低字节在前
    return data + struct.pack("<H", crc)


def read_exact(ser: serial.Serial, size: int) -> bytes:
    data = bytearray()

    while len(data) < size:
        part = ser.read(size - len(data))

        if not part:
            raise TimeoutError(
                f"串口接收超时：需要{size}字节，"
                f"实际收到{len(data)}字节"
            )

        data.extend(part)

    return bytes(data)


def read_modbus_response(
    ser: serial.Serial,
    expected_slave: int,
    expected_function: int,
) -> bytes:
    header = read_exact(ser, 2)

    slave = header[0]
    function = header[1]

    if function & 0x80:
        # 异常响应：
        # 地址、功能码|0x80、异常码、CRC低、CRC高
        frame = header + read_exact(ser, 3)
    elif function in (3, 4):
        byte_count_data = read_exact(ser, 1)
        byte_count = byte_count_data[0]

        frame = (
            header
            + byte_count_data
            + read_exact(ser, byte_count + 2)
        )
    elif function in (6, 16):
        # 06H和10H的正常响应共8字节
        frame = header + read_exact(ser, 6)
    else:
        raise RuntimeError(
            f"收到不支持的功能码：0x{function:02X}"
        )

    received_crc = struct.unpack("<H", frame[-2:])[0]
    calculated_crc = modbus_crc(frame[:-2])

    if received_crc != calculated_crc:
        raise RuntimeError(
            "CRC错误："
            f"接收0x{received_crc:04X}，"
            f"计算0x{calculated_crc:04X}"
        )

    if slave != expected_slave:
        raise RuntimeError(
            f"从站地址错误：收到{slave}，"
            f"期望{expected_slave}"
        )

    if function & 0x80:
        exception_code = frame[2]

        exception_names = {
            1: "非法功能",
            2: "非法数据地址",
            3: "非法数据值",
            4: "从站设备故障",
        }

        name = exception_names.get(
            exception_code,
            "未知异常",
        )

        raise RuntimeError(
            f"Modbus异常响应："
            f"代码{exception_code}，{name}"
        )

    if function != expected_function:
        raise RuntimeError(
            f"功能码错误：收到0x{function:02X}，"
            f"期望0x{expected_function:02X}"
        )

    return frame


def transact(
    ser: serial.Serial,
    request: bytes,
    slave: int,
    function: int,
) -> bytes:
    """
    完成一次请求—响应事务。
    5ms延时用于保证9600波特率下的RTU帧间隔。
    """
    time.sleep(INTER_FRAME_DELAY_S)

    ser.reset_input_buffer()
    ser.write(request)
    ser.flush()

    return read_modbus_response(
        ser,
        expected_slave=slave,
        expected_function=function,
    )


def write_single_register(
    ser: serial.Serial,
    slave: int,
    address: int,
    value: int,
):
    """06H写单个保持寄存器。"""
    pdu = struct.pack(
        ">BBHH",
        slave,
        0x06,
        address,
        value,
    )

    request = append_crc(pdu)

    response = transact(
        ser,
        request,
        slave,
        0x06,
    )

    # 06H正常响应应原样返回请求
    if response != request:
        raise RuntimeError(
            "06H响应内容与请求不一致"
        )


def read_input_registers(
    ser: serial.Serial,
    slave: int,
    address: int,
    count: int,
) -> list[int]:
    """04H读取输入寄存器。"""
    if count <= 0 or count > 125:
        raise ValueError(
            "04H单次寄存器数量必须为1～125"
        )

    pdu = struct.pack(
        ">BBHH",
        slave,
        0x04,
        address,
        count,
    )

    request = append_crc(pdu)

    response = transact(
        ser,
        request,
        slave,
        0x04,
    )

    byte_count = response[2]

    if byte_count != count * 2:
        raise RuntimeError(
            f"04H字节数错误：收到{byte_count}，"
            f"期望{count * 2}"
        )

    register_data = response[3:-2]

    return list(
        struct.unpack(
            ">" + "H" * count,
            register_data,
        )
    )


def combine_u32(high: int, low: int) -> int:
    return ((high & 0xFFFF) << 16) | (
        low & 0xFFFF
    )


def read_status(
    ser: serial.Serial,
    slave: int,
) -> dict:
    regs = read_input_registers(
        ser,
        slave,
        STATUS_REGISTER_START,
        STATUS_REGISTER_COUNT,
    )

    return {
        "marker": regs[0],
        "uptime_s": regs[1],
        "uptime_ms": combine_u32(
            regs[2],
            regs[3],
        ),
        "slave_address": regs[4],
        "baud_div_100": regs[5],
        "last_width_ticks": regs[6],
        "tim2_count": combine_u32(
            regs[7],
            regs[8],
        ),
        "capture_valid": regs[9],
        "tim1_count": combine_u32(
            regs[10],
            regs[11],
        ),
        "running": regs[12],
        "error_flags": regs[13],
        "histogram_bins": regs[14],
        "first_tick": regs[15],
        "ticks_per_bin": regs[16],
        "out_of_range": combine_u32(
            regs[17],
            regs[18],
        ),
        "histogram_register_start": regs[19],
    }


def read_histogram(
    ser: serial.Serial,
    slave: int,
    register_start: int,
    bin_count: int,
) -> list[int]:
    """
    一个桶占两个16位寄存器。
    每次读取120个寄存器，即60个桶。
    """
    total_registers = bin_count * 2
    all_registers = []

    offset = 0

    while offset < total_registers:
        count = min(
            HISTOGRAM_READ_CHUNK_REGS,
            total_registers - offset,
        )

        # 保证每次都读取完整的32位桶
        if count & 1:
            count -= 1

        print(
            f"读取直方图："
            f"地址={register_start + offset}，"
            f"寄存器数量={count}"
        )

        regs = read_input_registers(
            ser,
            slave,
            register_start + offset,
            count,
        )

        all_registers.extend(regs)
        offset += count

    histogram = []

    for i in range(bin_count):
        count = combine_u32(
            all_registers[i * 2],
            all_registers[i * 2 + 1],
        )

        histogram.append(count)

    return histogram


def save_histogram_csv(
    filename: Path,
    histogram: list[int],
    first_tick: int,
    ticks_per_bin: int,
):
    with filename.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.writer(file)

        writer.writerow([
            "bin",
            "width_ticks",
            "width_ns",
            "count",
        ])

        for bin_index, count in enumerate(histogram):
            width_ticks = (
                first_tick
                + bin_index * ticks_per_bin
            )

            width_ns = (
                width_ticks
                * 1_000_000_000
                / TIM2_CLOCK_HZ
            )

            writer.writerow([
                bin_index,
                width_ticks,
                f"{width_ns:.3f}",
                count,
            ])


def save_histogram_plot(
    filename: Path,
    histogram: list[int],
    first_tick: int,
    ticks_per_bin: int,
):
    ticks = [
        first_tick + i * ticks_per_bin
        for i in range(len(histogram))
    ]

    plt.figure(figsize=(12, 7))

    plt.bar(
        ticks,
        histogram,
        width=max(ticks_per_bin * 0.9, 0.8),
        color="#4472C4",
    )

    plt.xlabel("Pulse width / tick")
    plt.ylabel("Counts")
    plt.title(
        "Pulse Width Histogram "
        f"(N={sum(histogram)})"
    )

    plt.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(filename, dpi=160)
    plt.close()


def print_status(status: dict):
    print()
    print("========== 测量状态 ==========")
    print(
        f"固定标志       : "
        f"0x{status['marker']:04X}"
    )
    print(
        f"最新脉宽       : "
        f"{status['last_width_ticks']} ticks"
    )
    print(
        f"TIM2有效脉冲数 : "
        f"{status['tim2_count']}"
    )
    print(
        f"TIM1 ETR计数   : "
        f"{status['tim1_count']}"
    )
    print(
        f"运行状态       : "
        f"{status['running']}"
    )
    print(
        f"直方图桶数     : "
        f"{status['histogram_bins']}"
    )
    print(
        f"超范围计数     : "
        f"{status['out_of_range']}"
    )
    print(
        f"直方图起始地址 : "
        f"{status['histogram_register_start']}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "通过Modbus RTU读取STM32脉宽直方图"
        )
    )

    parser.add_argument(
        "--port",
        required=True,
        help="串口，例如COM5或/dev/ttyUSB0",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=9600,
    )

    parser.add_argument(
        "--slave",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="采集时间，单位秒",
    )

    parser.add_argument(
        "--name",
        default="modbus_histogram",
        help="输出文件名前缀",
    )

    args = parser.parse_args()

    csv_file = Path(args.name + ".csv")
    png_file = Path(args.name + ".png")

    ser = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=8,
        parity=serial.PARITY_NONE,
        stopbits=1,
        timeout=1.0,
    )

    measurement_started = False
    measurement_stopped = False

    try:
        print(
            f"已打开{args.port}，"
            f"{args.baud} baud"
        )

        initial_status = read_status(
            ser,
            args.slave,
        )

        if initial_status["marker"] != 0x1234:
            raise RuntimeError(
                "状态寄存器0不是0x1234，"
                "请检查固件和地址映射"
            )

        print("发送清零并开始命令……")

        write_single_register(
            ser,
            args.slave,
            CMD_REGISTER,
            CMD_START,
        )

        measurement_started = True

        start_time = time.monotonic()
        deadline = start_time + args.seconds

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            print(
                f"\r剩余采集时间："
                f"{remaining:6.1f} s",
                end="",
                flush=True,
            )

            time.sleep(min(1.0, remaining))

        print()
        print("发送停止命令……")

        write_single_register(
            ser,
            args.slave,
            CMD_REGISTER,
            CMD_STOP,
        )

        measurement_stopped = True

        # 等待状态寄存器更新
        time.sleep(0.05)

        status = read_status(
            ser,
            args.slave,
        )

        print_status(status)

        if status["running"] != 0:
            raise RuntimeError(
                "停止命令执行后，运行状态仍不为0"
            )

        histogram = read_histogram(
            ser,
            args.slave,
            status["histogram_register_start"],
            status["histogram_bins"],
        )

        histogram_sum = sum(histogram)
        histogram_total = (
            histogram_sum
            + status["out_of_range"]
        )

        print()
        print("========== 一致性检查 ==========")
        print(
            f"直方图范围内计数 : {histogram_sum}"
        )
        print(
            f"超范围计数       : "
            f"{status['out_of_range']}"
        )
        print(
            f"直方图总计数     : "
            f"{histogram_total}"
        )
        print(
            f"TIM2有效脉冲数   : "
            f"{status['tim2_count']}"
        )
        print(
            f"TIM1 ETR计数     : "
            f"{status['tim1_count']}"
        )
        print(
            f"TIM1-TIM2差值    : "
            f"{status['tim1_count'] - status['tim2_count']}"
        )

        if histogram_total == status["tim2_count"]:
            print("直方图与TIM2计数一致：通过")
        else:
            print("警告：直方图与TIM2计数不一致")

        save_histogram_csv(
            csv_file,
            histogram,
            status["first_tick"],
            status["ticks_per_bin"],
        )

        save_histogram_plot(
            png_file,
            histogram,
            status["first_tick"],
            status["ticks_per_bin"],
        )

        print()
        print(f"CSV已保存：{csv_file.resolve()}")
        print(f"图像已保存：{png_file.resolve()}")

    finally:
        if (
            measurement_started
            and not measurement_stopped
        ):
            try:
                write_single_register(
                    ser,
                    args.slave,
                    CMD_REGISTER,
                    CMD_STOP,
                )
                print("异常退出前已发送停止命令")
            except Exception:
                print("警告：异常退出时无法发送停止命令")

        ser.close()


if __name__ == "__main__":
    main()