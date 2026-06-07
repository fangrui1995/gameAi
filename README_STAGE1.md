# 游戏画面识别项目

本项目当前包含两个阶段：

- 第一阶段：录屏抽帧、筛选、标注、YOLO 数据集构建、训练和验证。
- 第二阶段：全屏截图、YOLO 实时识别、固定 UI 区域识别、游戏状态层、异常恢复、Windows `SendInput` 底层键鼠动作执行。

## 依赖

```bash
pip install -r requirements.txt
```

抽帧还需要本机安装 `ffmpeg` 并加入 PATH。


## 可视化页面

第一阶段不必只能用命令行。项目提供了本地 GUI 页面：

```bash
python main.py gui
```

页面功能：

- 选择 `data/raw_videos/` 中的视频。
- 设置抽帧 FPS，执行抽帧。
- 生成并打开 `reports/frame_review.html`。
- 打开 `raw_frames`、`selected_frames`、`annotated/images`、`annotated/labels` 目录。
- 保存 `classes.txt` 类别名。
- 把 `selected_frames` 复制到 `annotated/images`。
- 安装/修复并启动 LabelImg。
- 补全无目标图片的空 `.txt` 标签。
- 检查图片与标签是否一一对应。
- 构建 YOLO 数据集。
- 启动 YOLO 训练。

推荐使用顺序：

```text
选择视频 -> 开始抽帧 -> 生成筛选页面 -> 打开筛选页面
-> 手工复制有价值图片到 selected_frames
-> 已筛选图片复制到 annotated/images
-> 安装/修复 LabelImg
-> 启动 LabelImg 并标注
-> 补全空标签 txt
-> 检查图片和标签是否对应
-> 构建数据集
-> 训练 YOLO
```

GUI 使用 Tkinter，属于 Python 标准库，不需要额外安装。

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
- `ui_templates/`：固定 UI 模板图，例如死亡、菜单、断线弹窗。
- `runtime_logs/`：第二阶段实时运行日志和失败帧。
- `classes.txt`：类别名，一行一个，顺序必须与标注工具中的类别顺序一致。
- `runtime_config.json`：第二阶段 UI 检测、状态层和异常恢复配置。

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

### 固定 UI 区域识别

借鉴 LostArk-Endless-Chaos 的经验，项目现在支持固定 UI 区域识别。配置在 `runtime_config.json` 的 `ui_regions` 中。

支持三种检测类型：

- `template`：模板匹配，适合死亡提示、菜单、确认按钮、断线弹窗。
- `brightness`：亮度判断，适合黑屏、加载页、极暗异常画面。
- `color`：颜色占比，适合血条、蓝条、小地图颜色点、技能冷却遮罩。

模板配置示例：

```json
{
  "name": "dead_template",
  "type": "template",
  "roi": [700, 350, 520, 220],
  "template": "ui_templates/dead.png",
  "threshold": 0.86,
  "state": "DEAD",
  "recovery_after": 2,
  "recovery": {"type": "key", "key": "enter"},
  "enabled": true
}
```

颜色检测示例：

```json
{
  "name": "low_hp_red",
  "type": "color",
  "roi": [600, 820, 360, 35],
  "color_bgr": [35, 35, 180],
  "tolerance": 45,
  "min_ratio": 0.08,
  "state": "COMBAT",
  "recovery_after": 1,
  "recovery": {"type": "key", "key": "f1"},
  "enabled": true
}
```

注意：`roi` 使用屏幕绝对坐标，格式是 `[left, top, width, height]`。

### 游戏状态层

实时循环现在会先根据固定 UI 检测结果推导 `game_state`，再决定是否执行 YOLO 战斗动作。

默认状态优先级：

```json
["CRASHED", "DISCONNECTED", "DEAD", "LOADING", "MENU", "COMBAT", "SEARCH"]
```

默认允许执行战斗动作的状态：

```json
["COMBAT", "SEARCH", "UNKNOWN"]
```

如果检测到 `DEAD`、`LOADING`、`MENU` 等非战斗状态，主循环会暂停普通攻击动作，优先执行配置的恢复动作。

### 异常恢复

支持两类恢复：

- UI 事件恢复：某个 UI 状态持续超过 `recovery_after` 后执行恢复动作。
- 无目标恢复：长时间没有 YOLO 目标时执行探索动作。

无目标恢复配置示例：

```json
"no_target_recovery": {
  "enabled": true,
  "after_seconds": 8,
  "cooldown": 4,
  "action": {"type": "key", "key": "tab"}
}
```

支持的恢复动作：

```json
{"type": "key", "key": "esc"}
{"type": "click", "x": 960, "y": 540, "button": "left"}
{"type": "both", "x": 960, "y": 540, "button": "left", "key": "space"}
```

恢复触发时会写入 `runtime_logs/*.csv`。如果 `save_failure_frames` 为 `true`，还会保存失败帧到 `runtime_logs/failure_frames/`，便于后续补标和调参。

### 调试运行，不执行动作

```bash
python main.py run models/game_yolo/weights/best.pt --target-class enemy --debug --dry-run
```

含义：

- 识别 `enemy` 类别。
- 显示 OpenCV 调试窗口。
- 显示 YOLO 框和 UI 区域命中框。
- 不发送键鼠动作。
- 连续稳定识别到目标后，只打印 dry-run 动作。

### 指定运行配置

默认会自动读取项目根目录下的 `runtime_config.json`。也可以手动指定：

```bash
python main.py run models/game_yolo/weights/best.pt --runtime-config runtime_config.json --target-class enemy --debug --dry-run
```

重新生成默认配置：

```bash
python main.py init-runtime-config --overwrite
```

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
- 第二阶段不要一开始就关闭 `--dry-run`，先确认识别框、类别名、目标中心、UI 区域和触发节奏都正确。
- 固定 UI 规则要从最稳定的目标开始加，例如死亡提示、加载黑屏、菜单按钮，不要一开始配置太多规则。




