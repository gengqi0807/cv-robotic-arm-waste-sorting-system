import cv2
import numpy as np
import torch
from det_utils import letterbox, scale_coords, nms, non_max_suppression

def preprocess_image(img_bgr, cfg):
    """
    对图像进行预处理
    :param img_bgr: 输入的 BGR 格式图像
    :param cfg: 包含 input_shape 的配置字典
    """
    # 调用 det_utils 中的 letterbox 进行等比例缩放和填充
    img, scale_ratio, pad_size = letterbox(img_bgr, new_shape=cfg['input_shape'])
    # BGR转RGB，并且 HWC(高宽通道) 转为 CHW(通道高宽)
    img = img[:, :, ::-1].transpose(2, 0, 1) 
    # 转换为内存连续存储的数组，并归一化到 0-1 之间，数据类型为 float16
    img = np.ascontiguousarray(img, dtype=np.float16) / 255.0 
    
    return img, scale_ratio, pad_size
    
def draw_bbox(bbox, img0, color, wt, names):
    """
    在图像上绘制预测框和文本
    :param bbox: 预测框数据 [x1, y1, x2, y2, conf, class_id]
    :param img0: 原始图像
    :param color: 框的颜色
    :param wt: 线条粗细
    :param names: 类别名称列表
    """
    det_result_str = ''
    for idx, class_id in enumerate(bbox[:, 5]):
        if float(bbox[idx][4] < float(0.05)):
            continue
        
        # 画框
        img0 = cv2.rectangle(img0, (int(bbox[idx][0]), int(bbox[idx][1])), 
                             (int(bbox[idx][2]), int(bbox[idx][3])), color, wt)
        
        # 写类别名称
        img0 = cv2.putText(img0, str(idx) + ' ' + names[int(class_id)], 
                           (int(bbox[idx][0]), int(bbox[idx][1] + 16)), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        
        # 写置信度
        img0 = cv2.putText(img0, '{:.4f}'.format(bbox[idx][4]), 
                           (int(bbox[idx][0]), int(bbox[idx][1] + 32)), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        
        # 拼接检测结果字符串 (注意这里加入了 int() 防止索引浮点数报错)
        det_result_str += '{} {} {} {} {} {}\n'.format(
            names[int(bbox[idx][5])], str(bbox[idx][4]), 
            bbox[idx][0], bbox[idx][1], bbox[idx][2], bbox[idx][3])
            
    # 注：原教程截图中只 return 了 img0，如果您还需要结果字符串，可以改为 return img0, det_result_str
    return img0
    
def get_labels_from_txt(path):
    """
    从 txt 文件中读取类别名称
    :param path: coco_names.txt 文件路径
    """
    labels = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f.readlines():
            # 去除每行末尾的换行符并添加到列表中
            labels.append(line.strip())
    return labels
    
def draw_prediction(img0, det, names):
    """
    可视化推理结果，调用 draw_bbox 在图像上绘制预测框
    :param img0: 原始图像
    :param det: 非极大值抑制后的预测框结果 (Tensor 或 Numpy)
    :param names: 类别名称列表
    """
    # 如果检测结果为空，直接返回原图
    if det is None or len(det) == 0:
        return img0
    
    # 将 Tensor 转换为 Numpy 数组，方便 cv2 处理
    if isinstance(det, torch.Tensor):
        det = det.cpu().numpy()
        
    # 设置默认的边框粗细和颜色 (这里统一设为绿色，也可以根据类别随机分配)
    wt = 2
    color = (0, 255, 0) 
    
    # 调用之前定义的 draw_bbox() 绘制
    img0 = draw_bbox(det, img0, color, wt, names)
    
    return img0
    
def infer_image(img_bgr, model, cfg, names):
    """
    对图像进行推理及可视化
    返回：det (预测框张量), img_bgr (画好框的图像)
    """
    # 1. 预处理
    img, scale_ratio, pad_size = preprocess_image(img_bgr, cfg)
    
    # ais_bench 需要 numpy 格式的输入，并增加 batch 维度
    input_data = np.expand_dims(img, axis=0) 
    
    # 2. 昇腾 NPU 推理 (调用 .infer 方法)
    # 注意：不同版本的 ais_bench 返回格式可能略有差异，通常是 list
    infer_outputs = model.infer([input_data])
    # 将输出转回 tensor 以适配我们之前写的 NMS
    pred = torch.from_numpy(infer_outputs[0])
        
    # 3. NMS
    conf_thres = cfg.get('conf_thres', 0.25)
    iou_thres = cfg.get('iou_thres', 0.45)
    det = non_max_suppression(pred, conf_thres=conf_thres, iou_thres=iou_thres)[0]
    
    # 4. 坐标映射与画图
    if det is not None and len(det):
        det[:, :4] = scale_coords(img.shape[1:], det[:, :4], img_bgr.shape).round()
        # 调用可视化方法
        draw_prediction(img_bgr, det, names)
        
    return det, img_bgr
