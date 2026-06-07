# 游戏画面识别项目

本项目当前包含两个阶段：

- 第一阶段：录屏抽帧、筛选、标注、YOLO 数据集构建、训练和验证。
- 第二阶段：全屏截图、YOLO 实时识别、状态机防抖、Windows `SendInput` 底层键鼠动作执行。

## 依赖

```bash
pip install -r requirements.txt
```

抽帧还需要本机安装 `ffmpeg` 并加入 PATH。

## 第一阶段：数据集与训练

### 目录约定

- `data/raw_videos/`：原始游戏录屏。
- `data/raw_frames/`：从视频抽出的原始帧。
- `data/selected_frames/`：人工筛选后准备标注的图片。
- `data/annotated/images/`：已标注图片。
- `data/annotated/labels/`：YOLO 格式 `.txt` 标签，文件名需与图片同名。
- `datasets/game_yolo/`：自动生成的 YOLO 训练集。
- `models/`：训练输出。
- `reports/`：数据集摘要、验证结果、筛选 HTML。
- `runtime_logs/`：第二阶段实时运行日志。
- `classes.txt`：类别名，一行一个，顺序必须与标注工具中的类别顺序一致。

### 推荐流程

1. 初始化目录：

```bash
python main.py init --classes enemy hp_bar item
```

2. 抽帧：

```bash
python main.py extract data/raw_videos/gameplay.mp4 --fps 2
```

3. 生成图片筛选页：

```bash
python main.py review
```

打开 `reports/frame_review.html` 查看抽帧质量。把要保留的图片放入 `data/selected_frames/`。

4. 使用 LabelImg / Label Studio 标注。

标注完成后，把图片放到：

```text
data/annotated/images/
```

把 YOLO `.txt` 标签放到：

```text
data/annotated/labels/
```

5. 构建 YOLO 数据集：

```bash
python main.py build-dataset --val 0.2 --test 0.1
```

6. 训练 YOLO：

```bash
python main.py train --model yolov8n.pt --epochs 80 --imgsz 640 --batch 16
```

7. 验证权重：

```bash
python main.py val models/game_yolo/weights/best.pt
```

8. 查看当前数据量：

```bash
python main.py inspect
```

## 第二阶段：实时识别与动作执行

### 安全原则

第二阶段默认建议先使用 `--dry-run`。这个模式会实时识别、更新状态机、打印将要执行的动作，但不会真正发送键鼠输入。

紧急停止键默认是 `F8`：

```text
按 F8 退出实时运行
```

如果开了调试窗口，也可以按窗口内的 `q` 退出。

### 全屏游戏注意事项

- 截图使用 `mss`，对普通全屏和无边框全屏通常可用。
- 如果独占全屏截图是黑屏，先把游戏切到“无边框全屏”或“窗口化全屏”。
- 如果有多显示器，使用 `--monitor` 选择显示器。`1` 通常是主显示器，`0` 表示所有显示器组成的虚拟桌面。
- 可以用 `--roi left,top,width,height` 只截取游戏区域，降低延迟。

### 调试运行，不执行动作

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --debug --dry-run
```

含义：

- 识别 `enemy` 类别。
- 显示 OpenCV 调试窗口。
- 不发送键鼠动作。
- 连续稳定识别到目标后，只打印 dry-run 动作。

### 执行按键动作

例如稳定识别到 `enemy` 后按 `space`：

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --action key --key space
```

### 执行鼠标点击动作

例如稳定识别到目标后把鼠标移动到目标中心并左键点击：

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --action click --aim --mouse-button left
```

### 同时移动、点击和按键

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --action both --key e --aim --mouse-button left
```

### 状态机参数

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --stable-frames 3 --lost-frames 5 --cooldown 0.6
```

参数说明：

- `--stable-frames 3`：连续 3 帧检测到目标才触发动作，避免单帧误检。
- `--lost-frames 5`：连续 5 帧丢失目标才回到搜索状态。
- `--cooldown 0.6`：两次动作之间至少间隔 0.6 秒。
- `--conf 0.45`：YOLO 置信度阈值。
- `--imgsz 640`：推理输入尺寸，越大越准但越慢。

### 底层输入实现

动作执行没有使用 PyAutoGUI，而是直接通过 Windows `SendInput`：

- 键盘使用扫描码方式发送，兼容性通常强于普通虚拟键。
- 鼠标移动使用虚拟桌面绝对坐标，适配多显示器。
- 鼠标点击使用 `SendInput` 的 mouse down/up 事件。

仍需注意：某些游戏或反作弊环境可能拦截模拟输入。单机游戏建议优先使用无边框全屏，并以管理员权限运行脚本和游戏保持权限级别一致。

## 注意事项

- 负样本也要保留一部分，也就是没有目标的游戏画面。
- `classes.txt` 的顺序不要在标注后随意改，否则标签类别会错位。
- 抽帧频率不宜过高，初期建议 `--fps 2` 到 `--fps 5`。
- 训练前先保证 `data/annotated/images` 和 `data/annotated/labels` 中的文件能一一对应。
- 第二阶段不要一开始就关闭 `--dry-run`，先确认识别框、类别名、目标中心和触发节奏都正确。
