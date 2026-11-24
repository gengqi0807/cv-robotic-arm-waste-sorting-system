import time
import sys
# 注：原代码这里有一行 `import torch`，但整个文件从未使用 torch（只 import 未调用）。
# 系统 Python 没装 torch 时会直接报 ModuleNotFoundError 导致程序起不来，故移除。
import rclpy
import Arm_Lib
import cv2 as cv
import numpy as np
from time import sleep
from numpy import random
from dofbot_info.srv import Kinemarics
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image
from pathlib import Path
import os
from garbage_grap import garbage_grap_move
from ais_bench.infer.interface import InferSession
from det_utils import letterbox, scale_coords, nms, xyxy2xywh
from npu_utils import get_labels_from_txt, infer_image, draw_prediction, preprocess_image, draw_bbox
import threading 

# (2) 定义全局变量
cfg = {
    'conf_thres': 0.7,  # 置信度阈值
    'iou_thres': 0.7,   # 非极大值抑制 IoU 阈值
    'input_shape': [640, 640], # 模型输入尺寸
}

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]
model_folder = os.path.join(ROOT, 'model')
model_path = os.path.join(model_folder, 'yolov5s_bs1.om') # 模型路径
label_path = os.path.join(model_folder, 'coco_names.txt') # 标签路径
cfg_folder = os.path.join(ROOT, 'config')
offset_cfg_path = os.path.join(cfg_folder, 'offset.txt') # 抓取误差配置路径
XYT_path = os.path.join(cfg_folder, 'XYT_config.txt') # 相机标定参数路径

model = InferSession(0, model_path)  # 初始化推理模型
labels_dict = get_labels_from_txt(label_path) # 获取标签名称
WARMUP_INDEX = 5 # 模型预热阈值

# (3) 垃圾检测推理与机械臂各关节旋转角度求解
class garbage_identify:
    def __init__(self):
        # 初始化变量
        self.frame = None
        self.arm = Arm_Lib.Arm_Device()
        self.xy = [90, 130] # 默认初始角度
        self.garbage_index = 0
        self.grab_move = garbage_grap_move()
        self.is_busy = False 

        # ROS2 节点初始化
        self.node = rclpy.create_node('dofbot_garbage')
        self.node_pub = rclpy.create_node('dofbot_img_node')

        # 创建客户端，连接逆解服务
        self.client = self.node.create_client(Kinemarics, 'trial_service')
        
        # 创建图像发布者
        self.image_pub = self.node_pub.create_publisher(Image, "cam_data", 10)
        self.bridge = CvBridge()
        
        # ================= 新增：启动 ROS 后台 Spin 线程 =================
        self.spin_thread = threading.Thread(target=self.spin_node, daemon=True)
        self.spin_thread.start()

        # 读取抓取误差参数
        self.offset = -1
        self.x_offset = -1
        try:
            with open(offset_cfg_path, 'r') as f:
                lines = f.readlines()
                self.x_offset = float(lines[0].strip())
                self.offset = float(lines[1].strip())
        except Exception as e:
            print("读取误差参数失败:", e)

    def get_pos(self):
        """
        执行推理，获取类别名称和位置坐标
        """
        det, img_draw = infer_image(self.frame, model, cfg, labels_dict)
        
        msg = {}
        if det is not None and len(det) > 0:
            for box in det:
                # 将 Tensor 转换为 Python 的基本类型（float, int）
                cls_id = int(box[5])
                if cls_id < len(labels_dict):
                    name = labels_dict[cls_id]
                else:
                    name = "Unknown"
                    
                # 提取坐标并转换为 float
                x1 = float(box[0])
                y1 = float(box[1])
                x2 = float(box[2])
                y2 = float(box[3])
                
                # Use the bottom-center point, which lies on the table plane.
                point_x = (x1 + x2) / 2.0
                point_y = y2
                
                # 这时 round() 就不会报错了
                a = round((point_x - 320) / 4000, 4)
                b = round((480 - point_y) / 3000, 4)
                msg[name] = (a, b)
                print(f"Pick target {name}: pixel=({point_x:.1f}, {point_y:.1f}), arm_xy=({a:.4f}, {b:.4f})")
                
        return msg, img_draw

    def garbage_run(self, image):
        """
        执行垃圾识别函数
        """
        self.frame = cv.resize(image, (640, 480))
        txt0 = 'Model Loading'
        msg = {}

        # 模型预热
        if self.garbage_index < WARMUP_INDEX:
            cv.putText(self.frame, txt0, (150, 50), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            self.garbage_index += 1
            return self.frame, msg
            
        if self.garbage_index >= WARMUP_INDEX:
            try:
                # 接收 msg 和画好框的 self.frame
                msg, self.frame = self.get_pos() 
                print("msg is: ", msg)
            except Exception as e:
                print("get_pos Exception:", e)
            return self.frame, msg

    def server_joint(self, posxy):
        print("posxy is:", posxy)
        # 等待服务可用
        if not self.client.wait_for_service(timeout_sec=1.0):
            print("Service not available!")
            return None
        
        request = Kinemarics.Request()
        request.tar_x = posxy[0] + self.x_offset
        request.tar_y = posxy[1] + self.offset
        request.kin_name = "ik"

        try:
            self.future = self.client.call_async(request)
            # ================= 修改：不再调用 rclpy.spin_until_future_complete
            # 因为后台线程已经在 spin 了，我们只需要在这里死循环等待结果即可
            while rclpy.ok() and not self.future.done():
                time.sleep(0.01)
            # =========================================================
            response = self.future.result()
            
            if response:
                joints = [0, 0, 0, 0, 0]
                joints[0] = response.joint1
                joints[1] = response.joint2
                joints[2] = response.joint3
                joints[3] = response.joint4
                joints[4] = response.joint5

                if joints[2] < 0:
                    joints[1] += joints[2] / 2
                    joints[3] += joints[2] * 3 / 4
                    joints[2] = 0
                joints.append(self.grab_move.grap_joint)
                return joints
        except Exception as e:
            print("arg error:", e)

    def garbage_grap(self, msg, xy=None):
        """
        将机械臂移动到指定位置进行抓取，并将机械臂移回初始位置
        """
        # 如果正在抓取，直接跳过
        if self.is_busy:
            return
        self.is_busy = True # 锁定
        
        if xy is not None:
            self.xy = xy
            
        try:
            if len(msg) != 0:
                self.arm.Arm_Buzzer_On(1)
                sleep(0.5)

                # 抓取顺序：**先近后远**
                #
                # arm_xy 的第二个分量 b = (480 - point_y) / 3000：
                #   point_y 大（画面下方） → b 小 → tar_y 小 → 离机械臂近
                #   point_y 小（画面上方） → b 大 → tar_y 大 → 离机械臂远
                #
                # 所以按 b 升序排 = 先抓画面下方（近的），再抓上方（远的）。
                # 反过来先抓远的，机械臂伸过去时会压到/撞倒前面（近处）的积木，
                # 导致后面下不去。
                new_msg = sorted(list(msg.items()), key=lambda x: x[1][1], reverse=False)

                for item in new_msg:
                    name = item[0]
                    posxy = item[1]

                    # 调用逆解
                    joints = self.server_joint(posxy)

                    if joints:
                        self.grab_move.arm_run(name, joints)
                        self.arm.Arm_serial_servo_write6_array([self.xy[0], self.xy[1], 0, 0, 90, 30], 1000)
                        sleep(1.5)
        finally:
            self.is_busy = False
        
    def spin_node(self):
        rclpy.spin(self.node)
