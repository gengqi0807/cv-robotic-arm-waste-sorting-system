# 板子端（机械臂）代码说明

这一目录是机械臂（Atlas 200I DK A2 + DOFBOT）上运行的代码。电脑上的前端入口将板端请求转发给后端，板端本地控制台也可单独运行。 [电脑端启动命令](../README.md#4-分开启动)

## 目录结构

```
arm/
├── dofbot_garbage_yolov5/          ROS2 包（识别 + 抓取 + 任务动作）
│   ├── main.py                     主程序：识别循环、任务轮询、动作分发
│   ├── garbage_identify.py         YOLOv5 推理 + 透视变换 + 逆解（IK）
│   ├── garbage_grap.py             抓取 + 投放动作序列
│   ├── stack_runner.py             叠罗汉 / 积木搬运工（按示教点执行）
│   ├── robot_bridge.py             HTTP 客户端：轮询命令 / 上报状态 / 上传画面
│   ├── web_server.py               本地零依赖 Web 控制台（备用，不依赖 Flask）
│   ├── voice_control.py            语音控制：录音 + WeNet 识别 + 命令匹配
│   ├── npu_utils.py / det_utils.py NPU 推理与后处理工具
│   ├── config/                     标定数据（透视变换四角、XYT、偏移）
│   └── robot_bridge.env.example    连接 Flask 的配置模板
├── arm_basic/                      示教点
│   ├── stack_poses.json            叠罗汉 13/17 个点
│   └── garbage_poses.json          分拣示教点
└── tools/                          辅助脚本（需要在板子上跑）
    ├── teach_stack13.py            13 点示教（ready + 4 抓 + 4 过渡 + 4 放）
    ├── teach_place4.py             只重学 4 个放置点
    ├── teach_place_above4.py       只重学 4 个放置点上方过渡点
    ├── live_adjust_cli.py          键盘微调姿态并读出角度
    └── speech_recognition.py       WeNet ASR 封装（fbank + CMVN + CTC 解码）
```

## 没有放进仓库的东西（需要自己在板子上准备）

| 缺的 | 从哪来 |
|---|---|
| `model/yolov5s_bs1.om`（14.5 MB） | 课程提供的 YOLOv5s 量化模型，放在 `dofbot_garbage_yolov5/model/` |
| `~/speech_recognition/offline_encoder.om`（75 MB）+ `vocab.txt` | 课程 `wenet.zip` |
| conda 环境 `ros-arm-env` | 按课程文档装（Python 3.10 + torch + ais_bench + Arm_Lib） |
| ROS2 Humble 工作空间编译产物 | `colcon build` |

## 部署位置（板子上）

```
/home/HwHiAiUser/RobotArm-a/
├── ros2_dofbot_ws/
│   └── src/dofbot_garbage_yolov5/dofbot_garbage_yolov5/   ← dofbot_garbage_yolov5/ 里的文件
├── arm_basic/                                               ← arm_basic/ 里的 json
└── live_adjust_cli.py                                       ← tools/ 里的脚本
```

## 环境准备

```bash
cd /home/HwHiAiUser/RobotArm-a
conda activate ros-arm-env
. ./ros2_dofbot_ws/setenv.sh
export PYTHONPATH=$(ls -d /root/miniconda3/envs/ros-arm-env/lib/python3.10/site-packages/*.egg | tr '\n' ':')/root/miniconda3/envs/ros-arm-env/lib/python3.10/site-packages:$PYTHONPATH
```

> `PYTHONPATH` 必须同时包含 `.egg`（Arm_Lib）和 `site-packages`（ais_bench），缺一个就 `ModuleNotFoundError`。

## 连接前端（联调模式）

1. 复制配置模板并填自己的值：

```bash
cp arm/dofbot_garbage_yolov5/robot_bridge.env.example \
   /home/HwHiAiUser/RobotArm-a/ros2_dofbot_ws/src/dofbot_garbage_yolov5/dofbot_garbage_yolov5/robot_bridge.env
```

2. 编辑 `robot_bridge.env`：

```bash
export ROBOT_BRIDGE_URL="http://<跑 Flask 的电脑 IP>:5000"
export ROBOT_BRIDGE_TOKEN="<和 Flask 端 .env 里一致的令牌>"
export ROBOT_BRIDGE_DEVICE_IP="<板子自己的 IP>"
```

3. 启动（联调模式，数据上报到 Flask）：

```bash
unset ROBOT_BRIDGE_URL ROBOT_BRIDGE_TOKEN ROBOT_BRIDGE_DEVICE_IP   # 清掉 shell 里的旧值
export ROBOT_BRIDGE_LOCAL=0
/usr/bin/python3 src/dofbot_garbage_yolov5/dofbot_garbage_yolov5/main.py
```

看到这两行就说明连上了：

```
[Web配置] 已加载 .../robot_bridge.env（不显示令牌）
[联调模式] Flask=http://<电脑IP>:5000  连接=成功
```

4. 不连前端（单机模式，板子自己起 Web 服务）：

```bash
/usr/bin/python3 src/dofbot_garbage_yolov5/dofbot_garbage_yolov5/main.py
# 浏览器打开 http://<板子IP>:5000/
```

## 示教点怎么调

叠罗汉 / 积木搬运工完全依赖 `arm_basic/stack_poses.json` 里的示教点，**换机械臂或换了积木摆放位置就必须重新示教**。

```bash
cd /home/HwHiAiUser/RobotArm-a/arm_basic
cp stack_poses.json stack_poses.json.bak      # 先备份
/usr/bin/python3 <仓库>/arm/tools/teach_stack13.py
```

- 每个点：进学习模式（松扭矩）→ 手掰 → 回车 → 自动读 5 个角度
- `ready` 要高于所有积木，且不挡摄像头
- `pick_<色>_above` 和 `pick_<色>` 的 XY 相同，只是抬高 10~15cm
- `place_layer1~4` 的 XY 相同，只有高度逐层增加

只重学某一个点：

```bash
/usr/bin/python3 <仓库>/arm/tools/teach_place4.py          # 4 个放置点
/usr/bin/python3 <仓库>/arm/tools/teach_place_above4.py    # 4 个放置点上方过渡点
```

## 语音控制

需要 **带麦克风的 4 段（TRRS）耳机**插在 USB 声卡上；摄像头和声卡 USB 供电紧张，建议用带电源的 HUB。

```bash
# 单独测语音识别
export PYTHONPATH=...:$PYTHONPATH
/usr/bin/python3 arm/tools/speech_recognition.py
```

## 已知硬件约束

| 现象 | 原因 | 处理 |
|---|---|---|
| `Arm_serial_servo_write6 I2C error`（偶发一次） | 首次握手时某个舵机没响应 | 重跑即可；频繁出现则查排线/电源 |
| 摄像头 `errno=19` | USB 掉线（供电不足） | 换口、用带电源 HUB、避开大功率插排 |
| 只有部分舵机读值为 `None` | 该舵机排线接触不良 | 断电重启扩展板、重插排线 |
| 学习模式下读角度全是 `None` | 学习模式接管了串口 | 先 `Arm_Button_Mode(0)` 再读 |
