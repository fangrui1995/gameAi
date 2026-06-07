from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
LABEL_EXT = ".txt"

KEY_NAME_TO_VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "esc": 0x1B,
    "space": 0x20,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def raw_videos(self) -> Path:
        return self.root / "data" / "raw_videos"

    @property
    def raw_frames(self) -> Path:
        return self.root / "data" / "raw_frames"

    @property
    def selected_frames(self) -> Path:
        return self.root / "data" / "selected_frames"

    @property
    def annotated(self) -> Path:
        return self.root / "data" / "annotated"

    @property
    def dataset(self) -> Path:
        return self.root / "datasets" / "game_yolo"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def runtime_logs(self) -> Path:
        return self.root / "runtime_logs"

    @property
    def ui_templates(self) -> Path:
        return self.root / "ui_templates"

    @property
    def failure_frames(self) -> Path:
        return self.root / "runtime_logs" / "failure_frames"


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


@dataclass
class RuntimeState:
    stable_hits: int = 0
    lost_hits: int = 0
    last_action_ts: float = 0.0
    last_target_ts: float = 0.0
    last_recovery_ts: float = 0.0
    action_count: int = 0
    recovery_count: int = 0
    mode: str = "SEARCH"
    game_state: str = "UNKNOWN"
    ui_events: str = ""


@dataclass(frozen=True)
class UIEvent:
    name: str
    state: str
    score: float
    roi: tuple[int, int, int, int]
    recovery_after: float
    recovery: dict


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]


class SendInputController:
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MAPVK_VK_TO_VSC = 0
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.user32 = ctypes.windll.user32

    def is_key_down(self, key_name: str) -> bool:
        vk = key_to_vk(key_name)
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def press_key(self, key_name: str, hold_seconds: float = 0.03) -> None:
        vk = key_to_vk(key_name)
        scan = self.user32.MapVirtualKeyW(vk, self.MAPVK_VK_TO_VSC)
        if self.dry_run:
            print(f"[dry-run] press_key {key_name}")
            return
        self._send_keyboard(scan, key_up=False)
        time.sleep(max(0.0, hold_seconds))
        self._send_keyboard(scan, key_up=True)

    def click(self, button: str = "left") -> None:
        if self.dry_run:
            print(f"[dry-run] click {button}")
            return
        if button == "left":
            down, up = self.MOUSEEVENTF_LEFTDOWN, self.MOUSEEVENTF_LEFTUP
        elif button == "right":
            down, up = self.MOUSEEVENTF_RIGHTDOWN, self.MOUSEEVENTF_RIGHTUP
        else:
            raise SystemExit(f"Unsupported mouse button: {button}")
        self._send_mouse(0, 0, down)
        self._send_mouse(0, 0, up)

    def move_to(self, x: int, y: int) -> None:
        if self.dry_run:
            print(f"[dry-run] move_to {x},{y}")
            return
        left = self.user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)
        top = self.user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)
        width = max(1, self.user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN) - 1)
        height = max(1, self.user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN) - 1)
        absolute_x = int((x - left) * 65535 / width)
        absolute_y = int((y - top) * 65535 / height)
        flags = self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK
        self._send_mouse(absolute_x, absolute_y, flags)

    def _send_keyboard(self, scan: int, key_up: bool) -> None:
        flags = self.KEYEVENTF_SCANCODE | (self.KEYEVENTF_KEYUP if key_up else 0)
        extra = ctypes.c_ulong(0)
        event = INPUT(
            type=self.INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(0, scan, flags, 0, ctypes.pointer(extra))),
        )
        self._send_input(event)

    def _send_mouse(self, dx: int, dy: int, flags: int) -> None:
        extra = ctypes.c_ulong(0)
        event = INPUT(
            type=self.INPUT_MOUSE,
            union=INPUT_UNION(mi=MOUSEINPUT(dx, dy, 0, flags, 0, ctypes.pointer(extra))),
        )
        self._send_input(event)

    def _send_input(self, event: ctypes.Structure) -> None:
        sent = self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
        if sent != 1:
            raise ctypes.WinError()


def iter_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def key_to_vk(key_name: str) -> int:
    normalized = key_name.strip().lower()
    if len(normalized) == 1:
        char = normalized.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char)
    if normalized in KEY_NAME_TO_VK:
        return KEY_NAME_TO_VK[normalized]
    raise SystemExit(f"Unsupported key name: {key_name}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def require_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{description} not found: {path}")


def find_executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and name.lower() == "ffmpeg":
        package_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if package_root.exists():
            matches = sorted(package_root.rglob("ffmpeg.exe"))
            if matches:
                return str(matches[-1])
    return name


def project_python(paths: ProjectPaths) -> Path:
    venv_python = paths.root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def find_labelimg_executable(paths: ProjectPaths) -> Path | None:
    candidates = [
        paths.root / ".venv" / "Scripts" / "labelImg.exe",
        paths.root / ".venv" / "Scripts" / "labelimg.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def patch_labelimg_pyqt_compat(paths: ProjectPaths) -> bool:
    canvas_path = paths.root / ".venv" / "Lib" / "site-packages" / "libs" / "canvas.py"
    if not canvas_path.exists():
        return False
    text = canvas_path.read_text(encoding="utf-8")
    original = text
    replacements = {
        "p.drawRect(left_top.x(), left_top.y(), rect_width, rect_height)": (
            "p.drawRect(int(left_top.x()), int(left_top.y()), int(rect_width), int(rect_height))"
        ),
        "p.drawLine(self.prev_point.x(), 0, self.prev_point.x(), self.pixmap.height())": (
            "p.drawLine(int(self.prev_point.x()), 0, int(self.prev_point.x()), int(self.pixmap.height()))"
        ),
        "p.drawLine(0, self.prev_point.y(), self.pixmap.width(), self.prev_point.y())": (
            "p.drawLine(0, int(self.prev_point.y()), int(self.pixmap.width()), int(self.prev_point.y()))"
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != original:
        canvas_path.write_text(text, encoding="utf-8")
        return True
    return False


def run_command(command: list[str]) -> None:
    print("Running:", " ".join(str(part) for part in command))
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(f"Command not found: {command[0]}. Install it and add it to PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}: {' '.join(command)}") from exc


def init_project(paths: ProjectPaths, classes: list[str]) -> None:
    for directory in [
        paths.raw_videos,
        paths.raw_frames,
        paths.selected_frames,
        paths.annotated / "images",
        paths.annotated / "labels",
        paths.dataset / "images" / "train",
        paths.dataset / "images" / "val",
        paths.dataset / "images" / "test",
        paths.dataset / "labels" / "train",
        paths.dataset / "labels" / "val",
        paths.dataset / "labels" / "test",
        paths.models,
        paths.reports,
        paths.runtime_logs,
        paths.ui_templates,
        paths.failure_frames,
    ]:
        ensure_dir(directory)

    classes_path = paths.root / "classes.txt"
    if classes and not classes_path.exists():
        classes_path.write_text("\n".join(classes) + "\n", encoding="utf-8")
    elif not classes_path.exists():
        classes_path.write_text("enemy\n", encoding="utf-8")
    write_default_runtime_config(paths, overwrite=False)

    print(f"Initialized project folders under {paths.root}")
    print(f"Edit class names in {classes_path} before annotation if needed.")


def write_default_runtime_config(paths: ProjectPaths, overwrite: bool = False) -> None:
    config_path = paths.root / "runtime_config.json"
    if config_path.exists() and not overwrite:
        print(f"Runtime config already exists: {config_path}")
        return

    config = {
        "state_priority": ["CRASHED", "DISCONNECTED", "DEAD", "LOADING", "MENU", "COMBAT", "SEARCH"],
        "combat_states": ["COMBAT", "SEARCH", "UNKNOWN"],
        "ui_regions": [
            {
                "name": "black_screen",
                "type": "brightness",
                "roi": [0, 0, 1920, 1080],
                "max_mean": 8,
                "state": "LOADING",
                "recovery_after": 15,
                "recovery": {"type": "key", "key": "esc"},
            },
            {
                "name": "dead_template",
                "type": "template",
                "roi": [700, 350, 520, 220],
                "template": "ui_templates/dead.png",
                "threshold": 0.86,
                "state": "DEAD",
                "recovery_after": 2,
                "recovery": {"type": "key", "key": "enter"},
                "enabled": False,
            },
            {
                "name": "menu_template",
                "type": "template",
                "roi": [0, 0, 1920, 1080],
                "template": "ui_templates/menu.png",
                "threshold": 0.86,
                "state": "MENU",
                "recovery_after": 3,
                "recovery": {"type": "key", "key": "esc"},
                "enabled": False,
            },
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
                "enabled": False,
            },
        ],
        "no_target_recovery": {
            "enabled": True,
            "after_seconds": 8,
            "cooldown": 4,
            "action": {"type": "key", "key": "tab"},
        },
        "save_failure_frames": True,
        "failure_frame_cooldown": 5,
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Runtime config written: {config_path}")


def extract_frames(video: Path, output_dir: Path, fps: float, prefix: str | None) -> None:
    require_file(video, "Video")
    ensure_dir(output_dir)
    frame_prefix = prefix or video.stem
    output_pattern = output_dir / f"{frame_prefix}_%06d.jpg"
    ffmpeg = find_executable("ffmpeg")
    run_command([
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps={fps}",
        "-q:v",
        "2",
        str(output_pattern),
    ])
    print(f"Frames saved to {output_dir}")


def write_review_manifest(image_dir: Path, output: Path, title: str) -> None:
    images = iter_images(image_dir)
    ensure_dir(output.parent)
    rows = []
    for image in images:
        relative = image.resolve().as_uri()
        rows.append(
            f'<figure><img src="{relative}" loading="lazy"><figcaption>{image.name}</figcaption></figure>'
        )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ margin: 24px; font-family: Segoe UI, sans-serif; background: #111; color: #eee; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }}
figure {{ margin: 0; padding: 8px; background: #1d1d1d; border: 1px solid #333; border-radius: 8px; }}
img {{ width: 100%; height: 150px; object-fit: contain; background: #000; }}
figcaption {{ margin-top: 6px; font-size: 12px; word-break: break-all; color: #bbb; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p>共 {len(images)} 张。人工筛选时，把保留图片复制到 <code>data/selected_frames</code>，或直接在标注工具中打开该目录。</p>
<div class="grid">
{''.join(rows)}
</div>
</body>
</html>
"""
    output.write_text(html, encoding="utf-8")
    print(f"Review manifest written: {output}")


def copy_images(src: Path, dst: Path) -> None:
    ensure_dir(dst)
    copied = 0
    for image in iter_images(src):
        target = dst / image.name
        if not target.exists():
            shutil.copy2(image, target)
            copied += 1
    print(f"Copied {copied} images to {dst}")


def read_classes(classes_file: Path) -> list[str]:
    require_file(classes_file, "Classes file")
    classes = [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not classes:
        raise SystemExit(f"No classes found in {classes_file}")
    return classes


def paired_samples(images_dir: Path, labels_dir: Path) -> list[tuple[Path, Path]]:
    samples: list[tuple[Path, Path]] = []
    missing_labels: list[Path] = []
    for image in iter_images(images_dir):
        label = labels_dir / f"{image.stem}{LABEL_EXT}"
        if label.exists():
            samples.append((image, label))
        else:
            missing_labels.append(image)

    if missing_labels:
        print(f"Warning: {len(missing_labels)} images have no label file and will be skipped.")
        for item in missing_labels[:10]:
            print(f"  missing label: {item.name}")
    return samples


def split_samples(samples: list[tuple[Path, Path]], val_ratio: float, test_ratio: float, seed: int) -> dict[str, list[tuple[Path, Path]]]:
    if not 0 <= val_ratio < 1 or not 0 <= test_ratio < 1 or val_ratio + test_ratio >= 1:
        raise SystemExit("Ratios must satisfy: 0 <= val_ratio, 0 <= test_ratio, val_ratio + test_ratio < 1")

    shuffled = samples[:]
    random.Random(seed).shuffle(shuffled)
    total = len(shuffled)
    test_count = int(total * test_ratio)
    val_count = int(total * val_ratio)
    train_count = total - val_count - test_count
    return {
        "train": shuffled[:train_count],
        "val": shuffled[train_count:train_count + val_count],
        "test": shuffled[train_count + val_count:],
    }


def clear_dataset_split(dataset: Path, split: str) -> None:
    for kind in ["images", "labels"]:
        directory = dataset / kind / split
        ensure_dir(directory)
        for file_path in directory.iterdir():
            if file_path.is_file():
                file_path.unlink()


def build_dataset(paths: ProjectPaths, val_ratio: float, test_ratio: float, seed: int) -> None:
    images_dir = paths.annotated / "images"
    labels_dir = paths.annotated / "labels"
    samples = paired_samples(images_dir, labels_dir)
    if not samples:
        raise SystemExit(
            "No annotated image/label pairs found. Put images in data/annotated/images and YOLO txt labels in data/annotated/labels."
        )

    splits = split_samples(samples, val_ratio=val_ratio, test_ratio=test_ratio, seed=seed)
    for split, split_samples_list in splits.items():
        clear_dataset_split(paths.dataset, split)
        for image, label in split_samples_list:
            shutil.copy2(image, paths.dataset / "images" / split / image.name)
            shutil.copy2(label, paths.dataset / "labels" / split / label.name)
        print(f"{split}: {len(split_samples_list)} samples")

    classes = read_classes(paths.root / "classes.txt")
    write_yolo_data_yaml(paths.dataset, classes)
    summary = {
        "train": len(splits["train"]),
        "val": len(splits["val"]),
        "test": len(splits["test"]),
        "classes": classes,
        "seed": seed,
        "val_ratio": val_ratio,
        "test_ratio": test_ratio,
    }
    ensure_dir(paths.reports)
    (paths.reports / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Dataset ready: {paths.dataset}")


def write_yolo_data_yaml(dataset: Path, classes: list[str]) -> None:
    names = "\n".join(f"  {index}: {name}" for index, name in enumerate(classes))
    yaml_text = f"""path: {dataset.as_posix()}
train: images/train
val: images/val
test: images/test
names:
{names}
"""
    (dataset / "data.yaml").write_text(yaml_text, encoding="utf-8")
    print(f"YOLO data config written: {dataset / 'data.yaml'}")


def train_yolo(paths: ProjectPaths, model: str, epochs: int, imgsz: int, batch: int, device: str | None) -> None:
    data_yaml = paths.dataset / "data.yaml"
    require_file(data_yaml, "YOLO data.yaml")
    command = [
        sys.executable,
        "-m",
        "ultralytics",
        "train",
        f"model={model}",
        f"data={data_yaml}",
        f"epochs={epochs}",
        f"imgsz={imgsz}",
        f"batch={batch}",
        f"project={paths.models}",
        "name=game_yolo",
    ]
    if device:
        command.append(f"device={device}")
    run_command(command)


def validate_yolo(paths: ProjectPaths, weights: Path, imgsz: int, device: str | None) -> None:
    data_yaml = paths.dataset / "data.yaml"
    require_file(data_yaml, "YOLO data.yaml")
    require_file(weights, "Weights")
    command = [
        sys.executable,
        "-m",
        "ultralytics",
        "val",
        f"model={weights}",
        f"data={data_yaml}",
        f"imgsz={imgsz}",
        f"project={paths.reports}",
        "name=val",
    ]
    if device:
        command.append(f"device={device}")
    run_command(command)


def inspect_dataset(paths: ProjectPaths) -> None:
    classes_file = paths.root / "classes.txt"
    classes = read_classes(classes_file) if classes_file.exists() else []
    print(f"Classes ({len(classes)}): {', '.join(classes) if classes else '(none)'}")
    for split in ["train", "val", "test"]:
        images = iter_images(paths.dataset / "images" / split)
        labels = sorted((paths.dataset / "labels" / split).glob("*.txt"))
        print(f"{split}: {len(images)} images, {len(labels)} labels")
    print(f"Raw frames: {len(iter_images(paths.raw_frames))}")
    print(f"Selected frames: {len(iter_images(paths.selected_frames))}")
    print(f"Annotated images: {len(iter_images(paths.annotated / 'images'))}")


def select_monitor(monitors: list[dict], monitor_index: int) -> dict:
    if monitor_index < 0 or monitor_index >= len(monitors):
        raise SystemExit(f"Invalid monitor index {monitor_index}. MSS exposes 0..{len(monitors) - 1}; 0 means all monitors.")
    return monitors[monitor_index]


def parse_roi(roi: str | None, monitor: dict) -> dict:
    if not roi:
        return monitor
    parts = [int(part.strip()) for part in roi.split(",")]
    if len(parts) != 4:
        raise SystemExit("ROI must be formatted as left,top,width,height")
    left, top, width, height = parts
    return {"left": left, "top": top, "width": width, "height": height}


def load_runtime_config(config_path: Path | None) -> dict:
    if config_path is None or not config_path.exists():
        return {
            "state_priority": ["CRASHED", "DISCONNECTED", "DEAD", "LOADING", "MENU", "COMBAT", "SEARCH"],
            "combat_states": ["COMBAT", "SEARCH", "UNKNOWN"],
            "ui_regions": [],
            "no_target_recovery": {"enabled": False},
            "save_failure_frames": False,
            "failure_frame_cooldown": 5,
        }
    return json.loads(config_path.read_text(encoding="utf-8"))


def crop_absolute_roi(frame_bgr, capture_area: dict, roi_values: list[int] | tuple[int, int, int, int]):
    left, top, width, height = [int(value) for value in roi_values]
    cap_left = int(capture_area["left"])
    cap_top = int(capture_area["top"])
    cap_right = cap_left + int(capture_area["width"])
    cap_bottom = cap_top + int(capture_area["height"])
    right = left + width
    bottom = top + height

    clipped_left = max(left, cap_left)
    clipped_top = max(top, cap_top)
    clipped_right = min(right, cap_right)
    clipped_bottom = min(bottom, cap_bottom)
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None

    x1 = clipped_left - cap_left
    y1 = clipped_top - cap_top
    x2 = clipped_right - cap_left
    y2 = clipped_bottom - cap_top
    return frame_bgr[y1:y2, x1:x2]


def detect_ui_events(frame_bgr, capture_area: dict, runtime_config: dict, root: Path, template_cache: dict) -> list[UIEvent]:
    import cv2
    import numpy as np

    events: list[UIEvent] = []
    for region in runtime_config.get("ui_regions", []):
        if region.get("enabled", True) is False:
            continue
        roi = region.get("roi")
        if not roi:
            continue
        crop = crop_absolute_roi(frame_bgr, capture_area, roi)
        if crop is None or crop.size == 0:
            continue

        region_type = region.get("type")
        matched = False
        score = 0.0

        if region_type == "template":
            template_path = root / str(region.get("template", ""))
            if not template_path.is_file():
                continue
            template_key = str(template_path)
            if template_key not in template_cache:
                template_cache[template_key] = cv2.imread(template_key, cv2.IMREAD_COLOR)
            template = template_cache.get(template_key)
            if template is None or crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
                continue
            result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, _ = cv2.minMaxLoc(result)
            score = float(max_value)
            matched = score >= float(region.get("threshold", 0.85))
        elif region_type == "brightness":
            mean_value = float(np.mean(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)))
            min_mean = region.get("min_mean")
            max_mean = region.get("max_mean")
            score = mean_value
            matched = (min_mean is None or mean_value >= float(min_mean)) and (max_mean is None or mean_value <= float(max_mean))
        elif region_type == "color":
            target = np.array(region.get("color_bgr", [0, 0, 0]), dtype=np.int16)
            tolerance = int(region.get("tolerance", 30))
            diff = np.abs(crop.astype(np.int16) - target)
            mask = np.all(diff <= tolerance, axis=2)
            ratio = float(np.count_nonzero(mask)) / float(mask.size)
            score = ratio
            matched = ratio >= float(region.get("min_ratio", 0.05))
        else:
            continue

        if matched:
            events.append(
                UIEvent(
                    name=str(region.get("name", region_type)),
                    state=str(region.get("state", "UNKNOWN")),
                    score=score,
                    roi=tuple(int(value) for value in roi),
                    recovery_after=float(region.get("recovery_after", 0)),
                    recovery=dict(region.get("recovery", {})),
                )
            )
    return events


def derive_game_state(events: list[UIEvent], target: Detection | None, runtime_config: dict) -> str:
    if events:
        priority = runtime_config.get("state_priority", [])
        states = {event.state for event in events}
        for state in priority:
            if state in states:
                return str(state)
        return events[0].state
    if target:
        return "COMBAT"
    return "SEARCH"


def execute_configured_action(controller: SendInputController, action: dict, dry_run_label: str) -> None:
    action_type = action.get("type")
    if not action_type:
        return
    if action_type == "key":
        controller.press_key(str(action.get("key", "esc")))
    elif action_type == "click":
        x = action.get("x")
        y = action.get("y")
        if x is not None and y is not None:
            controller.move_to(int(x), int(y))
        controller.click(str(action.get("button", "left")))
    elif action_type == "both":
        x = action.get("x")
        y = action.get("y")
        if x is not None and y is not None:
            controller.move_to(int(x), int(y))
        controller.click(str(action.get("button", "left")))
        controller.press_key(str(action.get("key", "space")))
    else:
        print(f"Unsupported recovery action for {dry_run_label}: {action_type}")


def maybe_save_failure_frame(frame_bgr, paths: ProjectPaths, state: RuntimeState, runtime_config: dict, now: float) -> None:
    if not runtime_config.get("save_failure_frames", False):
        return
    cooldown = float(runtime_config.get("failure_frame_cooldown", 5))
    if now - state.last_recovery_ts < cooldown:
        return
    import cv2

    ensure_dir(paths.failure_frames)
    output = paths.failure_frames / f"{time.strftime('%Y%m%d_%H%M%S')}_{state.game_state}.jpg"
    cv2.imwrite(str(output), frame_bgr)


def detections_from_result(result, names: dict[int, str], offset_x: int, offset_y: int) -> list[Detection]:
    detections: list[Detection] = []
    if result.boxes is None:
        return detections
    for box in result.boxes:
        xyxy = box.xyxy[0].tolist()
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        detections.append(
            Detection(
                class_id=class_id,
                class_name=str(names.get(class_id, class_id)),
                confidence=confidence,
                x1=int(xyxy[0]) + offset_x,
                y1=int(xyxy[1]) + offset_y,
                x2=int(xyxy[2]) + offset_x,
                y2=int(xyxy[3]) + offset_y,
            )
        )
    return detections


def choose_target(detections: list[Detection], target_class: str | None) -> Detection | None:
    candidates = [item for item in detections if target_class is None or item.class_name == target_class]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.confidence, item.area))


def execute_runtime_action(
    controller: SendInputController,
    target: Detection,
    action: str,
    key: str,
    mouse_button: str,
    aim: bool,
) -> None:
    if aim:
        x, y = target.center
        controller.move_to(x, y)
        time.sleep(0.01)
    if action in {"key", "both"}:
        controller.press_key(key)
    if action in {"click", "both"}:
        controller.click(mouse_button)


def append_runtime_log(log_path: Path, row: dict) -> None:
    ensure_dir(log_path.parent)
    is_new = not log_path.exists()
    keys = [
        "ts",
        "game_state",
        "mode",
        "ui_events",
        "detections",
        "target",
        "confidence",
        "action_count",
        "recovery_count",
        "dry_run",
    ]
    with log_path.open("a", encoding="utf-8") as file:
        if is_new:
            file.write(",".join(keys) + "\n")
        file.write(",".join(str(row.get(key, "")) for key in keys) + "\n")


def draw_debug_frame(
    frame,
    detections: list[Detection],
    target: Detection | None,
    state: RuntimeState,
    ui_events: list[UIEvent],
    roi_left: int,
    roi_top: int,
):
    import cv2

    for detection in detections:
        color = (0, 255, 0) if detection is target else (80, 180, 255)
        x1, y1 = detection.x1 - roi_left, detection.y1 - roi_top
        x2, y2 = detection.x2 - roi_left, detection.y2 - roi_top
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{detection.class_name} {detection.confidence:.2f}"
        cv2.putText(frame, label, (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    for event in ui_events:
        left, top, width, height = event.roi
        x1, y1 = left - roi_left, top - roi_top
        x2, y2 = x1 + width, y1 + height
        cv2.rectangle(frame, (x1, y1), (x2, y2), (30, 30, 255), 2)
        cv2.putText(frame, f"{event.name}:{event.state}", (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 255), 2)

    status = (
        f"{state.game_state}/{state.mode} stable={state.stable_hits} lost={state.lost_hits} "
        f"actions={state.action_count} recovery={state.recovery_count}"
    )
    cv2.putText(frame, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    return frame


def run_realtime(
    paths: ProjectPaths,
    weights: Path,
    runtime_config_path: Path | None,
    monitor_index: int,
    roi: str | None,
    target_class: str | None,
    confidence: float,
    imgsz: int,
    device: str | None,
    stable_frames: int,
    lost_frames: int,
    cooldown: float,
    action: str,
    key: str,
    mouse_button: str,
    aim: bool,
    debug: bool,
    dry_run: bool,
    stop_key: str,
) -> None:
    require_file(weights, "Weights")
    ensure_dir(paths.runtime_logs)

    try:
        import cv2
        import mss
        import numpy as np
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(f"Missing runtime dependency: {exc.name}. Run: pip install -r requirements.txt") from exc

    model = YOLO(str(weights))
    controller = SendInputController(dry_run=dry_run)
    state = RuntimeState(last_target_ts=time.monotonic())
    runtime_config = load_runtime_config(runtime_config_path)
    template_cache: dict = {}
    ui_seen_since: dict[str, float] = {}
    log_path = paths.runtime_logs / f"runtime_{time.strftime('%Y%m%d_%H%M%S')}.csv"

    print("Runtime started.")
    print(f"Stop key: {stop_key}. Dry-run: {dry_run}. Debug window: {debug}.")
    if runtime_config_path:
        print(f"Runtime config: {runtime_config_path}")
    print("For fullscreen games, run the game in borderless fullscreen first if exclusive fullscreen capture is black.")

    with mss.mss() as screen:
        monitor = select_monitor(screen.monitors, monitor_index)
        capture_area = parse_roi(roi, monitor)
        roi_left = int(capture_area["left"])
        roi_top = int(capture_area["top"])

        while True:
            loop_start = time.perf_counter()
            if controller.is_key_down(stop_key):
                print("Stop key pressed. Exiting runtime.")
                break

            screenshot = screen.grab(capture_area)
            frame_bgra = np.array(screenshot)
            frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)

            results = model.predict(frame_bgr, conf=confidence, imgsz=imgsz, device=device, verbose=False)
            detections = detections_from_result(results[0], model.names, roi_left, roi_top)
            target = choose_target(detections, target_class)
            now = time.monotonic()
            ui_events = detect_ui_events(frame_bgr, capture_area, runtime_config, paths.root, template_cache)
            state.ui_events = "|".join(f"{event.name}:{event.state}:{event.score:.3f}" for event in ui_events)
            state.game_state = derive_game_state(ui_events, target, runtime_config)
            active_ui_names = {event.name for event in ui_events}
            for event in ui_events:
                ui_seen_since.setdefault(event.name, now)
            for event_name in list(ui_seen_since):
                if event_name not in active_ui_names:
                    del ui_seen_since[event_name]

            if target:
                state.stable_hits += 1
                state.lost_hits = 0
                state.last_target_ts = now
                state.mode = "TARGET_LOCKED" if state.stable_hits >= stable_frames else "CONFIRMING"
            else:
                state.lost_hits += 1
                if state.lost_hits >= lost_frames:
                    state.stable_hits = 0
                    state.mode = "SEARCH"

            combat_states = set(str(item) for item in runtime_config.get("combat_states", ["COMBAT", "SEARCH", "UNKNOWN"]))
            can_combat_act = state.game_state in combat_states

            if can_combat_act and target and state.stable_hits >= stable_frames and now - state.last_action_ts >= cooldown:
                execute_runtime_action(controller, target, action, key, mouse_button, aim)
                state.last_action_ts = now
                state.action_count += 1
                state.mode = "ACTION"
            elif not can_combat_act:
                state.mode = "RECOVERY_PENDING"

            recovery_event = next((event for event in ui_events if event.recovery and event.recovery_after >= 0), None)
            if (
                recovery_event
                and now - ui_seen_since.get(recovery_event.name, now) >= recovery_event.recovery_after
                and now - state.last_recovery_ts >= max(1.0, recovery_event.recovery_after)
            ):
                print(f"Recovery for UI event: {recovery_event.name} -> {recovery_event.recovery}")
                maybe_save_failure_frame(frame_bgr, paths, state, runtime_config, now)
                execute_configured_action(controller, recovery_event.recovery, recovery_event.name)
                state.last_recovery_ts = now
                state.recovery_count += 1
                state.mode = "RECOVERY"

            no_target_recovery = runtime_config.get("no_target_recovery", {})
            if (
                can_combat_act
                and no_target_recovery.get("enabled", False)
                and not target
                and now - state.last_target_ts >= float(no_target_recovery.get("after_seconds", 8))
                and now - state.last_recovery_ts >= float(no_target_recovery.get("cooldown", 4))
            ):
                print(f"No target recovery: {no_target_recovery.get('action', {})}")
                maybe_save_failure_frame(frame_bgr, paths, state, runtime_config, now)
                execute_configured_action(controller, dict(no_target_recovery.get("action", {})), "no_target")
                state.last_recovery_ts = now
                state.recovery_count += 1
                state.mode = "NO_TARGET_RECOVERY"

            append_runtime_log(
                log_path,
                {
                    "ts": f"{time.time():.3f}",
                    "game_state": state.game_state,
                    "mode": state.mode,
                    "ui_events": state.ui_events,
                    "detections": len(detections),
                    "target": target.class_name if target else "",
                    "confidence": f"{target.confidence:.3f}" if target else "",
                    "action_count": state.action_count,
                    "recovery_count": state.recovery_count,
                    "dry_run": dry_run,
                },
            )

            if debug:
                debug_frame = draw_debug_frame(frame_bgr, detections, target, state, ui_events, roi_left, roi_top)
                fps = 1.0 / max(0.001, time.perf_counter() - loop_start)
                cv2.putText(debug_frame, f"FPS {fps:.1f}", (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
                cv2.imshow("game-agent-runtime", debug_frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    if debug:
        cv2.destroyAllWindows()
    print(f"Runtime log written: {log_path}")


def launch_stage1_gui(paths: ProjectPaths) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    init_project(paths, [])

    root = tk.Tk()
    root.title("Game AI - 第一阶段可视化流程")
    root.geometry("980x700")
    root.minsize(900, 620)

    video_var = tk.StringVar()
    fps_var = tk.StringVar(value="2")
    classes_var = tk.StringVar(value=", ".join(read_classes(paths.root / "classes.txt")))
    val_var = tk.StringVar(value="0.2")
    test_var = tk.StringVar(value="0.1")
    model_var = tk.StringVar(value="yolov8n.pt")
    epochs_var = tk.StringVar(value="80")
    imgsz_var = tk.StringVar(value="640")
    batch_var = tk.StringVar(value="16")
    status_var = tk.StringVar(value="就绪")
    running = {"active": False}

    def log(message: str) -> None:
        log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        log_box.see("end")

    def refresh_counts() -> None:
        raw_count = len(iter_images(paths.raw_frames))
        selected_count = len(iter_images(paths.selected_frames))
        annotated_images = len(iter_images(paths.annotated / "images"))
        annotated_labels = len(list((paths.annotated / "labels").glob("*.txt"))) if (paths.annotated / "labels").exists() else 0
        train_count = len(iter_images(paths.dataset / "images" / "train"))
        val_count = len(iter_images(paths.dataset / "images" / "val"))
        test_count = len(iter_images(paths.dataset / "images" / "test"))
        counts_var.set(
            f"原始帧: {raw_count}    已筛选: {selected_count}    "
            f"已标注: {annotated_images} 张图 / {annotated_labels} 个标签    "
            f"数据集: 训练 {train_count}, 验证 {val_count}, 测试 {test_count}"
        )

    def run_background(label: str, task) -> None:
        if running["active"]:
            messagebox.showinfo("任务运行中", "已有任务正在执行，请等待完成。")
            return

        def worker() -> None:
            running["active"] = True
            root.after(0, lambda: status_var.set(f"正在执行：{label}"))
            root.after(0, lambda: log(f"开始：{label}"))
            try:
                task()
            except Exception as exc:
                root.after(0, lambda: log(f"错误：{label}: {exc}"))
                root.after(0, lambda: messagebox.showerror(label, str(exc)))
            else:
                root.after(0, lambda: log(f"完成：{label}"))
            finally:
                running["active"] = False
                root.after(0, refresh_counts)
                root.after(0, lambda: status_var.set("就绪"))

        threading.Thread(target=worker, daemon=True).start()

    def pick_video() -> None:
        initial_dir = paths.raw_videos if paths.raw_videos.exists() else paths.root
        file_path = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            title="选择游戏录屏视频",
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi"), ("所有文件", "*.*")],
        )
        if file_path:
            video_var.set(file_path)

    def open_path(path: Path) -> None:
        ensure_dir(path if path.suffix == "" else path.parent)
        os.startfile(str(path))

    def open_review() -> None:
        review_path = paths.reports / "frame_review.html"
        if not review_path.exists():
            messagebox.showwarning("筛选页不存在", "请先生成图片筛选页。")
            return
        webbrowser.open(review_path.resolve().as_uri())

    def open_labelimg_log() -> None:
        log_path = paths.runtime_logs / "labelimg_last.log"
        ensure_dir(log_path.parent)
        if not log_path.exists():
            log_path.write_text("还没有 LabelImg 日志。请先启动 LabelImg。\n", encoding="utf-8")
        os.startfile(str(log_path))

    def save_classes() -> None:
        classes = [item.strip() for item in classes_var.get().replace("\n", ",").split(",") if item.strip()]
        if not classes:
            messagebox.showwarning("缺少类别", "请至少输入一个类别名。")
            return
        (paths.root / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
        log(f"已保存类别：{', '.join(classes)}")

    def do_extract() -> None:
        video = Path(video_var.get().strip())
        fps = float(fps_var.get())
        extract_frames(video.resolve(), paths.raw_frames.resolve(), fps, None)

    def do_review() -> None:
        write_review_manifest(paths.raw_frames.resolve(), (paths.reports / "frame_review.html").resolve(), "Frame Review")

    def do_select_all() -> None:
        copy_images(paths.raw_frames.resolve(), paths.selected_frames.resolve())

    def do_copy_to_annotated() -> None:
        copy_images(paths.selected_frames.resolve(), (paths.annotated / "images").resolve())

    def do_install_labelimg() -> None:
        python_exe = project_python(paths)
        run_command([str(python_exe), "-m", "pip", "install", "labelImg"])
        if patch_labelimg_pyqt_compat(paths):
            log("已修复 LabelImg 与新版 PyQt 的兼容问题。")

    def launch_labelimg() -> None:
        ensure_dir(paths.annotated / "images")
        ensure_dir(paths.annotated / "labels")
        executable = find_labelimg_executable(paths)
        if executable is None:
            messagebox.showwarning("未找到 LabelImg", "没有找到 LabelImg。请先点击“安装/修复 LabelImg”。")
            return
        if patch_labelimg_pyqt_compat(paths):
            log("已修复 LabelImg 与新版 PyQt 的兼容问题。")
        ensure_dir(paths.runtime_logs)
        log_path = paths.runtime_logs / "labelimg_last.log"
        log_file = log_path.open("w", encoding="utf-8")
        log_file.write(f"启动时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"程序路径：{executable}\n\n")
        log_file.flush()
        subprocess.Popen(
            [str(executable)],
            cwd=str(paths.root),
            stdout=log_file,
            stderr=log_file,
            close_fds=False,
        )
        log(f"已启动 LabelImg：{executable}")
        log(f"LabelImg 错误日志：{log_path}")

    def do_build_dataset() -> None:
        build_dataset(paths, float(val_var.get()), float(test_var.get()), 42)

    def do_train() -> None:
        train_yolo(paths, model_var.get().strip(), int(epochs_var.get()), int(imgsz_var.get()), int(batch_var.get()), None)

    style = ttk.Style()
    style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
    style.configure("Step.TLabelframe.Label", font=("Segoe UI", 11, "bold"))

    main_frame = ttk.Frame(root, padding=16)
    main_frame.pack(fill="both", expand=True)

    header = ttk.Frame(main_frame)
    header.pack(fill="x", pady=(0, 12))
    ttk.Label(header, text="第一阶段可视化流程", style="Title.TLabel").pack(side="left")
    ttk.Label(header, textvariable=status_var).pack(side="right")

    counts_var = tk.StringVar()
    ttk.Label(main_frame, textvariable=counts_var, foreground="#444").pack(fill="x", pady=(0, 10))

    body = ttk.PanedWindow(main_frame, orient="horizontal")
    body.pack(fill="both", expand=True)

    left = ttk.Frame(body, padding=(0, 0, 12, 0))
    right = ttk.Frame(body)
    body.add(left, weight=2)
    body.add(right, weight=3)

    video_box = ttk.LabelFrame(left, text="1. 视频抽帧", style="Step.TLabelframe")
    video_box.pack(fill="x", pady=(0, 10))
    ttk.Entry(video_box, textvariable=video_var).pack(fill="x", padx=10, pady=(10, 6))
    row = ttk.Frame(video_box)
    row.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(row, text="选择视频", command=pick_video).pack(side="left")
    ttk.Label(row, text="FPS").pack(side="left", padx=(12, 4))
    ttk.Entry(row, width=6, textvariable=fps_var).pack(side="left")
    ttk.Button(row, text="开始抽帧", command=lambda: run_background("抽帧", do_extract)).pack(side="right")

    review_box = ttk.LabelFrame(left, text="2. 图片筛选", style="Step.TLabelframe")
    review_box.pack(fill="x", pady=(0, 10))
    ttk.Button(review_box, text="生成筛选页面", command=lambda: run_background("生成筛选页面", do_review)).pack(fill="x", padx=10, pady=(10, 6))
    ttk.Button(review_box, text="打开筛选页面", command=open_review).pack(fill="x", padx=10, pady=6)
    ttk.Button(review_box, text="打开原始帧目录 raw_frames", command=lambda: open_path(paths.raw_frames)).pack(fill="x", padx=10, pady=6)
    ttk.Button(review_box, text="打开已筛选目录 selected_frames", command=lambda: open_path(paths.selected_frames)).pack(fill="x", padx=10, pady=6)
    ttk.Button(review_box, text="全部原始帧复制到 selected_frames", command=lambda: run_background("复制全部原始帧", do_select_all)).pack(fill="x", padx=10, pady=(6, 10))

    annotate_box = ttk.LabelFrame(left, text="3. 标注准备", style="Step.TLabelframe")
    annotate_box.pack(fill="x", pady=(0, 10))
    ttk.Label(annotate_box, text="类别名，多个类别用逗号分隔").pack(anchor="w", padx=10, pady=(10, 2))
    ttk.Entry(annotate_box, textvariable=classes_var).pack(fill="x", padx=10, pady=(0, 6))
    ttk.Button(annotate_box, text="保存 classes.txt", command=save_classes).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="已筛选图片复制到 annotated/images", command=lambda: run_background("复制到 annotated/images", do_copy_to_annotated)).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="安装/修复 LabelImg", command=lambda: run_background("安装/修复 LabelImg", do_install_labelimg)).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="启动 LabelImg", command=launch_labelimg).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="打开 LabelImg 错误日志", command=open_labelimg_log).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="打开标注图片目录 annotated/images", command=lambda: open_path(paths.annotated / "images")).pack(fill="x", padx=10, pady=6)
    ttk.Button(annotate_box, text="打开标签目录 annotated/labels", command=lambda: open_path(paths.annotated / "labels")).pack(fill="x", padx=10, pady=(6, 10))

    dataset_box = ttk.LabelFrame(left, text="4. 构建数据集与训练", style="Step.TLabelframe")
    dataset_box.pack(fill="x")
    ratio_row = ttk.Frame(dataset_box)
    ratio_row.pack(fill="x", padx=10, pady=(10, 6))
    ttk.Label(ratio_row, text="Val").pack(side="left")
    ttk.Entry(ratio_row, width=6, textvariable=val_var).pack(side="left", padx=(4, 12))
    ttk.Label(ratio_row, text="Test").pack(side="left")
    ttk.Entry(ratio_row, width=6, textvariable=test_var).pack(side="left", padx=(4, 12))
    ttk.Button(ratio_row, text="构建数据集", command=lambda: run_background("构建数据集", do_build_dataset)).pack(side="right")

    train_row_1 = ttk.Frame(dataset_box)
    train_row_1.pack(fill="x", padx=10, pady=6)
    ttk.Label(train_row_1, text="模型").pack(side="left")
    ttk.Entry(train_row_1, width=14, textvariable=model_var).pack(side="left", padx=(4, 12))
    ttk.Label(train_row_1, text="轮数").pack(side="left")
    ttk.Entry(train_row_1, width=6, textvariable=epochs_var).pack(side="left", padx=(4, 12))

    train_row_2 = ttk.Frame(dataset_box)
    train_row_2.pack(fill="x", padx=10, pady=(6, 10))
    ttk.Label(train_row_2, text="imgsz").pack(side="left")
    ttk.Entry(train_row_2, width=6, textvariable=imgsz_var).pack(side="left", padx=(4, 12))
    ttk.Label(train_row_2, text="batch").pack(side="left")
    ttk.Entry(train_row_2, width=6, textvariable=batch_var).pack(side="left", padx=(4, 12))
    ttk.Button(train_row_2, text="训练 YOLO", command=lambda: run_background("训练 YOLO", do_train)).pack(side="right")

    guide = ttk.LabelFrame(right, text="流程说明", style="Step.TLabelframe")
    guide.pack(fill="x", pady=(0, 10))
    notes = (
        "1. 选择 data/raw_videos 中的视频，FPS 建议 2 或 5，然后点击开始抽帧。\n"
        "2. 生成并打开筛选页面，把有价值的图片复制到 data/selected_frames。\n"
        "3. 保存类别名，然后把已筛选图片复制到 data/annotated/images。\n"
        "4. 使用 LabelImg：Open Dir 选 annotated/images，Change Save Dir 选 annotated/labels，格式选 YOLO。\n"
        "5. 每张图片都有同名 .txt 标签后，再构建数据集。\n"
        "6. 确认数据集数量正确后，再开始训练。"
    )
    ttk.Label(guide, text=notes, justify="left").pack(fill="x", padx=10, pady=10)

    log_frame = ttk.LabelFrame(right, text="日志", style="Step.TLabelframe")
    log_frame.pack(fill="both", expand=True)
    log_box = tk.Text(log_frame, height=20, wrap="word")
    scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=log_box.yview)
    log_box.configure(yscrollcommand=scrollbar.set)
    log_box.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
    scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=10)

    footer = ttk.Frame(main_frame)
    footer.pack(fill="x", pady=(10, 0))
    ttk.Button(footer, text="刷新数量", command=refresh_counts).pack(side="left")
    ttk.Button(footer, text="打开项目目录", command=lambda: open_path(paths.root)).pack(side="left", padx=8)
    ttk.Button(footer, text="打开说明文档", command=lambda: os.startfile(str(paths.root / "README_STAGE1.md"))).pack(side="left")
    ttk.Button(footer, text="退出", command=root.destroy).pack(side="right")

    refresh_counts()
    log("界面已就绪。")
    root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Game-screen YOLO dataset pipeline and realtime runtime.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create first-stage project folders.")
    init.add_argument("--classes", nargs="*", default=[], help="Initial class names, e.g. enemy hp_bar item.")

    runtime_config = sub.add_parser("init-runtime-config", help="Create or overwrite runtime_config.json.")
    runtime_config.add_argument("--overwrite", action="store_true", help="Overwrite existing runtime_config.json.")

    sub.add_parser("gui", help="Launch a local visual UI for the first-stage workflow.")

    extract = sub.add_parser("extract", help="Extract video frames with ffmpeg.")
    extract.add_argument("video", type=Path, help="Input video path.")
    extract.add_argument("--fps", type=float, default=2.0, help="Frames per second to extract.")
    extract.add_argument("--out", type=Path, default=None, help="Output frame directory. Default: data/raw_frames.")
    extract.add_argument("--prefix", default=None, help="Output filename prefix. Default: video stem.")

    review = sub.add_parser("review", help="Generate an HTML contact sheet for manual frame review.")
    review.add_argument("--src", type=Path, default=None, help="Image directory to review. Default: data/raw_frames.")
    review.add_argument("--out", type=Path, default=None, help="HTML output. Default: reports/frame_review.html.")

    select = sub.add_parser("select-all", help="Copy all raw frames to selected_frames as a starting point.")
    select.add_argument("--src", type=Path, default=None, help="Source image directory. Default: data/raw_frames.")
    select.add_argument("--dst", type=Path, default=None, help="Destination image directory. Default: data/selected_frames.")

    dataset = sub.add_parser("build-dataset", help="Split annotated YOLO images/labels into train/val/test.")
    dataset.add_argument("--val", type=float, default=0.2, help="Validation ratio.")
    dataset.add_argument("--test", type=float, default=0.1, help="Test ratio.")
    dataset.add_argument("--seed", type=int, default=42, help="Random seed for split.")

    train = sub.add_parser("train", help="Train YOLO through ultralytics.")
    train.add_argument("--model", default="yolov8n.pt", help="Base model, e.g. yolov8n.pt or yolov8s.pt.")
    train.add_argument("--epochs", type=int, default=80)
    train.add_argument("--imgsz", type=int, default=640)
    train.add_argument("--batch", type=int, default=16)
    train.add_argument("--device", default=None, help="Ultralytics device, e.g. 0 or cpu.")

    val = sub.add_parser("val", help="Validate YOLO weights through ultralytics.")
    val.add_argument("weights", type=Path, help="Trained weights path, usually models/game_yolo/weights/best.pt.")
    val.add_argument("--imgsz", type=int, default=640)
    val.add_argument("--device", default=None)

    run = sub.add_parser("run", help="Run realtime fullscreen detection and low-level input control.")
    run.add_argument("weights", type=Path, help="Trained weights path, usually models/game_yolo/weights/best.pt.")
    run.add_argument("--runtime-config", type=Path, default=None, help="Runtime config JSON. Default: runtime_config.json if it exists.")
    run.add_argument("--monitor", type=int, default=1, help="MSS monitor index. 1 is primary monitor; 0 captures all monitors.")
    run.add_argument("--roi", default=None, help="Optional capture ROI: left,top,width,height. Use screen coordinates.")
    run.add_argument("--target-class", default=None, help="Class name to trigger on. Default: any detected class.")
    run.add_argument("--conf", type=float, default=0.45, help="YOLO confidence threshold.")
    run.add_argument("--imgsz", type=int, default=640)
    run.add_argument("--device", default=None, help="Ultralytics device, e.g. 0 or cpu.")
    run.add_argument("--stable-frames", type=int, default=3, help="Consecutive target frames required before action.")
    run.add_argument("--lost-frames", type=int, default=5, help="Consecutive missing frames before resetting state.")
    run.add_argument("--cooldown", type=float, default=0.6, help="Minimum seconds between actions.")
    run.add_argument("--action", choices=["key", "click", "both"], default="key", help="Action to execute when target is stable.")
    run.add_argument("--key", default="space", help="Key for key/both actions. Examples: space, e, f, 1, f8.")
    run.add_argument("--mouse-button", choices=["left", "right"], default="left")
    run.add_argument("--aim", action="store_true", help="Move cursor to target center before clicking/pressing.")
    run.add_argument("--debug", action="store_true", help="Show OpenCV debug window. Press q to close it.")
    run.add_argument("--dry-run", action="store_true", help="Do not send keyboard/mouse input.")
    run.add_argument("--stop-key", default="f8", help="Emergency stop hotkey checked through GetAsyncKeyState.")

    sub.add_parser("inspect", help="Print current dataset counts.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = ProjectPaths(args.root.resolve())

    if args.command == "init":
        init_project(paths, args.classes)
    elif args.command == "init-runtime-config":
        write_default_runtime_config(paths, overwrite=args.overwrite)
    elif args.command == "gui":
        launch_stage1_gui(paths)
    elif args.command == "extract":
        extract_frames(args.video.resolve(), (args.out or paths.raw_frames).resolve(), args.fps, args.prefix)
    elif args.command == "review":
        write_review_manifest((args.src or paths.raw_frames).resolve(), (args.out or paths.reports / "frame_review.html").resolve(), "Frame Review")
    elif args.command == "select-all":
        copy_images((args.src or paths.raw_frames).resolve(), (args.dst or paths.selected_frames).resolve())
    elif args.command == "build-dataset":
        build_dataset(paths, args.val, args.test, args.seed)
    elif args.command == "train":
        train_yolo(paths, args.model, args.epochs, args.imgsz, args.batch, args.device)
    elif args.command == "val":
        validate_yolo(paths, args.weights.resolve(), args.imgsz, args.device)
    elif args.command == "run":
        runtime_config_path = args.runtime_config
        if runtime_config_path is None and (paths.root / "runtime_config.json").exists():
            runtime_config_path = paths.root / "runtime_config.json"
        run_realtime(
            paths=paths,
            weights=args.weights.resolve(),
            runtime_config_path=runtime_config_path.resolve() if runtime_config_path else None,
            monitor_index=args.monitor,
            roi=args.roi,
            target_class=args.target_class,
            confidence=args.conf,
            imgsz=args.imgsz,
            device=args.device,
            stable_frames=args.stable_frames,
            lost_frames=args.lost_frames,
            cooldown=args.cooldown,
            action=args.action,
            key=args.key,
            mouse_button=args.mouse_button,
            aim=args.aim,
            debug=args.debug,
            dry_run=args.dry_run,
            stop_key=args.stop_key,
        )
    elif args.command == "inspect":
        inspect_dataset(paths)


if __name__ == "__main__":
    main()
