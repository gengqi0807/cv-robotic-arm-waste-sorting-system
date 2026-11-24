import cv2
import numpy as np
import torch
import torchvision
import time

def letterbox(img, new_shape=(640, 640), color=(114, 114, 114), auto=False, scaleFill=False, scaleup=True):
    shape = img.shape[:2]  # 获取图像的初始尺寸（高度、宽度）
    # 如果给定的新尺寸是一个整数，则将其转换为一个元组，元组的两个值均为该整数。
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1]) # 计算缩放比例（新尺寸/原始尺寸），选择较小的比例作为缩放因子
    if not scaleup:  # 如果scaleup=False，即不允许放大图像，则缩放因子小于1
        r = min(r, 1.0)

    # 计算填充量
    ratio = r, r  # 宽高的缩放比例
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))  # 根据获得的缩放因子计算缩放后的图像尺寸
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # 高和宽的填充量，新尺寸-缩放后的图像尺寸
    if auto:  # 如果选择自动填充
        dw, dh = np.mod(dw, 64), np.mod(dh, 64)  # 将填充量调整为64的倍数
    elif scaleFill:  # 如果选择自动拉伸
        dw, dh = 0.0, 0.0  # 则将填充量设置为0
        new_unpad = (new_shape[1], new_shape[0])  # 将缩放后的图像尺寸设置为指定尺寸
        ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # 根据新尺寸与原始尺寸重新计算宽高的缩放因子

    # 将填充量均分到上下左右四个方向
    dw /= 2
    dh /= 2

    if shape[::-1] != new_unpad:  # 如果原图像尺寸不等于无填充的图像尺寸
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)  # 将图像尺寸缩放为无填充的图像尺寸
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))  # 分别计算图像顶部和底部的填充量
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))  # 分别计算图像左侧和右侧的填充量
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # 在图像的上下左右添加边框来进行填充，边框的颜色由给定的color参数确定。
    return img, ratio, (dw, dh)  # 返回填充后的图像、缩放比例和填充量
    
def xyxy2xywh(x):
    y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)  # 复制x，保存在变量y中
    y[:, 0] = (x[:, 0] + x[:, 2]) / 2  # 计算box的x中心坐标
    y[:, 1] = (x[:, 1] + x[:, 3]) / 2  # 计算box的y中心坐标
    y[:, 2] = x[:, 2] - x[:, 0]  # 计算box的宽
    y[:, 3] = x[:, 3] - x[:, 1]  # 计算box的高
    return y

def xywh2xyxy(x):
    y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # 左上角的横坐标x1
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # 左上角纵坐标y1
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # 右下角的横坐标x2
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # 右下角的纵坐标y2
    return y

def non_max_suppression(
    prediction,
    conf_thres=0.25,
    iou_thres=0.45,
    classes=None,
    agnostic=False,
    multi_label=False,
    labels=(),
    max_det=300,
    nm=0,  # number of masks
):
    """执行非极大值抑制 (NMS)"""
    # 如果 prediction 是列表或元组，取第一个元素
    if isinstance(prediction, (list, tuple)):
        prediction = prediction[0]

    device = prediction.device
    mps = 'mps' in device.type
    if mps:
        prediction = prediction.cpu()

    bs = prediction.shape[0]  # batch size
    nc = prediction.shape[2] - nm - 5  # 类别数量
    xc = prediction[..., 4] > conf_thres  # 候选框掩码（置信度大于阈值）

    # 断言检查阈值合法性
    assert 0 <= conf_thres <= 1, f'Invalid Confidence threshold {conf_thres}, valid values are between 0.0 and 1.0'
    assert 0 <= iou_thres <= 1, f'Invalid IoU {iou_thres}, valid values are between 0.0 and 1.0'

    # 以下为根据描述补全的核心逻辑
    # 设置最大处理时间限制（这里假设为 10 秒，可根据实际需求调整）
    t = time.time()
    max_time_img = 0.2  # 可调整的处理单张图片的最大时间限制

    output = [torch.zeros((0, 6 + nm), device=prediction.device)] * bs

    for xi, x in enumerate(prediction):  # 逐张图像处理
        # 1. 根据置信度阈值过滤掉低置信度的预测框
        x = x[xc[xi]]

        # 如果没有符合条件的框，直接返回空结果
        if not x.shape[0]:
            continue

        # 2. 将预测框转换为 (x1, y1, x2, y2) 格式
        # x[:, :4] 默认是 (x, y, w, h) 格式，调用之前定义的 xywh2xyxy()
        box = xywh2xyxy(x[:, :4])
        # 提取类别分数和掩码（如果支持）
        cls = x[:, 5:5 + nc]
        mask = x[:, 5 + nc:]

        # 3. 如果允许每个预测框有多个类别 (multi_label)
        if multi_label:
            i, j = (cls > conf_thres).nonzero(as_tuple=False).T
            x = torch.cat((box[i], x[i, 4 + j, None], j[:, None].float(), mask[i]), 1)
        else:  # 否则只取分数最高的类别
            conf, j = cls.max(1, keepdim=True)
            x = torch.cat((box, conf, j.float(), mask), 1)[conf.view(-1) > conf_thres]

        # 4. 根据类别过滤预测框（如果指定了 classes）
        if classes is not None:
            x = x[(x[:, 5:6] == torch.tensor(classes, device=x.device)).any(1)]

        n = x.shape[0]  # 当前图像剩余预测框数量
        if not n:
            continue

        # 5. 根据 NMS 算法对预测框进行筛选
        # 如果 agnostic 为 True，则忽略类别，所有框一起算；否则按类别分别计算
        c = x[:, 5:6] * (0 if agnostic else 4096) 
        boxes, scores = x[:, :4] + c, x[:, 4]
        
        # 调用我们自己定义的 nms() 方法
        i = nms(boxes, scores, iou_thres) 

        # 6. 截取前 max_det 个预测框
        if i.shape[0] > max_det:
            i = i[:max_det]

        # 将最终结果保存到 output 列表中
        output[xi] = x[i]

        # 7. 检查时间限制，如果超时则停止处理剩余的图像
        if (time.time() - t) > max_time_img * bs:
            print("超出时间限制，停止处理剩余图像")
            break

    return output
    
def nms(boxes, scores, iou_thres):
    """
    非极大值抑制 (NMS) 封装函数
    """
    try:
        # 尝试使用常规的 NMS (对应单类别或多类别偏移后的处理)
        # 注意：在上面的代码中，如果是 multi_label 或者 agnostic=False，
        # 我们通过给 boxes 加上不同类别的偏移量 c，其实已经将多类别问题转化为了单类别问题。
        return torchvision.ops.nms(boxes, scores, iou_thres)
    except Exception as e:
        # 如果出现错误，则回退到最简单的单类别 NMS
        print(f"NMS 错误: {e}, 尝试回退")
        return torchvision.ops.nms(boxes, scores, iou_thres)
        
def clip_coords(coords, img_shape):
    """
    用于修剪坐标，确保其在图像范围内，如果超出范围则修改为最接近的边界值。
    :param coords: 待修剪的坐标 (n, 4)，格式为 (x1, y1, x2, y2)
    :param img_shape: 目标图像尺寸 (高度, 宽度)
    """
    if isinstance(coords, torch.Tensor):
        # 如果是 PyTorch 张量，使用 clamp_ 原地修改
        coords[:, 0].clamp_(0, img_shape[1])  # x1
        coords[:, 1].clamp_(0, img_shape[0])  # y1
        coords[:, 2].clamp_(0, img_shape[1])  # x2
        coords[:, 3].clamp_(0, img_shape[0])  # y2
    else:
        # 如果是 NumPy 数组，使用 clip 进行边界修剪
        coords[:, [0, 2]] = coords[:, [0, 2]].clip(0, img_shape[1])  # x1, x2
        coords[:, [1, 3]] = coords[:, [1, 3]].clip(0, img_shape[0])  # y1, y2
    return coords
    
def scale_coords(img1_shape, coords, img0_shape, ratio_pad=None):
    """
    将标注框从当前处理的图像上缩放至目标图像上。
    :param img1_shape: 当前图像尺寸 (高度, 宽度)，例如网络输入尺寸 640x640
    :param coords: 待缩放处理的标注框坐标 (n, 4)
    :param img0_shape: 目标图像尺寸 (高度, 宽度)，即原始图像尺寸
    :param ratio_pad: 缩放比例和填充参数。如果为 None，则自动计算；如果不为 None，则使用传入的值。
    """
    # 1. 如果 ratio_pad 参数为空，则计算缩放比例和填充量
    if ratio_pad is None:
        # 计算缩放比例 (选择较小的比例)，保证图像能完整放入
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])
        # 计算填充量： (当前尺寸 - 目标尺寸 * 缩放比例) / 2
        pad = ((img1_shape[1] - img0_shape[1] * gain) / 2, 
               (img1_shape[0] - img0_shape[0] * gain) / 2)
    # 如果 ratio_pad 不为空，则使用给定的缩放比例和填充量
    else:
        gain = ratio_pad[0][0] # 宽高缩放比相同，取第一个即可
        pad = ratio_pad[1]     # (pad_w, pad_h)

    # 2. 坐标平移：减去填充量 (x方向减去pad[0], y方向减去pad[1])
    coords[:, [0, 2]] -= pad[0]  # x padding
    coords[:, [1, 3]] -= pad[1]  # y padding
    
    # 3. 坐标缩放：除以缩放比例
    coords[:, :4] /= gain

    # 4. 调用 clip_coords 函数，修剪坐标，确保其在图像范围内
    clip_coords(coords, img0_shape)

    # 5. 返回缩放后的坐标
    return coords
    

