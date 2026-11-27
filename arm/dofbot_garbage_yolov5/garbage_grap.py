import time
import Arm_Lib

class garbage_grap_move:
    def __init__(self):
        # 设置移动状态
        self.move_status = True
        # 创建机械臂实例
        self.arm = Arm_Lib.Arm_Device()
        # 夹爪角度（2026-11-13 现场实测确认）：
        #   30  = 完全张开
        #   60  = 半开（叠罗汉文件里当"松开"用）
        #   130 = 完全闭合（夹紧）
        # 原代码写成"130 张开 / 30 夹紧"，方向反了：
        # 每次"夹紧"发 30 实际是张到最大，把积木撞飞；"松开"发 130 反而夹死。
        self.grap_joint = 30           # 张开
        self.grab_close_joint = 125    # 夹紧（现场反馈 130 略紧，降到 125）

    def move(self, joints_high, joints_grab, target_joints):
        """
        移动过程函数：高位张爪 → 垂直下探 → 夹紧 → 抬回高位 → 移到桶上方 → 松开
        :param joints_high: 高位姿态(积木上方若干厘米处,夹爪张开)
        :param joints_grab: 抓取姿态(逆解算出的、贴到积木上的位姿)
        :param target_joints: 对应垃圾桶位置的各关节角度
        """
        # 1. 先到高位(此时夹爪张开,不会碰到积木)
        self.arm.Arm_serial_servo_write6_array(joints_high, 1000)
        time.sleep(1)

        # 2. 从高位垂直下探到抓取位
        self.arm.Arm_serial_servo_write6_array(joints_grab, 1000)
        time.sleep(1)

        # 3. 闭合夹爪抓取物体。闭合时保持抓取位的前五个关节不变。
        self.arm.Arm_serial_servo_write6(joints_grab[0], joints_grab[1], joints_grab[2], joints_grab[3], joints_grab[4], self.grab_close_joint, 500)
        time.sleep(0.5)

        # 4. 夹着积木抬回高位(先离开桌面,再横移,避免蹭掉)
        # 注意: joints_high 的第 6 个位置是"张开角度"(grap_joint=30),
        # 直接 write6_array(joints_high) 会在抬高的同时把爪子张开、积木当场掉落。
        # 所以这里必须把第 6 个换成"夹紧角度",抬高过程中一直夹住不放。
        carry_high = list(joints_high)
        carry_high[5] = self.grab_close_joint
        self.arm.Arm_serial_servo_write6_array(carry_high, 1000)
        time.sleep(1)

        # 5. 先抬到"桶正上方的高位"，再垂直下降
        # 原来是从抓取位直接斜着走到桶口，视觉上像"斜着扑过去"，动作也慢。
        # 这里拆成两步：先到桶上方较高的位置(平移到桶的正上方)，再垂直降下来，
        # 和"举着积木平移过去、再往下放"的动作一致，落点也更稳。
        target_high = list(target_joints)
        target_high[1] = max(0.0, min(180.0, target_high[1] + 25))   # 大臂抬高 25°(到桶上方)
        target_high[3] = max(0.0, min(180.0, target_high[3] - 10))   # 腕部补偿，保持爪子朝下
        carry_high2 = list(target_high)
        carry_high2[5] = self.grab_close_joint                       # 保持夹紧
        self.arm.Arm_serial_servo_write6_array(carry_high2, 1200)
        time.sleep(0.7)

        # 6. 从桶上方垂直下降到桶口(仍保持夹紧)
        carry = list(target_joints)
        carry[5] = self.grab_close_joint
        self.arm.Arm_serial_servo_write6_array(carry, 900)
        time.sleep(0.7)

        # 7. 松开夹爪，放下垃圾
        self.arm.Arm_serial_servo_write6(target_joints[0], target_joints[1], target_joints[2], target_joints[3], target_joints[4], self.grap_joint, 500)
        time.sleep(0.5)

    def arm_run(self, name, joints):
        """
        机械臂移动函数：根据垃圾名称设置目标垃圾桶关节角度并执行动作
        :param name: 识别的垃圾类别名称 (Blue, Green, Red, Yellow)
        :param joints: 反解求得的物体位置各关节角度
        """
        # 垃圾桶位置：用 2026-11-16 现场重新示教读出的真实角度（garbage_poses.json 里的 drop_*）。
        # 这一版是"往前伸、往下探、贴桶口"的姿态（j2 小、j3 大）：
        #   - 移动过程是直臂伸过去，不是弯腰收手，动作更利落
        #   - 末端贴近桶口，松手积木直接落进去，不会从高处摔下
        # 第 6 个位置统一用 grap_joint(30=张开)，落料时才张开。
        if name == 'Red':
            target_joints = [44, 29, 63, 29, 89, self.grap_joint]      # drop_red    11-16 v2
        elif name == 'Blue':
            target_joints = [30, 35, 66, 3, 89, self.grap_joint]       # drop_blue   11-16 v2
        elif name == 'Green':
            target_joints = [143, 40, 58, 0, 89, self.grap_joint]      # drop_green  11-16 v2
        elif name == 'Yellow':
            target_joints = [129, 37, 65, 7, 89, self.grap_joint]      # drop_yellow 11-16 v2
        else:
            print("未知的垃圾类别，放入默认垃圾桶")
            target_joints = [90, 50, 50, 50, 90, self.grap_joint]

        # 由逆解结果生成"高位姿态":只抬高、不下探。
        # 原代码 joints_down = list(joints) 让"下探"和"抬起"两个动作都退化成原地不动,
        # 结果是夹爪贴着桌面横扫过去(把积木撞飞),夹住后又贴着桌面平移(积木蹭掉)。
        # 这里在抓取姿态基础上把肩关节抬高,形成"先高后低"的安全轨迹。
        #
        # 抬多少度需要现场试:先小后大,确认既不撞积木也不撞上方物体。
        #   joints_high[1] 肩关节:增大 = 末端抬高
        #   joints_high[3] 腕关节:配合补偿,让夹爪保持朝下
        joints_high = list(joints)
        joints_high[1] = max(0.0, min(270.0, joints_high[1] + 15))   # 肩关节抬起 15°
        joints_high[3] = max(0.0, min(270.0, joints_high[3] + 8))    # 腕关节补偿 8°

        # 调用 move() 执行完整动作:先到高位,再下探到抓取位
        self.move(joints_high, joints, target_joints)
