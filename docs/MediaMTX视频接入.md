# MediaMTX + FFmpeg + WebRTC 视频通道

## 实现方式

```text
机械臂摄像头（只打开一次）
  ├─ 独立采集线程 → 最新帧 → 原有视觉识别/抓取流程
  └─ 最新帧队列（最多1帧）→ JPEG编码线程 → 设备HTTP MJPEG :5001/stream
      → 电脑FFmpeg拉取并编码H.264 → 本机RTSP :8554/robot-camera
      → 电脑MediaMTX → WebRTC/WHEP :8889 → 网页 <video>
```

电脑已有Windows版FFmpeg，所以编码放在电脑；机械臂无需安装FFmpeg、无需第二次打开摄像头。这次没有使用设备端H.264推流，设备至电脑的一段仍是MJPEG；优化的是将采集/视频与推理、Flask同步图片上传分开，以及浏览器端改用连续视频。不能据此宣称整条链路都采用H.264或已测得更低延迟。

WebRTC模式显示原始相机画面，识别框只保留在设备本地显示和JPEG兼容通道；网页识别/任务状态继续从原接口获取。这样视频不需要等待检测结果，画面与识别结果不保证逐帧同步。

状态、命令和MySQL记录仍由原Flask处理，视频数据不再经过Flask。视频断开会重连，不触发机械臂动作。WebRTC不支持或连接失败时显示提示，不静默声称已连接；收到解码帧才显示实时视频。可手动切换JPEG兼容通道。

## 已定位的工具

- MediaMTX：`D:\tool\mediamtx\mediamtx_v1.19.2_windows_amd64\mediamtx.exe`
- FFmpeg：`D:\tool\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe`

`tools/start_video.py`自动查找这两个目录中的工具；存在多个版本时，通过项目`.env`的`MEDIAMTX_EXE`和`FFMPEG_EXE`指定完整路径。脚本不覆盖D:\tool内的配置，仅在项目`runtime/video`写入专用配置和日志。Ctrl+C终止本脚本启动的两个子进程；不结束其他服务进程。端口占用时明确退出，不自动抢占。

## 设备文件同步

本项目包含两套历史代码，必须按实际启动路径选择：

**目前实机使用的RobotArm-a**：将本机下列两个文件同步到设备同名源代码目录：

```text
D:\code\python\robotic-arm\RobotArm-a\ros2_dofbot_ws\src\dofbot_garbage_yolov5\dofbot_garbage_yolov5\main.py
D:\code\python\robotic-arm\RobotArm-a\ros2_dofbot_ws\src\dofbot_garbage_yolov5\dofbot_garbage_yolov5\video_source.py
```

保留前序控制文件、模型、标定、令牌和记录缓存。仍使用`bash start_robot.sh`启动，无需为这些Python源文件重新编译ROS包。原始RobotArm目录未修改。

**如果改用gengqi分支内的arm代码**：同步`arm/dofbot_garbage_yolov5/main.py`和`video_source.py`，使用该套代码已有启动命令，并在启动前`export ROBOT_BRIDGE_LOCAL=0`连接电脑Flask。这一套和RobotArm-a的历史控制逻辑不同，不要把main.py跨套覆盖。

两个视频源模块内容相同，机器人HTTP视频源复用已有`ROBOT_BRIDGE_TOKEN`；FFmpeg使用同一令牌拉流，令牌不会通过Flask视频配置接口发给浏览器。

## 现场启动步骤（本次未执行）

沿用电脑`192.168.137.101`、机械臂`192.168.137.100`。IP变化时修改命令参数；MediaMTX配置中的地址必须是浏览器实际能到达的电脑地址。

1. 电脑在项目目录启动现有Flask：

   ```powershell
   cd D:\code\python\cv-robotic-arm-waste-sorting-system
   python app.py
   ```

2. 设备同步文件后启动RobotArm-a：

   ```bash
   cd /home/HwHiAiUser/RobotArm-a
   export ROBOT_VIDEO_MODE=webrtc
   bash start_robot.sh
   ```

   正常应显示`[视频源] HTTP MJPEG :5001/stream，等待PC拉流`。摄像头仍由原程序初始化，视频模块不控制舵机。

3. 电脑另开PowerShell终端：

   ```powershell
   cd D:\code\python\cv-robotic-arm-waste-sorting-system
   python tools/start_video.py --pc-ip 192.168.137.101 --robot-ip 192.168.137.100
   ```

   此命令会真正启动MediaMTX和FFmpeg。若设备尚未启动或摄像头中断，FFmpeg退出后每3秒重试。启动脚本默认H.264 baseline、零延迟编码、15fps、640×480、无B帧。调整帧率可用`--fps 10`，编码负载与清晰度仍需现场评估。

4. 浏览器Ctrl+F5刷新`http://127.0.0.1:5000/monitor`或电脑局域网地址的监控页，选择“接口联调”，视频通道选择“低延迟视频”。默认仍为模拟数据，避免将示意画面误认为实机视频。

   WebRTC信令端点由`GET /api/video/config`提供，默认跟随网页主机地址：`http://网页主机:8889/robot-camera/whep`。接收解码帧后显示“实时视频已连接”。多网卡/代理部署可在电脑`.env`显式设置`VIDEO_WEBRTC_BASE_URL`。

5. 浏览器看不到视频时分别检查：

   - `runtime/video/mediamtx.log`是否监听8889、8189并出现推流路径；
   - `runtime/video/ffmpeg.log`是否设备源连接失败、401（令牌不一致）、编码器不可用或RTSP连接失败；
   - 视频服务监听端口有无被另一个MediaMTX进程占用；
   - 视频页是否混用了HTTPS网页和HTTP信令。当前提供的是HTTP局域网配置；HTTPS部署必须同时配置MediaMTX证书及HTTPS信令，不能只把URL前缀改成https。

## 网络配置

- 设备：允许电脑访问TCP 5001（带令牌的MJPEG视频源）。
- 电脑：允许浏览器访问TCP 8889（WHEP信令）、UDP 8189（优先视频传输）和TCP 8189（回退）。
- RTSP发布端口8554仅绑定电脑127.0.0.1，供本机FFmpeg使用。
- 默认MediaMTX仅允许本机发布`robot-camera`，局域网浏览器可读取该路径；仅供可信局域网演示。视频阅读权限与Flask设备令牌不是同一套鉴权。
- 默认允许`http://192.168.137.101:5000`、`http://127.0.0.1:5000`和`http://localhost:5000`跨域播放。改用域名、Flask其他端口时需同步修改`tools/start_video.py`生成的`webrtcAllowOrigins`。

本次没有新增防火墙规则，没有修改系统网络配置。

## 回退JPEG

停止设备程序，启动前设置：

```bash
export ROBOT_VIDEO_MODE=jpeg
bash start_robot.sh
```

网页选择“JPEG兼容模式”。也可在电脑`.env`设置`VIDEO_TRANSPORT=jpeg`并重启Flask，作为页面默认通道。设备和页面必须匹配；设备处于webrtc模式时不再POST JPEG给Flask，仅在网页切换不会自动改变设备上传方式。

## 后续验证及本次完成范围

本次按要求没有启动MediaMTX、FFmpeg或Flask进行本机联调，也没有访问机械臂。仅进行了不联网的离线单元测试、代码语法检查，以及`python tools/start_video.py --check-only`工具路径/配置检查。未验证真实MediaMTX启动、浏览器ICE互通、相机、编码性能或端到端延迟。

现场应以同样分辨率、网络、光照和机械臂动作对比JPEG与WebRTC：摄像头拍摄毫秒计时器，记录显示时间差及连续运行卡顿，结合设备/电脑CPU占用和实际抓取过程判断。FFmpeg合成15fps输出不代表摄像头持续产生15帧真实新画面；不要只看播放器输出帧率判断实时性。
