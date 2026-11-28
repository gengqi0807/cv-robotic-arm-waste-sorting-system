# 智能机械臂垃圾分拣与管理系统

`gengqi` 合并 FXH 页面、人脸登录与角色权限，保留 MediaMTX / FFmpeg / WebRTC 视频工具。

## 部署结构

| 位置 | 职责 | 默认端口 |
|---|---|---|
| 本仓库 frontend/ | 页面、浏览器摄像头采集、同源 API 转发 | 5000 |
| RobotArm-a/web_api/ | 业务 API、人脸模型与匹配、会话、MySQL | 5002 |
| RobotArm-a 原有 ROS 程序 | 检测、抓取、命令轮询、MJPEG 视频源 | 视频 5001 |
| tools/start_video.py | 电脑 FFmpeg 编码和 MediaMTX 分发 | RTSP 8554、WHEP 8889、ICE 8189 |

完整后端在 `D:/code/python/robotic-arm/RobotArm-a/web_api`，Linux 对应 `/home/HwHiAiUser/RobotArm-a/web_api`。模型及特征在其中的 `data/faces/`，不放入前端仓库。API 独立启动，不导入 ROS、不启动舵机。原始 RobotArm 不修改。

浏览器经电脑 `/api/*` 同源转发到 `BACKEND_URL`。Cookie 会话和操作员/班组长/维护员权限由后端维护。视频配置在电脑生成，WHEP 指向电脑 MediaMTX。

## 电脑摄像头联调

后端终端（代码仍位于 RobotArm-a）：

```powershell
cd D:\code\python\robotic-arm\RobotArm-a\web_api
python -m pip install -r backend/requirements-face.txt
# 首次建表和准备四名成员账号；保留已有分拣数据
python -m backend.db.init_db
python setup_face_demo.py
python run_backend.py
```

前端终端：

```powershell
cd D:\code\python\cv-robotic-arm-waste-sorting-system
python -m pip install -r requirements.txt
python app.py
```

前端 `.env` 使用 `BACKEND_URL=http://127.0.0.1:5002`。后端 `.env` 启用 `WEB_AUTH_REQUIRED=1`、`FACE_IMAGE_LOGIN_ENABLED=1`、`FACE_THRESHOLD=0.45`；数据库填 `web_api/backend/db/config.ini`。两份配置独立，不公开令牌、密码或特征。

打开 **http://127.0.0.1:5000/login**，页面自动开启摄像头，授权后约 1 秒开始自动识别，无需手动拍照。识别请求逐次执行，每次处理完成后约 1 秒重试，30 秒未识别则停止并提供重新开启。每次上传单张 JPEG，后端内存处理、不保存照片。识别成功后显示姓名和中文身份，约 1.4 秒后跳转；登录后的页面持续显示当前姓名与身份。普通局域网 HTTP 页面通常不能访问浏览器摄像头，需 localhost/127.0.0.1 或 HTTPS。

| 账号 | 角色 | 登录后页面 |
|---|---|---|
| geng-qi（耿绮） | manager | 运营统计 |
| zuo-yiwei（左逸伟） | maintainer | 设备维护 |
| liu-tianyue（刘天悦） | operator | 分拣工作台 |
| fei-xianghong（费翔鸿） | operator | 分拣工作台 |

YuNet 检测 + SFace INT8 特征匹配；0.45 是余弦距离阈值，越小越相似。没有活体检测。无脸、多脸、陌生人不应获得会话。模型加载及接口测试不能代替真人准确率测试。重新采集见 [PHOTO_GUIDE.md](PHOTO_GUIDE.md)，其中命令从 RobotArm-a/web_api 执行。

## Linux 机械臂部署

完整同步 `web_api`（含本地模型及许可证），按该目录 README 创建 Python 3.10 独立虚拟环境，避免改变 Ascend/ROS 依赖。后端设 `BACKEND_HOST=0.0.0.0`，电脑前端设 `BACKEND_URL=http://192.168.137.100:5002`。

桥接程序 `ROBOT_BRIDGE_URL` 指向 `http://127.0.0.1:5002` 或同一 API 的可达地址，令牌与后端一致。四台电脑可配置同一机械臂 API，无需将业务后端绑定某台 101/102/103/104 电脑。任务队列在内存，API 只启动一份。MySQL 若仍在电脑，机械臂数据库配置需填写电脑可达地址与授权，不能沿用 Linux 本机 127.0.0.1。

## 视频与页面

保留 [MediaMTX 说明](docs/MediaMTX视频接入.md) 中的链路。API 迁移不会自动启动视频或机械臂动作。

```powershell
python tools/start_video.py --pc-ip 192.168.137.101 --robot-ip 192.168.137.100
```

网页切换「接口联调」，支持 WebRTC/JPEG 及重连。任务视频来自机械臂摄像头；登录用的是电脑摄像头。保留记录查询、统计、任务与课程演示 UI；表情互动仍含模拟接口，不能视为真实表情动作已完成。

`arm/` 是历史快照，实机使用独立 RobotArm-a，勿跨版本覆盖控制、标定和模型。

## 测试

```powershell
python -m unittest discover -s tests -v
node --test tests/webrtc-player.test.cjs tests/login.test.cjs
```

测试默认从相邻 RobotArm-a/web_api 导入业务源码，可用 `ROBOT_WEB_API_ROOT` 指定。普通 `python app.py` 可独立部署，仅需 Flask 和远程 API。

## 启动认证与机械臂人脸观察位

实机流程已改为：启动 API → 机器人复用叠罗汉 ready → 到位验证 → 自动人脸认证 → 操作员显式启动 → 返回分拣观察位。启动不自动抓取。Linux API 使用 `bash web_api/run_robot_api.sh`，机器人在 ros-arm-env 使用 `bash start_authenticated_robot.sh`；电脑前端 BACKEND_URL 指向机械臂 5002。详见 RobotArm-a/启动认证与观察位说明.md。

部署模式从机械臂相机认证，登录页不打开电脑摄像头；本机临时测试仍用 `python run_backend.py` 和 browser 相机配置，不会驱动机械臂。
