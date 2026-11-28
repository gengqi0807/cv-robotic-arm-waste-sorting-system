# 人脸照片采集与放置

## 文件夹

所有模型/照片统一放在 `RobotArm-a/web_api/data/faces/`，下列命令在 `RobotArm-a/web_api` 执行。该目录下，四位组员各有两个文件夹。目录名与账号管理中的员工编号一致。

| 成员 | 训练集 | 测试集 |
|---|---|---|
| 费翔鸿 | `fei-xianghong/train/` | `fei-xianghong/test/` |
| 耿绮 | `geng-qi/train/` | `geng-qi/test/` |
| 刘天悦 | `liu-tianyue/train/` | `liu-tianyue/test/` |
| 左逸伟 | `zuo-yiwei/train/` | `zuo-yiwei/test/` |

## 每个文件夹放什么

- **`train/` 每人 5 张。** 全部是本人清晰、无遮挡的正脸照。用白色或纯色背景，保持光线均匀，避免逆光和过强侧光。可命名为 `01.jpg` 至 `05.jpg`。
- **`test/` 每人 10 张。** 另选时间、换房间或换背景单独拍摄。包含 2～3 张轻微侧脸，左右至少各一张，角度约 15～30°。再拍 1～2 张不同光照的照片，例如顺光一张和柔和侧光一张。其余用正脸。可命名为 `01.jpg` 至 `10.jpg`。
- 两组均使用 JPG，分辨率至少 640×480，人脸占画面至少 1/3。每张只拍本人，避免帽子、口罩、墨镜和美颜滤镜。两组用不同的原始照片。
- 未登记人员先确认同意，校准照片放在 `data/faces_unknown_calibration/`，正式拒识测试照片放在 `data/faces_unknown_test/`。

## 采集清单与验收

在本地 `data/faces/manifest.json` 填写参与训练成员的授权确认日期和两组实际张数。采集者与采集完成时间按实际情况记录。新检出项目时，可从 `manifest.example.json` 复制模板。照片齐全时，本轮共有 20 张训练照片、40 张已登记测试照片。

正式验收需要至少 50 张独立的已登记测试照片和 50 张未登记测试照片，两组各达到 49/50 的正确识别或拒识比例。正式验收样本单独预留，在阈值确定后再使用。

在项目根目录安装 OpenCV 依赖，并将 YuNet 检测器和 SFace 特征模型放入 `data/faces/`。

```powershell
python -m pip install -r backend/requirements-face.txt
$detector = 'data/faces/face_detection_yunet_2023mar.onnx'
$extractor = 'data/faces/face_recognition_sface_2021dec_int8.onnx'
Invoke-WebRequest 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx' -OutFile $detector
Invoke-WebRequest 'https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec_int8.onnx?download=true' -OutFile $extractor
Get-FileHash -Algorithm SHA256 $detector,$extractor
```

| 模型 | SHA-256 |
|---|---|
| YuNet | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| SFace INT8 | `2b0e941e6f16cc048c20aee0c8e31f569118f65d702914540f7bfdc14048d78a` |

### 已登记人员阶段测试

```powershell
python -m backend.train_face_model
python -m backend.verify_face_recognition --known-only --threshold 0.45
```

登记结果保存在本地 `data/faces/face_embeddings.npz`。阶段测试输出各成员的最佳候选、正确接受数和图片读取至识别的耗时。阶段阈值 `0.45` 是余弦距离，用于当前已登记组试验。如需训练或验证部分成员，两条命令都可重复传入 `--operator-id <员工编号>`。

### 电脑摄像头测试

在本机测试环境启用 `FACE_IMAGE_LOGIN_ENABLED=1` 并设置阶段阈值 `FACE_THRESHOLD=0.45`。登录页对应的数据库账号须已启用，且员工编号与登记目录一致。用 `http://127.0.0.1:5000/login` 打开自己的服务，或使用已启动的本机测试页。点击「打开电脑摄像头」，允许浏览器使用摄像头，再点击「拍照识别」。浏览器只在点击拍照时发送一张图片。识别成功后进入该账号角色的页面。

### 完整验证

先准备独立的校准样本和正式验收样本。下面示例将登记校准样本放在 `data/faces/<员工编号>/test/`，未登记校准样本放在 `data/faces_unknown_calibration/`。将正式登记样本另放在 `data/faces_acceptance/<员工编号>/test/`，正式未登记样本放在 `data/faces_unknown_test/`。用校准脚本输出的余弦距离替换示例中的 `0.45`。

```powershell
python -m backend.train_face_model --data-dir data/faces
python -m backend.calibrate_face_threshold --data-dir data/faces --unknown-dir data/faces_unknown_calibration
python -m backend.verify_face_recognition --data-dir data/faces --known-dir data/faces_acceptance --unknown-dir data/faces_unknown_test --threshold 0.45
```

照片、正式采集清单、检测模型和登记特征放在本机，Git 仅跟踪空白采集清单示例。运行时认证审计仅记录认证结果和必要会话信息。
