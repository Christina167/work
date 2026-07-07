import serial
import numpy as np
import matplotlib.pyplot as plt

# ===== 配置 =====
PORT = 'COM6'          
BAUD = 115200
TIMEOUT = 2
ACQ_TIME = 1           # 采集秒数，可根据需要修改

# ===== 连接串口 =====
ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
ser.reset_input_buffer()

def send_cmd(cmd):
    ser.write((cmd + '\r\n').encode('ascii'))

# ===== 开始采集 =====
print(f"发送 start_{ACQ_TIME} ...")
send_cmd(f'start_{ACQ_TIME}')

# 等待自动采集完成提示
while True:
    line = ser.readline().decode(errors='ignore').strip()
    if line.startswith('Acquisition finished'):
        print(line)
        break

# ===== 导出数据 =====
print("发送 dump ...")
send_cmd('dump')

widths_ns = []
reading = False

while True:
    line = ser.readline().decode(errors='ignore').strip()
    if not line:
        continue
    if line.startswith('index,width_ns'):
        reading = True
        continue
    if line.startswith('END'):
        break
    if reading:
        parts = line.split(',')
        if len(parts) == 2:
            try:
                w = int(parts[1])
                widths_ns.append(w)
            except:
                pass

ser.close()

# ===== 数据分析 =====
if len(widths_ns) == 0:
    print("没有收到数据！请检查连接和串口号。")
    exit()

widths = np.array(widths_ns, dtype=float)

print(f"\n脉冲总数: {len(widths)}")
print(f"平均值  : {np.mean(widths):.2f} ns")
print(f"标准差  : {np.std(widths):.2f} ns")
print(f"最小值  : {np.min(widths):.2f} ns")
print(f"最大值  : {np.max(widths):.2f} ns")

# 保存原始数据
np.savetxt('width_data.csv', widths, delimiter=',', header='width_ns', comments='')
print("原始数据已保存至 width_data.csv")

# 绘制直方图
plt.figure(figsize=(10, 5))
plt.hist(widths, bins=50, edgecolor='black', alpha=0.7)
plt.xlabel('Pulse Width (ns)')
plt.ylabel('Counts')
plt.title(f'Pulse Width Distribution (N={len(widths)})')
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('width_histogram.png', dpi=200)
plt.show()