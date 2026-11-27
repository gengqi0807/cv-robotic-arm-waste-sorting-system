# 智能机械臂垃圾分拣与管理系统

基于计算机视觉的机械臂垃圾分拣系统由三个部分组成。

- **`frontend/`** 管理页面、HTML/CSS/JavaScript 和同源 API 转发。前端通过 HTTP 获取账号和设备状态。
- **`backend/`** Flask API、任务队列、设备上报、账号权限、人脸识别和 MySQL。
- **`arm/`** Atlas 200I DK A2 板端识别与抓取程序，通过 HTTP 与后端联调。 [板端部署说明](arm/README.md)

## 功能概览

| 页面 | 路径 | 功能 |
|---|---|---|
| 记录查询 | `/records` | 按日期/类别/结果筛选，分页查看，事件详情，导出 CSV |
| 运营统计 | `/stats` | 分拣总数、成功/失败/故障、四类占比与分布条形图 |
| 分拣工作台 | `/tasks` | 同屏显示相机和识别结果，支持启动、暂停、继续、结束分拣及课程任务演示 |
| 实时监控 | `/monitor` | 班组长和维护员查看工作台的监控内容。操作员从此入口进入分拣工作台 |
| 登录 | `/login` | 密码或工牌登录，可选人脸图片匹配 |
| 账号管理 | `/users` | 班组长查看、新增和删除账号 |
| 设备维护 | `/maintain` | 维护员可查看设备概况，位置校准待接入 |
| 表情互动 | `/expression` | 操作员配置授权、开关和预设动作，模拟并查看触发结果 |

### 课程任务演示（`/tasks` 页）

在分拣工作台展开「课程任务演示」。现场联调且设备在线时，动作按钮向当前设备下发命令。模拟演示中的任务操作只更新页面状态。

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

单独部署前端时安装 `frontend/requirements.txt`，单独部署后端时安装 `backend/requirements.txt`。运行人脸识别时，后端安装 `backend/requirements-face.txt`。

### 2. 配置

```powershell
Copy-Item backend\db\config.example.ini backend\db\config.ini
Copy-Item .env.example .env
```

- **`backend/db/config.ini`** 填 MySQL 的 `host`、`port`、`user`、`password` 和 `database`。已有根目录 `db/config.ini` 时，后端也会读取该文件作为回退配置。
  也可用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`MYSQL_CHARSET` 环境变量覆盖。
  `MYSQL_DATABASE` 必须是 `garbage_sorting`。
  `ssl_disabled` 默认 `auto`：仅当 MySQL 主机是本机 loopback 时关闭 TLS；远程默认开启。可用 `MYSQL_SSL_DISABLED=true/false` 显式覆盖。
- **`.env`** 设置前端的 `FLASK_HOST`、`FLASK_PORT` 和板端连接用的令牌。分开运行时，后端默认监听 `127.0.0.1:5001`。前端可用 `BACKEND_URL` 指定后端地址。

> 本地数据库配置和 `.env` 已由 Git 忽略。模板中的配置值需按环境填写。

生成随机令牌：

```powershell
python -c "import secrets;print(secrets.token_hex(32))"
```

### 3. 建库建表

```powershell
python -m backend.db.init_db
```

脚本会创建分拣事件、账号、会话、认证审计和表情互动所需的表。

### 4. 分开启动

在项目根目录打开两个终端，分别运行后端和前端。

```powershell
python -m backend
```

```powershell
python -m frontend
```

浏览器打开 `http://127.0.0.1:5000/`。前端把 `/api/` 请求转发到后端，浏览器仍通过同一个端口登录、查询和控制。板端保留原来的局域网 `5000` 地址。

`python app.py` 仍可在一个进程内启动同一套前后端，用于原有单端口部署。

### 5. 运行测试

```powershell
python -m unittest discover -s tests -v
```

`verify_acceptance.py` 和 `verify_pages.py` 分别验证事件接口及记录可见时延、持久性。两者会在配置的 MySQL 中写入并清理测试记录。

## 登录与角色权限

完成建库后，用命令创建首个班组长账号。命令会提示输入员工编号、姓名和密码。

```powershell
python -m flask --app backend.app create-manager
```

将 `.env` 中的 `WEB_AUTH_REQUIRED` 设为 `1` 并重启服务。默认值为 `0`，用于继续使用原有课程联调模式。使用 HTTPS 时可将 `WEB_SECURE_COOKIE` 设为 `1`。

- 操作员可操作分拣与课程任务、查看监控与记录，并设置表情互动。
- 班组长可查看记录、导出 CSV、查看运营统计与监控，并管理账号。
- 维护员可进入维护页、查看监控与记录。

浏览器用会话 Cookie 跳转和调用同源 API。退出会撤销服务端会话。启用门禁后，板端仍用原有 `ROBOT_BRIDGE_TOKEN` 通信，写入 `POST /api/events` 时也需携带此令牌。

### 人脸识别

图片匹配用 YuNet 检测和对齐人脸，再用 SFace 比对本地登记特征。部署时安装 `backend/requirements-face.txt`，准备模型和授权样本，训练并校准后设置 `FACE_IMAGE_LOGIN_ENABLED=1`，并填写 `FACE_THRESHOLD` 余弦距离。浏览器上传登录只允许匹配已启用的账号。当前方式没有活体检测。外部识别终端使用独立的 `FACE_TERMINAL_TOKEN`，通过 `POST /api/auth/face` 提交识别结果。两个入口默认关闭。 [人脸准备与验收说明](PHOTO_GUIDE.md)

### 表情互动

操作员可在 `/expression` 保存独立开关、授权和预设动作。模拟笑脸请求走同一个触发 API。服务端结合实时任务、机械臂状态和置信度作判断，记录模拟结果。真实笑脸识别与板端动作待联调。

## 与机械臂联调

机械臂端通过 HTTP 主动访问本服务，**板子不开端口，只做 HTTP 客户端**：

```
浏览器 ──HTTP──> 前端 :5000 ── /api ──> 后端 :5001 ──> MySQL
板子 ────HTTP──> 前端 :5000 ── /api ──> 后端命令与状态接口
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
| `GET  /api/tasks/state` | 分拣工作台 | 一次轮询同时更新任务按钮、设备状态和识别结果 |

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

首次写入返回 HTTP 201 和 `{"event_id":"event-001","created":true}`。重复 `event_id` 返回 200 和 `created:false`，保留原记录不覆盖。
`class_id` 只能是 `recyclable`、`kitchen`、`hazardous`、`other`；目标坐标与 `error_code` 可为 `null`。

### 查询事件

`GET /api/events` 支持 `date=YYYY-MM-DD`（按 UTC 日期）、`start_time_ms` 与 `end_time_ms`（左闭右开毫秒范围）、`class_id`、`result`、`page`、`page_size`。
日期不能与毫秒范围同时传入。按 `timestamp_ms` 倒序取最近最多 500 条，再在这批内分页；`page_size` 最大 100。

`GET /api/events/{event_id}` 返回单条事件；找不到时 404。

### 导出 CSV

`GET /api/events/export` 接受与列表相同的筛选参数，返回带 UTF-8 BOM 的 CSV（Excel 直接打开不乱码）。

### 统计

`GET /api/stats` 接受 `date` 或 `start_time_ms` + `end_time_ms`。

统计口径以集成分支为准。`success` 计成功，`failed` 计失败，`unverified` 单独计未验证，非空 `error_code` 计故障。类别占比 = 该类数量 / 筛选范围总数，空结果为 0。

## 目录结构

```
.
├── app.py                    单端口兼容入口
├── frontend/
│   ├── templates/            业务、登录和管理页面
│   └── static/               样式与前端脚本
├── backend/
│   ├── app.py                API 和板端命令队列
│   ├── web_access.py         登录、角色权限、账号管理与表情互动
│   ├── database.py           MySQL 连接与配置加载
│   ├── db/                   数据库配置与初始化
│   └── sql/                  数据修复脚本
├── arm/                      板子端代码（见 arm/README.md）
├── data/faces/               本地人脸训练集和测试集
├── tests/                    隔离测试
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
