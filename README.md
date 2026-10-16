# 分拣事件保存 API

第一版使用 Flask、PyMySQL 和 MySQL 数据库 `garbage_sorting`。API 只负责分拣事件保存、查询、CSV 导出和统计，不包含前端、人脸、表情或多机协同功能。

## 配置和运行

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item db\config.example.ini db\config.ini
```

编辑 `db/config.ini`，填写 MySQL 连接信息，并确认数据库和 `event` 表已按 `db/init.sql` 创建。也可用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`MYSQL_CHARSET` 环境变量覆盖配置。`MYSQL_DATABASE` 必须是 `garbage_sorting`。

`ssl_disabled` 默认 `auto`：仅当 MySQL 主机为本机 loopback（`127.0.0.1`、`localhost` 或 `::1`）时关闭 TLS 握手以减少本机延迟；远程数据库默认不关闭 TLS。可在配置文件或用 `MYSQL_SSL_DISABLED=true/false` 显式覆盖。

```powershell
.venv\Scripts\python.exe app.py
```

服务默认监听 `http://127.0.0.1:5000`。运行隔离测试：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Web 页面

启动 Flask 服务后访问 `http://127.0.0.1:5000/records` 打开记录查询页，访问 `http://127.0.0.1:5000/stats` 打开运营统计页。根路径 `/` 会跳转到记录查询页。

任务操作页为 `http://127.0.0.1:5000/tasks`，实时监控页为 `http://127.0.0.1:5000/monitor`。四页均通过左侧导航互相切换。

记录查询页支持本地日期、类别、结果筛选，最近500条分页查看、打开事件详情和按当前条件下载 CSV。运营统计页支持按本地日期或自选本地开始/结束时间统计；统计范围会转换为 Unix epoch 毫秒传给 API。两页时间展示均使用浏览器本地时区。

手动验收时，可先在数据库插入测试事件，然后逐项检查：记录筛选和分页、详情字段、CSV 文件列头、统计总数和四类占比，以及窄屏下筛选控件和表格横向滚动。此阶段仅包含记录查询和运营统计，不包含任务操作与实时监控。

任务操作页默认模拟模式，可体验单次/连续模式选择和启动、暂停、继续、结束状态流转；只有切到“接口联调”才会 POST JSON 到 `/api/tasks/control`。当前 Flask 服务尚未实现任务控制 API 或状态 WebSocket，联调失败不会显示模拟成功。监控页默认模拟数据，会明确标记 CSS 画面与模拟 `detect`/坐标/执行/状态；接口联调模式需填写状态 WebSocket 地址，接收 `{ "type": "detect|transform|execute_pick|status", "data": { ... } }` 消息。该 WebSocket 消息包络是前端适配格式，不是已上线的设备端契约。也可填写最近图片 URL 显示静态图片；当前没有真实相机视频接口。

## 接口

### 写入事件

`POST /api/events`，JSON 请求体必须包含接口契约的全部字段：

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

首次写入返回 HTTP 201 和 `{"event_id":"event-001","created":true}`；重复 `event_id` 返回 HTTP 200 和 `created:false`，保留原记录不覆盖。类别值限定为 `recyclable`、`kitchen`、`hazardous`、`other`。目标机械臂坐标与 `error_code` 可为 null。

### 查询事件

`GET /api/events` 支持 `date=YYYY-MM-DD`（按 UTC 日期）、`start_time_ms` 与 `end_time_ms`（左闭右开毫秒范围）、`class_id`、`result`、`page`、`page_size`。日期不能与毫秒范围同时传入。按 `timestamp_ms` 倒序查询最近最多500条，再在这批记录内分页；`page_size` 最大100。响应含 `items`、分页信息和实际时间范围。

`GET /api/events/{event_id}` 返回单条契约事件 JSON；找不到时返回 JSON 错误和 HTTP 404。

### 导出 CSV

`GET /api/events/export` 接受与列表相同的日期/时间、`class_id`、`result` 筛选参数，返回 UTF-8 BOM 的 CSV，便于 Excel 打开。成功响应为 CSV 文件；错误响应为 JSON。

### 统计

`GET /api/stats` 接受 `date=YYYY-MM-DD` 或 `start_time_ms` 与 `end_time_ms`。班次使用调用方提供的毫秒起止范围，项目没有定义固定班次时段。

返回总数、四类数量和占比、成功/失败数量及故障数量。当前统计口径：`result` 等于 `success` 计成功，其他结果计失败；非空 `error_code` 计故障。类别占比为类别数量除以筛选范围总数，空结果时为0。

## 验收映射与后续工作

本模块支撑 FR-03：连续产生30条记录后检查筛选、详情、导出和查询；重启后确认记录仍在；最近500条可查询；必填信息完整率至少99%；统计数量与记录明细一致；95%的分拣记录在产生后1秒内显示。性能与持久性需在真实 MySQL 和完整系统联调中测量，本地隔离测试不替代验收。

后续仍需：连接真实 `garbage_sorting` 实例做建表兼容和端到端验证；评测30条写入/显示时延、500条查询及重启持久性；与上游 `event` 生产者联调时间戳和错误码；确认项目真实 `result` 状态枚举及“故障数量”的业务定义；补齐账号权限、Web 四页面和任务控制等其他 FR-03/FR-04 功能。