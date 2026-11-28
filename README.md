# 智能机械臂垃圾分拣与管理系统

基于计算机视觉的机械臂垃圾分拣系统，包含两部分：

- **本仓库（Flask 前端 + 后端）**：网页控制台、任务下发、实时监控、分拣记录、运营统计，数据存 MySQL。
- **`arm/` 目录（板子端）**：跑在 Atlas 200I DK A2 上的识别与抓取程序，通过 HTTP 与本仓库联调。部署说明见 [arm/README.md](arm/README.md)。

## 功能概览

| 页面 | 路径 | 功能 |
|---|---|---|
| 记录查询 | `/records` | 按日期/类别/结果筛选，分页查看，事件详情，导出 CSV |
| 运营统计 | `/stats` | 分拣总数、成功/失败/故障、四类占比与分布条形图 |
| 任务操作 | `/tasks` | 启动/暂停/继续/结束分拣 + **课程任务演示**（9 个动作按钮） |
| 实时监控 | `/monitor` | 实时画面（板子上传的 JPEG）、机械臂状态、视觉识别结果 |

### 课程任务演示（`/tasks` 页）

点一下直接把命令下发给机械臂，不用敲命令行：

| 按钮 | 行为 |
|---|---|
| 读取舵机位置 | 读 6 个舵机当前角度并回显 |
| 蜂鸣器 | 响一声，验证扩展板通信 |
| RGB 灯 | 依次亮红/绿/蓝 |
| 上下左右摆动 | 四个方向各摆一次 |
| 跳舞 | 预设动作序列循环两遍 |
| 夹一块积木 | 先回识别位，再由分拣循环抓一次 |
| 叠罗汉 | 四周 4 块 → 中间摞 4 层（按 `stack_poses.json`） |
| 积木搬运工 | 中间那一摞 → 搬回四周原位（叠罗汉的逆流程） |
| 语音识别 | 走 WeNet 录音识别流程（需 4 段耳麦，见 `arm/README.md`） |

### 颜色与垃圾类别对应

板子识别出的是**积木颜色**，上报时映射成四类标准类别（`app.py` 的 `CLASS_IDS` 只接受这四个值）：

| 积木颜色 | `class_id` | 显示 | 统计页颜色 |
|---|---|---|---|
| Red 红 | `hazardous` | 有害垃圾 | 红 `#dc2626` |
| Blue 蓝 | `recyclable` | 可回收物 | 蓝 `#2563eb` |
| Green 绿 | `kitchen` | 厨余垃圾 | 绿 `#16a34a` |
| Yellow 黄 | `other` | 其他垃圾 | 黄 `#eab308` |

## 快速开始

### 1. 安装依赖

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 只有 `PyMySQL` 和 `Flask`，无其他依赖。

### 2. 配置

```powershell
Copy-Item db\config.example.ini db\config.ini
Copy-Item .env.example .env
```

- **`db/config.ini`**：填 MySQL 连接信息（`host` / `port` / `user` / `password` / `database`）。
  也可用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`MYSQL_CHARSET` 环境变量覆盖。
  `MYSQL_DATABASE` 必须是 `garbage_sorting`。
  `ssl_disabled` 默认 `auto`：仅当 MySQL 主机是本机 loopback 时关闭 TLS；远程默认开启。可用 `MYSQL_SSL_DISABLED=true/false` 显式覆盖。
- **`.env`**：`FLASK_HOST`（`0.0.0.0` 才能让局域网内其他设备访问）、`FLASK_PORT`、`ROBOT_BRIDGE_TOKEN`、`ROBOT_BRIDGE_DEVICE_IP`。

> `db/config.ini` 和 `.env` 都已被 `.gitignore` 忽略，**不要提交**。模板里是占位符，填自己的值。

生成随机令牌：

```powershell
python -c "import secrets;print(secrets.token_hex(32))"
```

### 3. 建库建表

```powershell
python db\init_db.py
```

预期输出：`MySQL 初始化完成：数据库 garbage_sorting，表 event。`

### 4. 启动

```powershell
python app.py
```

预期输出：

```
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
 * Running on http://<你的局域网IP>:5000
```

浏览器打开 `http://127.0.0.1:5000/`（会跳转到记录查询页）。
同一局域网的其他设备用 `http://<你的IP>:5000/` 访问。

### 5. 运行测试

```powershell
python -m unittest discover -s tests -v
```

## 与机械臂联调

机械臂端通过 HTTP 主动访问本服务，**板子不开端口，只做 HTTP 客户端**：

```
浏览器 ──HTTP──> Flask ──(命令队列)──> 板子轮询 /api/robot/poll
                 Flask <──(状态/画面/记录)── 板子 POST
```

| 接口 | 谁调 | 作用 |
|---|---|---|
| `POST /api/robot/connect` | 板子 | 注册设备，返回 `after_seq`（命令队列起点，避免重放旧命令） |
| `GET  /api/robot/poll` | 板子轮询 | 拉取新命令（`start`/`pause`/`resume`/`finish`/`action`） |
| `POST /api/robot/status` | 板子 | 上报状态、识别结果、是否忙碌 |
| `POST /api/robot/frame` | 板子 | 上传最新 JPEG 帧 |
| `GET  /api/robot/frame` | 监控页 | 读取最新帧 |
| `POST /api/tasks/control` | 任务页 | 分拣启动/暂停/继续/结束（受状态机和设备在线检查约束） |
| `POST /api/tasks/action` | 任务页 | 触发一次性动作（RGB/蜂鸣器/叠罗汉/搬运工/语音…），**不受状态机约束** |
| `GET  /api/tasks/state` | 任务页/监控页 | 轮询任务与设备状态 |

**两边必须配同一个 `ROBOT_BRIDGE_TOKEN`**，否则板子所有请求返回 401。

板子端配置与启动步骤见 [arm/README.md](arm/README.md)。

## 接口

### 写入事件

`POST /api/events`，JSON 请求体必须包含全部字段：

```json
{
  "event_id": "event-001",
  "task_id": "task-001",
  "timestamp_ms": 1791849600000,
  "class_id": "recyclable",
  "confidence": 0.95,
  "u_px": 120.0,
  "v_px": 240.0,
  "target_x_m": 0.12,
  "target_y_m": 0.24,
  "target_z_m": 0.03,
  "result": "success",
  "error_code": null,
  "duration_ms": 500,
  "calibration_id": "cal-001"
}
```

首次写入返回 HTTP 201 和 `{"event_id":"event-001","created":true}`；重复 `event_id` 返回 200 和 `created:false`，保留原记录不覆盖。
`class_id` 只能是 `recyclable`、`kitchen`、`hazardous`、`other`；目标坐标与 `error_code` 可为 `null`。

### 查询事件

`GET /api/events` 支持 `date=YYYY-MM-DD`（按 UTC 日期）、`start_time_ms` 与 `end_time_ms`（左闭右开毫秒范围）、`class_id`、`result`、`page`、`page_size`。
日期不能与毫秒范围同时传入。按 `timestamp_ms` 倒序取最近最多 500 条，再在这批内分页；`page_size` 最大 100。

`GET /api/events/{event_id}` 返回单条事件；找不到时 404。

### 导出 CSV

`GET /api/events/export` 接受与列表相同的筛选参数，返回带 UTF-8 BOM 的 CSV（Excel 直接打开不乱码）。

### 统计

`GET /api/stats` 接受 `date` 或 `start_time_ms` + `end_time_ms`。

统计口径：`result == "success"` 计成功；其他计失败；非空 `error_code` 计故障。类别占比 = 该类数量 / 筛选范围总数，空结果为 0。

## 目录结构

```
.
├── app.py                    Flask 主程序（页面路由 + API + 命令队列）
├── database.py               MySQL 连接与配置加载
├── db/
│   ├── config.example.ini    配置模板（复制成 config.ini）
│   ├── init.sql              建库建表
│   └── init_db.py            一键初始化
├── templates/                四个页面
├── static/                   样式与前端脚本
├── arm/                      板子端代码（见 arm/README.md）
├── tests/                    隔离测试
├── sql/                      数据修复脚本
├── verify_pages.py           页面验收脚本
└── verify_acceptance.py      接口验收脚本
```

## 验收映射

本模块支撑 FR-03：连续产生 30 条记录后检查筛选、详情、导出和查询；重启后确认记录仍在；最近 500 条可查询；必填信息完整率至少 99%；统计数量与记录明细一致；95% 的分拣记录在产生后 1 秒内显示。

性能与持久性需在真实 MySQL 和完整系统联调中测量，本地隔离测试不替代验收。

## 已知限制

- 语音识别准确率约 70~80%（模型限制），且需要能录音的 4 段（TRRS）耳麦。
- 摄像头与 USB 声卡共用 USB 供电，同时插入容易掉线，建议用带电源的 HUB。
- 叠罗汉 / 积木搬运工完全依赖 `arm/arm_basic/stack_poses.json` 的示教点，换机械臂或换积木摆位必须重新示教。
# MediaMTX低延迟视频

已增加电脑FFmpeg编码、MediaMTX转发和浏览器WebRTC播放。启动、设备同步及JPEG回退步骤见 [视频接入说明](docs/MediaMTX视频接入.md)。默认从`D:\tool\mediamtx`与`D:\tool\ffmpeg`定位工具；视频服务需要单独运行`python tools/start_video.py`。本次未进行真实流媒体联调或延迟测量。
