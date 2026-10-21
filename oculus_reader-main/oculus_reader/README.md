First follow then steps from [README.md](../README.md) in the repository root. Then install the packages from *requirements.txt* and continue the introduction in [README.dm](../README.md) in the root folder to run the code.

## Scripts description

The script [reader.py](reader.py) will automatically install the APK to the connected Oculus Quest and start program. By default it uses the APK delivered with the repository. If you decide to compile your own version of the app with the code from *app_source* folder, please adjust the `APK_path` argument when running `OculusReader.install` function.

Use script [install.py](install.py) to reinstall or uninstall the APK from the connected Oculus Quest.

## 三点固定空间数据（APK 1.4-three-points 起）

VR 面板和新增接收器使用相同坐标：X=-OpenXR Z、Y=-OpenXR X、Z=OpenXR Y，
单位为米；水平轴固定在参考空间中，不随当前视线旋转。`stage` 的 Z 是离地高度，
`local` 不是已校准的地面坐标。数据尚未映射到 H2O 仿真世界。

在项目根目录激活 `.venv` 后运行：

```bash
# USB
python -m oculus_reader.three_point_reader --serial 230YC01DB80182

# Wi-Fi；替换为头显当前 IP，不要使用旧网络的 IP
python -m oculus_reader.three_point_reader --ip 192.168.1.117

# 追加记录所有接收帧（终端打印频率不等于采样频率）
python -m oculus_reader.three_point_reader --ip 192.168.1.117 --record logs/quest_three_points.jsonl
```

接收器优先使用工作区 `.build-tools/android-sdk/platform-tools/adb`，避免系统旧 ADB
与新版服务冲突。首次启用无线 ADB 仍需通过 USB 执行 `adb -s USB序列号 tcpip 5555`，
再执行 `adb connect 头显IP:5555` 并接受授权。只有无线地址显示 `device` 后才拔 USB。
应用版本过旧时接收器会明确报错；安装新包使用 `adb -s 设备地址 install -r -t APK路径`。
`Ctrl+C` 停止接收器不会关闭 VR 面板。

Python 接口：

```python
import time
from oculus_reader.three_point_reader import ThreePointReader

with ThreePointReader(ip="192.168.1.117") as reader:
    while reader.connected:
        frame = reader.get_latest(max_age=0.5)
        # frame 为 None：没有新帧、最近接收已超过 0.5 秒或连接已断开。
        # frame 不为 None 时，也必须检查 focused 和各点 valid。
        if frame is not None:
            print(frame['points'])
        time.sleep(0.02)
```

每个 `QuestThreePoint` JSON 帧包含：

- `version`、`session_id`、`sequence`、`space_epoch`：协议、会话、序号和参考空间变更代数。
  会话或代数变化后，下游必须重新校准并重置速度/历史缓存。
- `sample_time_ns`：OpenXR 预测显示时刻，即本帧位姿对应的设备时钟时间，不是电脑时间，
  也不等于传感器采样或发送时刻。不可与电脑时钟直接相减当作链路延迟。
- `reference_space`：`stage` 或 `local`；`axes` 为 `flu`；`focused` 为应用焦点状态。
- `points.head/left/right.position_m`：与面板相同的位置，未四舍五入到面板的三位小数。
- 每点的 `valid/tracked` 为位置有效性及追踪状态；无效位置为 `null`，不以零坐标代替。
- `orientation_xyzw`：四元数，世界与设备坐标轴均按 FLU 换基（C R C^-1）；有独立的
  `orientation_valid/orientation_tracked`。该朝向不是手柄相对头部的朝向。
- `linear_velocity_m_s`：OpenXR 提供的同一参考空间中的线速度，已换轴；无有效速度时为 `null`。

JSONL 还附带 `host_receive_monotonic_ns` 和 `host_receive_unix_ns` 两个电脑接收时间。
接收超时使用电脑单调时钟，只判断接收新鲜度，不保证无线 ADB 中没有排队的旧帧。
无线断开后重新连接，再重启接收器；当前不自动重连，以便明确暴露失联。

旧的 `python -m oculus_reader.reader` 和 `get_transformations_and_buttons()` 保留原协议，
仍输出相对头部的双手位姿及按键，不能当成新的三点地面数据。
