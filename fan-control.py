#!/usr/bin/env python3
"""ThinkPad fan control GUI.

Modes:
  * Manual   - pin a fixed fan level.
  * Curve    - temperature -> level mapping with hysteresis (preset or custom).
  * Target   - closed-loop "thermostat": hold a chosen temperature, auto-adapting
               the fan to whatever the workload is doing.

Also: AC/battery-aware curves, a live temp/RPM graph, and optional
telemetry (Prometheus/VictoriaMetrics push + ntfy critical alerts).
"""

import csv
import glob
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

FAN_PATH = "/proc/acpi/ibm/fan"
FANCTL = "/usr/local/bin/fanctl"
MAX_RPM = 8000
FAN_CONTROL_PARAM = "/sys/module/thinkpad_acpi/parameters/fan_control"
SETUP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup-fan-control.sh")

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "fan-control")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

BAT_PATH = "/sys/class/power_supply/BAT0"
RAPL_PATH = "/sys/class/powercap/intel-rapl:0"

VERSION = "2.1"
APP_PATH = os.path.abspath(__file__)
AUTOSTART_PATH = os.path.join(os.path.expanduser("~"), ".config", "autostart",
                              "fan-control.desktop")

ACCENT = "#e74c3c"
STYLESHEET = """
* { font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif; color: #e6e8eb;
    font-size: 13px; }
QMainWindow, QWidget { background: #14161a; }
QLabel { background: transparent; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QGroupBox {
    background: #1c1f26; border: 1px solid #2a2e37; border-radius: 12px;
    margin-top: 16px; padding: 16px 14px 12px 14px; font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 14px; padding: 0 6px; color: #9aa1ab;
    text-transform: uppercase; font-size: 11px; letter-spacing: 1px;
}
QTabWidget::pane {
    border: 1px solid #2a2e37; border-radius: 12px; top: -1px; background: #1c1f26;
}
QTabBar::tab {
    background: transparent; color: #9aa1ab; padding: 8px 22px;
    margin-right: 2px; border: none; border-bottom: 2px solid transparent;
    font-weight: 500;
}
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid %(accent)s; }
QTabBar::tab:hover:!selected { color: #cfd3da; }
QPushButton {
    background: #262b34; color: #e6e8eb; border: 1px solid #333945;
    border-radius: 8px; padding: 8px 14px;
}
QPushButton:hover { background: #2f3540; border-color: #3d4552; }
QPushButton:pressed { background: %(accent)s; border-color: %(accent)s; color: #fff; }
QPushButton:disabled { color: #5a606b; background: #1a1d23; border-color: #262b34; }
QProgressBar {
    background: #0f1114; border: 1px solid #2a2e37; border-radius: 8px;
    min-height: 20px; text-align: center; color: #e6e8eb;
}
QProgressBar::chunk {
    border-radius: 7px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b6ea5, stop:1 #5aa0e0);
}
QComboBox, QSpinBox {
    background: #0f1114; border: 1px solid #333945; border-radius: 8px;
    padding: 6px 10px; color: #e6e8eb; min-height: 22px;
}
QComboBox:hover, QSpinBox:hover { border-color: #3d4552; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #1c1f26; border: 1px solid #333945;
    selection-background-color: %(accent)s; color: #e6e8eb; padding: 4px;
}
QRadioButton, QCheckBox { spacing: 8px; background: transparent; padding: 2px 0; }
QRadioButton::indicator, QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked {
    border: 1px solid #4a5160; border-radius: 4px; background: #0f1114;
}
QRadioButton::indicator:unchecked {
    border: 1px solid #4a5160; border-radius: 9px; background: #0f1114;
}
QCheckBox::indicator:checked {
    border: 1px solid %(accent)s; border-radius: 4px; background: %(accent)s;
}
QRadioButton::indicator:checked {
    border: 1px solid %(accent)s; border-radius: 9px; background: %(accent)s;
}
QToolTip {
    background: #14161a; color: #e6e8eb; border: 1px solid %(accent)s;
    padding: 6px 8px; border-radius: 6px;
}
QMenu { background: #1c1f26; border: 1px solid #333945; color: #e6e8eb; padding: 4px; }
QMenu::item { padding: 6px 20px; border-radius: 6px; }
QMenu::item:selected { background: %(accent)s; }
QTableWidget {
    background: #0f1114; gridline-color: #2a2e37; border: 1px solid #2a2e37;
    border-radius: 8px;
}
QHeaderView::section {
    background: #1c1f26; color: #9aa1ab; border: none; padding: 6px; font-weight: 600;
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #333945; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #454d5a; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""" % {"accent": ACCENT}

# Card look for the monospace readout labels (Power / Battery / Sensors panels).
READOUT_CSS = ("background: #0f1114; border: 1px solid #2a2e37; "
               "border-radius: 8px; padding: 10px 12px;")

CURVE_LEVELS = ["0", "1", "2", "3", "4", "5", "6", "7", "full-speed"]
CRITICAL_TEMP = 90
REASSERT_TICKS = 60       # re-assert held level every N s (defeats EC watchdog)
TARGET_INTERVAL = 3       # seconds between target-mode adjustments
DEFAULT_TARGET = 80       # °C for target mode
DEFAULT_HYSTERESIS = 5

DEFAULT_CURVE = [
    {"temp": 0, "level": "0"},
    {"temp": 50, "level": "1"},
    {"temp": 60, "level": "2"},
    {"temp": 70, "level": "4"},
    {"temp": 78, "level": "6"},
    {"temp": 85, "level": "7"},
    {"temp": 90, "level": "full-speed"},
]

# Curve presets, ordered quiet -> aggressive. Keyed off max(CPU, GPU).
PRESETS = {
    "Quiet Night": [
        {"temp": 0, "level": "0"}, {"temp": 65, "level": "1"},
        {"temp": 78, "level": "2"}, {"temp": 86, "level": "4"},
        {"temp": 90, "level": "7"},
    ],
    "Silent": [
        {"temp": 0, "level": "0"}, {"temp": 60, "level": "1"},
        {"temp": 72, "level": "2"}, {"temp": 82, "level": "4"},
        {"temp": 88, "level": "7"},
    ],
    "Balanced": DEFAULT_CURVE,
    "Performance": [
        {"temp": 0, "level": "2"}, {"temp": 50, "level": "4"},
        {"temp": 62, "level": "6"}, {"temp": 72, "level": "7"},
        {"temp": 82, "level": "full-speed"},
    ],
    # Sustained GPU load: floor at level 4 so airflow never dips mid-session,
    # then ramp hard as the GPU heats.
    "Gaming": [
        {"temp": 0, "level": "4"}, {"temp": 60, "level": "5"},
        {"temp": 70, "level": "6"}, {"temp": 78, "level": "7"},
        {"temp": 85, "level": "full-speed"},
    ],
    # Long compiles / local LLM inference: get ahead of heat early and hold a
    # high steady airflow to avoid thermal throttling on multi-minute jobs.
    "AI / Compute": [
        {"temp": 0, "level": "2"}, {"temp": 50, "level": "4"},
        {"temp": 60, "level": "6"}, {"temp": 70, "level": "7"},
        {"temp": 80, "level": "full-speed"},
    ],
    # Keep it as cold as possible, noise be damned.
    "Aggressive": [
        {"temp": 0, "level": "3"}, {"temp": 45, "level": "4"},
        {"temp": 55, "level": "6"}, {"temp": 65, "level": "7"},
        {"temp": 75, "level": "full-speed"},
    ],
}
PRESET_NAMES = list(PRESETS) + ["Custom"]

# Auto-profile: process-name signals (comm is truncated to 15 chars in /proc).
GAME_PROCS = {"gamescope", "wine", "wine64", "wineserver", "wine-preloader",
              "proton", "lutris", "heroic", "retroarch", "dosbox", "rpcs3",
              "yuzu", "ryujinx", "pcsx2", "ppsspp", "citra", "steamwebhelper"}
AI_PROCS = {"ollama", "llama-server", "llama-cli", "llama-bench", "vllm",
            "cc1", "cc1plus", "gcc", "g++", "clang", "clang++", "ld", "lld",
            "make", "ninja", "cargo", "rustc", "ffmpeg", "x264", "x265",
            "blender", "HandBrakeCLI", "torchrun"}
AUTO_INTERVAL = 5   # seconds between workload checks
AUTO_DEBOUNCE = 2   # consecutive checks a new profile must win before switching

# CPU power-limit presets (PL1, PL2 watts) — shared by the Power tab, the
# AC/battery auto-adapt, and the built-in benchmark.
PL_PRESETS = {
    "Quiet": (25, 45),
    "Balanced": (45, 65),
    "Performance": (65, 109),
    "Max": (109, 135),
}
PL_PRESET_ORDER = ["Quiet", "Balanced", "Performance", "Max"]
# Which power-profiles-daemon profile pairs with each PL preset.
PL_PPD = {"Quiet": "power-saver", "Balanced": "balanced",
          "Performance": "performance", "Max": "performance"}

HISTORY_MAX = 3600          # samples kept for CSV export (~1 h at 1 s)
BENCH_LOAD_SECS = 25        # per-preset sustained load in the in-app benchmark
BENCH_SAMPLE_SECS = 10      # trailing window sampled for steady-state clock/temp

DEFAULT_TELEMETRY = {
    "enabled": False,
    # Optional: push fan/temp metrics to a Prometheus-compatible endpoint
    # (VictoriaMetrics import API works). Leave blank to disable.
    "vm_url": "",
    # Optional: send a critical-temp alert to an ntfy server/topic.
    "ntfy_url": "",
    "ntfy_topic": "",
    "push_interval": 15,
    "alert_temp": 92,
    "alert_sustain": 30,
}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _norm_curve(curve) -> list:
    if not isinstance(curve, list) or not curve:
        return [dict(s) for s in DEFAULT_CURVE]
    out = [s for s in curve if isinstance(s, dict) and "temp" in s and "level" in s]
    return sorted(out, key=lambda s: s["temp"]) or [dict(s) for s in DEFAULT_CURVE]


def load_config() -> dict:
    cfg = {
        "mode": "manual",
        "curves": {"ac": [dict(s) for s in DEFAULT_CURVE],
                   "battery": [dict(s) for s in DEFAULT_CURVE]},
        "hysteresis": DEFAULT_HYSTERESIS,
        "power_auto": False,
        "auto_profile": False,
        "preset": "Balanced",
        "target_temp": DEFAULT_TARGET,
        "telemetry": dict(DEFAULT_TELEMETRY),
        "desktop_notify": True,
        "geometry": None,
        "power_adapt": False,
        "power_ac_preset": "Performance",
        "power_batt_preset": "Balanced",
    }
    try:
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        saved = {}

    if saved.get("mode") in ("manual", "curve", "target"):
        cfg["mode"] = saved["mode"]
    if isinstance(saved.get("hysteresis"), int):
        cfg["hysteresis"] = saved["hysteresis"]
    cfg["power_auto"] = bool(saved.get("power_auto", False))
    cfg["auto_profile"] = bool(saved.get("auto_profile", False))
    if saved.get("preset") in PRESET_NAMES:
        cfg["preset"] = saved["preset"]
    if isinstance(saved.get("target_temp"), int):
        cfg["target_temp"] = max(60, min(90, saved["target_temp"]))

    curves = saved.get("curves")
    if isinstance(curves, dict):
        cfg["curves"]["ac"] = _norm_curve(curves.get("ac"))
        cfg["curves"]["battery"] = _norm_curve(curves.get("battery"))
    elif "curve" in saved:  # migrate v1 single curve
        c = _norm_curve(saved["curve"])
        cfg["curves"]["ac"] = c
        cfg["curves"]["battery"] = [dict(s) for s in c]

    if isinstance(saved.get("telemetry"), dict):
        cfg["telemetry"].update(saved["telemetry"])
    cfg["desktop_notify"] = bool(saved.get("desktop_notify", True))
    geo = saved.get("geometry")
    if isinstance(geo, list) and len(geo) == 2 and all(isinstance(n, int) for n in geo):
        cfg["geometry"] = geo
    cfg["power_adapt"] = bool(saved.get("power_adapt", False))
    if saved.get("power_ac_preset") in PL_PRESETS:
        cfg["power_ac_preset"] = saved["power_ac_preset"]
    if saved.get("power_batt_preset") in PL_PRESETS:
        cfg["power_batt_preset"] = saved["power_batt_preset"]
    return cfg


def save_config(cfg: dict):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---- start-at-login (XDG autostart .desktop; inherits full GUI session env) --- #
def autostart_enabled() -> bool:
    return os.path.exists(AUTOSTART_PATH)


def set_autostart(enable: bool):
    if enable:
        os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
        with open(AUTOSTART_PATH, "w") as f:
            f.write(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Fan Control\n"
                f"Exec=python3 {APP_PATH}\n"
                "Icon=sensors-fan-symbolic\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
    else:
        try:
            os.remove(AUTOSTART_PATH)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Hardware access
# --------------------------------------------------------------------------- #
_TP_HWMON = None
for _p in sorted(glob.glob("/sys/class/hwmon/*/name")):
    with open(_p) as _f:
        if _f.read().strip() == "thinkpad":
            _TP_HWMON = _p.rsplit("/", 1)[0]
            break


def read_fan() -> dict:
    info = {"fan1": 0, "fan2": 0, "level": "unknown"}
    try:
        with open(FAN_PATH) as f:
            for line in f:
                if line.startswith("level:"):
                    info["level"] = line.split(":")[1].strip()
    except (OSError, ValueError):
        pass
    if _TP_HWMON:
        for key, fname in [("fan1", "fan1_input"), ("fan2", "fan2_input")]:
            try:
                with open(f"{_TP_HWMON}/{fname}") as f:
                    info[key] = int(f.read().strip())
            except (OSError, ValueError):
                pass
    return info


def read_temps() -> dict:
    temps = {"cpu": 0, "gpu": 0}
    try:
        for path in glob.glob("/sys/class/hwmon/*/name"):
            with open(path) as f:
                if f.read().strip() == "coretemp":
                    base = path.rsplit("/", 1)[0]
                    with open(f"{base}/temp1_input") as f2:
                        temps["cpu"] = int(f2.read().strip()) // 1000
                    break
    except (OSError, ValueError):
        pass
    if _TP_HWMON:
        try:
            with open(f"{_TP_HWMON}/temp2_input") as f:
                temps["gpu"] = int(f.read().strip()) // 1000
        except (OSError, ValueError):
            pass
    return temps


def read_on_ac() -> bool:
    """True if running on AC/mains. Defaults to True when undetectable."""
    for path in glob.glob("/sys/class/power_supply/*/type"):
        try:
            with open(path) as f:
                if f.read().strip() == "Mains":
                    base = path.rsplit("/", 1)[0]
                    with open(f"{base}/online") as f2:
                        return f2.read().strip() == "1"
        except OSError:
            continue
    return True


def list_comms() -> set:
    names = set()
    for p in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(p) as f:
                names.add(f.read().strip())
        except OSError:
            continue
    return names


def detect_workload(gpu_temp: int) -> str:
    """Map current system activity to the most fitting curve preset."""
    if not read_on_ac():
        return "Silent"                       # on battery: prioritise quiet/runtime
    comms = list_comms()
    try:
        load1 = float(open("/proc/loadavg").read().split()[0])
    except (OSError, ValueError):
        load1 = 0.0
    busy = load1 > (os.cpu_count() or 4) * 0.6
    if (comms & GAME_PROCS) or gpu_temp >= 72:
        return "Gaming"
    if (comms & AI_PROCS) and busy:
        return "AI / Compute"
    if busy:
        return "Performance"
    return "Balanced"


def setup_needed() -> str:
    if not os.path.exists(FANCTL):
        return "The fan control helper is not installed."
    try:
        with open(FAN_CONTROL_PARAM) as f:
            if f.read().strip() != "Y":
                return "Kernel fan control is disabled (thinkpad_acpi)."
    except OSError:
        return "thinkpad_acpi module not loaded."
    return ""


def run_setup() -> tuple[bool, str]:
    try:
        proc = subprocess.run(["pkexec", "bash", SETUP_SCRIPT], capture_output=True)
    except FileNotFoundError:
        return False, "pkexec not found — run: sudo bash setup-fan-control.sh"
    if proc.returncode == 0:
        return True, ""
    if proc.returncode == 126:
        return False, "Setup cancelled."
    return False, (proc.stderr or b"").decode(errors="replace").strip() or "Setup failed."


def set_fan(level: str):
    subprocess.run(
        ["sudo", "-n", FANCTL, level],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
    )


def fanctl_cmd(*args):
    """Run a privileged fanctl subcommand (raises CalledProcessError on failure)."""
    subprocess.run(
        ["sudo", "-n", FANCTL, *[str(a) for a in args]],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
    )


# ---- sysfs read helpers ------------------------------------------------- #
def _read_int(path, div=1):
    try:
        with open(path) as f:
            return int(f.read().strip()) // div
    except (OSError, ValueError):
        return None


def _read_str(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def has_dgpu() -> bool:
    """True if a non-Intel discrete GPU is present (vendor != 0x8086)."""
    for v in glob.glob("/sys/class/drm/card[0-9]/device/vendor"):
        if (_read_str(v) or "").lower() not in ("0x8086", ""):
            return True
    return False


def read_nvme_temp() -> int:
    best = 0
    for name in glob.glob("/sys/class/hwmon/*/name"):
        if _read_str(name) != "nvme":
            continue
        base = name.rsplit("/", 1)[0]
        v = _read_int(f"{base}/temp1_input", 1000)
        if v:
            best = max(best, v)
    return best


def read_all_sensors() -> dict:
    """Grouped temperature readout for the Sensors tab: {section: [(label, °C)]}."""
    out = {"CPU": [], "Storage": [], "System": []}
    for name_path in sorted(glob.glob("/sys/class/hwmon/*/name")):
        chip = _read_str(name_path)
        if not chip:
            continue
        base = name_path.rsplit("/", 1)[0]
        for tin in sorted(glob.glob(f"{base}/temp*_input")):
            v = _read_int(tin, 1000)
            if v is None or v <= 0 or v >= 200:
                continue
            lbl = _read_str(tin.replace("_input", "_label")) or \
                os.path.basename(tin).replace("_input", "")
            if chip == "coretemp":
                out["CPU"].append((lbl, v))
            elif chip == "nvme":
                out["Storage"].append((f"NVMe ({lbl})", v))
            elif chip == "iwlwifi":
                out["System"].append(("WiFi", v))
            elif chip == "acpitz":
                out["System"].append(("ACPI zone", v))
            elif chip == "thinkpad":
                out["System"].append((f"ThinkPad {lbl}", v))
    return out


def read_battery() -> dict:
    b = {
        "stop": _read_int(f"{BAT_PATH}/charge_control_end_threshold"),
        "start": _read_int(f"{BAT_PATH}/charge_control_start_threshold"),
        "capacity": _read_int(f"{BAT_PATH}/capacity"),
        "status": _read_str(f"{BAT_PATH}/status") or "?",
    }
    full = _read_int(f"{BAT_PATH}/energy_full")
    design = _read_int(f"{BAT_PATH}/energy_full_design")
    unit = "Wh"
    if not (full and design):                       # some report charge (µAh) not energy
        full = _read_int(f"{BAT_PATH}/charge_full")
        design = _read_int(f"{BAT_PATH}/charge_full_design")
        unit = "Ah"
    if full and design:
        b["health"] = round(full / design * 100)
        b["full"] = round(full / 1_000_000, 1)
        b["design"] = round(design / 1_000_000, 1)
        b["unit"] = unit

    # instantaneous power draw + time estimate (all normalised to µW / µWh)
    volt = _read_int(f"{BAT_PATH}/voltage_now")            # µV
    power = _read_int(f"{BAT_PATH}/power_now")             # µW (may be absent)
    if power is None:
        cur = _read_int(f"{BAT_PATH}/current_now")        # µA
        if cur is not None and volt:
            power = cur * volt // 1_000_000
    energy_now = _read_int(f"{BAT_PATH}/energy_now")
    if energy_now is None:
        ch = _read_int(f"{BAT_PATH}/charge_now")
        if ch is not None and volt:
            energy_now = ch * volt // 1_000_000
    energy_full = _read_int(f"{BAT_PATH}/energy_full")
    if energy_full is None:
        cf = _read_int(f"{BAT_PATH}/charge_full")
        if cf is not None and volt:
            energy_full = cf * volt // 1_000_000

    b["draw_w"] = round(power / 1_000_000, 1) if power else None
    b["time"] = None
    if power and power > 0:
        if b["status"] == "Discharging" and energy_now:
            hrs = energy_now / power
            b["time"] = f"{int(hrs)}h {round((hrs % 1) * 60):02d}m remaining"
        elif b["status"] == "Charging" and energy_now and energy_full and energy_full > energy_now:
            hrs = (energy_full - energy_now) / power
            b["time"] = f"{int(hrs)}h {round((hrs % 1) * 60):02d}m to full"
    return b


def read_cpu_power() -> dict:
    d = {
        "pl1": (_read_int(f"{RAPL_PATH}/constraint_0_power_limit_uw") or 0) // 1_000_000,
        "pl2": (_read_int(f"{RAPL_PATH}/constraint_1_power_limit_uw") or 0) // 1_000_000,
        "governor": _read_str("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "epp": _read_str("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
    }
    freqs = [v for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")
             if (v := _read_int(p, 1000)) is not None]
    d["freq_max"] = max(freqs) if freqs else None
    d["freq_avg"] = round(sum(freqs) / len(freqs)) if freqs else None
    nt = _read_int("/sys/devices/system/cpu/intel_pstate/no_turbo")
    d["turbo"] = ("on" if nt == 0 else "off") if nt is not None else "?"
    return d


def read_throttle_count() -> int:
    """Sum of per-core thermal-throttle events; a rising total = throttling now."""
    total = 0
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/thermal_throttle/core_throttle_count"):
        v = _read_int(p)
        if v is not None:
            total += v
    return total


# ---- power-profiles-daemon (no root needed; polkit allows active user) --- #
def read_ppd() -> str:
    try:
        return subprocess.run(["powerprofilesctl", "get"], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except Exception:
        return ""


def list_ppd() -> list:
    names = []
    try:
        out = subprocess.run(["powerprofilesctl", "list"], capture_output=True,
                             text=True, timeout=2).stdout
        for ln in out.splitlines():
            s = ln.strip()
            if s.endswith(":"):
                names.append(s[:-1].replace("* ", "").strip())
    except Exception:
        pass
    return names


def set_ppd(profile: str):
    subprocess.run(["powerprofilesctl", "set", profile], check=True,
                   capture_output=True, text=True, timeout=5)


def curve_index(temp: int, steps: list, current_idx: int, hyst: int) -> int:
    """Active curve step for `temp`, with hysteresis to stop flapping."""
    idx = max(0, min(current_idx, len(steps) - 1))
    while idx + 1 < len(steps) and temp >= steps[idx + 1]["temp"]:
        idx += 1
    while idx > 0 and temp < steps[idx]["temp"] - hyst:
        idx -= 1
    return idx


def level_to_num(level: str) -> int:
    if level in ("full-speed", "disengaged"):
        return 8
    if level == "auto":
        return -1
    try:
        return int(level)
    except ValueError:
        return -1


def temp_color(t: int) -> str:
    if t >= 85:
        return "#e74c3c"
    if t >= 70:
        return "#f39c12"
    return "#2ecc71"


# --------------------------------------------------------------------------- #
# Telemetry (fire-and-forget, never blocks the UI)
# --------------------------------------------------------------------------- #
def _post(url: str, data: bytes, headers: dict, timeout: float = 3.0):
    def _worker():
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=timeout).read()
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def push_metrics(tel: dict, temps: dict, info: dict, level: str, mode: str):
    url = tel.get("vm_url", "").strip()
    if not url:
        return
    host = os.uname().nodename
    body = (
        f'fan_cpu_temp_celsius{{host="{host}"}} {temps["cpu"]}\n'
        f'fan_gpu_temp_celsius{{host="{host}"}} {temps["gpu"]}\n'
        f'fan1_rpm{{host="{host}"}} {info["fan1"]}\n'
        f'fan2_rpm{{host="{host}"}} {info["fan2"]}\n'
        f'fan_level{{host="{host}",mode="{mode}"}} {level_to_num(level)}\n'
    ).encode()
    _post(url, body, {"Content-Type": "text/plain"})


def send_ntfy(tel: dict, title: str, message: str, priority: str = "urgent", tags: str = "fire"):
    base = tel.get("ntfy_url", "").strip()
    topic = tel.get("ntfy_topic", "").strip()
    if not base or not topic:
        return
    _post(f"{base.rstrip('/')}/{topic}", message.encode(),
          {"Title": title, "Priority": priority, "Tags": tags})


# --------------------------------------------------------------------------- #
# Live graph
# --------------------------------------------------------------------------- #
class Sparkline(QWidget):
    SPAN = 120

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cpu = deque(maxlen=self.SPAN)
        self.gpu = deque(maxlen=self.SPAN)
        self.rpm = deque(maxlen=self.SPAN)

    def add(self, cpu, gpu, rpm):
        self.cpu.append(cpu)
        self.gpu.append(gpu)
        self.rpm.append(rpm)
        self.update()

    def _poly(self, p, series, lo, hi, color, width=2):
        if len(series) < 2:
            return
        w, h = self.width(), self.height()
        span = max(1, hi - lo)
        pen = QPen(QColor(color))
        pen.setWidth(width)
        p.setPen(pen)
        pts = []
        for i, v in enumerate(series):
            x = w * i / (self.SPAN - 1)
            y = h - (max(lo, min(hi, v)) - lo) / span * (h - 4) - 2
            pts.append((x, y))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1b1b1b"))
        p.setPen(QPen(QColor("#333"), 1))
        for t in (50, 80):
            y = self.height() - (t - 20) / 80 * (self.height() - 4) - 2
            p.drawLine(0, int(y), self.width(), int(y))
        self._poly(p, self.rpm, 0, MAX_RPM, "#3b6ea5", 1)
        self._poly(p, self.gpu, 20, 100, "#f39c12", 2)
        self._poly(p, self.cpu, 20, 100, "#e74c3c", 2)
        p.end()


# --------------------------------------------------------------------------- #
# Curve editor
# --------------------------------------------------------------------------- #
class CurveDialog(QDialog):
    def __init__(self, curves: dict, hysteresis: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Fan Curve")
        self.setMinimumWidth(360)
        self._data = {
            "ac": sorted(curves["ac"], key=lambda s: s["temp"]),
            "battery": sorted(curves["battery"], key=lambda s: s["temp"]),
        }
        self._ctx = "ac"

        layout = QVBoxLayout(self)
        info = QLabel(
            "Fan steps up to a level when max(CPU, GPU) reaches its temperature.\n"
            "Hysteresis is how far it must cool before stepping back down."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888;")
        layout.addWidget(info)

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel("Editing curve:"))
        self.ctx_combo = QComboBox()
        self.ctx_combo.addItems(["AC (plugged in)", "Battery"])
        self.ctx_combo.currentIndexChanged.connect(self._switch_ctx)
        ctx_row.addWidget(self.ctx_combo)
        ctx_row.addStretch()
        layout.addLayout(ctx_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Temp °C", "Fan Level"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        for text, fn in [("Add", lambda: self._add_row(75, "4")),
                         ("Remove", self._remove_row), ("Reset", self._reset)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        hyst_row = QHBoxLayout()
        hyst_row.addWidget(QLabel("Hysteresis °C:"))
        self.hyst_spin = QSpinBox()
        self.hyst_spin.setRange(0, 30)
        self.hyst_spin.setValue(hysteresis)
        hyst_row.addWidget(self.hyst_spin)
        hyst_row.addStretch()
        layout.addLayout(hyst_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_table(self._data["ac"])

    def _add_row(self, temp, level):
        r = self.table.rowCount()
        self.table.insertRow(r)
        spin = QSpinBox()
        spin.setRange(0, 105)
        spin.setValue(int(temp))
        self.table.setCellWidget(r, 0, spin)
        combo = QComboBox()
        combo.addItems(CURVE_LEVELS)
        if str(level) in CURVE_LEVELS:
            combo.setCurrentText(str(level))
        self.table.setCellWidget(r, 1, combo)

    def _remove_row(self):
        r = self.table.currentRow()
        if r >= 0 and self.table.rowCount() > 1:
            self.table.removeRow(r)

    def _reset(self):
        self._load_table(DEFAULT_CURVE)
        self.hyst_spin.setValue(DEFAULT_HYSTERESIS)

    def _load_table(self, steps):
        self.table.setRowCount(0)
        for s in sorted(steps, key=lambda x: x["temp"]):
            self._add_row(s["temp"], s["level"])

    def _read_table(self) -> list:
        steps = []
        for r in range(self.table.rowCount()):
            steps.append({"temp": self.table.cellWidget(r, 0).value(),
                          "level": self.table.cellWidget(r, 1).currentText()})
        steps.sort(key=lambda s: s["temp"])
        if not steps or steps[0]["temp"] != 0:
            steps.insert(0, {"temp": 0, "level": "0"})
        return steps

    def _switch_ctx(self, idx):
        self._data[self._ctx] = self._read_table()
        self._ctx = "ac" if idx == 0 else "battery"
        self._load_table(self._data[self._ctx])

    def _on_accept(self):
        self._data[self._ctx] = self._read_table()
        self.accept()

    def result(self) -> tuple[dict, int]:
        return self._data, self.hyst_spin.value()


# --------------------------------------------------------------------------- #
# Built-in preset benchmark (worker thread + polled UI)
# --------------------------------------------------------------------------- #
class BenchDialog(QDialog):
    """Sustained-load benchmark of the PL presets. Runs in a background thread
    so the fan loop keeps running; results are polled into the table."""
    PRESETS = ["Balanced", "Performance", "Max"]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Compare power presets")
        self.setMinimumWidth(440)
        self._results = []          # worker appends dicts; UI polls
        self._status = "Ready."
        self._done = False
        self._orig = None

        v = QVBoxLayout(self)
        info = QLabel(
            "Runs a sustained all-core load at Balanced / Performance / Max and "
            f"measures steady clock, peak temp and throughput (~{len(self.PRESETS) * (BENCH_LOAD_SECS + 3)}s "
            "total). The CPU runs hot and loud during the test; your power limit "
            "is restored afterward."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#888;")
        v.addWidget(info)
        self.status = QLabel(self._status)
        self.status.setFont(QFont("monospace", 9))
        v.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Preset", "Throughput", "Clock", "Peak"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        v.addWidget(self.table)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        row.addWidget(self.start_btn)
        row.addStretch()
        row.addWidget(self.close_btn)
        v.addLayout(row)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll)

    def _start(self):
        if not any(os.access(os.path.join(p, "stress-ng"), os.X_OK)
                   for p in os.environ.get("PATH", "").split(":")):
            QMessageBox.warning(self, "Benchmark", "stress-ng is not installed.")
            return
        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._results = []
        self._done = False
        self.parent()._bench_running = True
        threading.Thread(target=self._worker, daemon=True).start()
        self.poll.start(500)

    def _worker(self):
        try:
            self._orig = read_cpu_power()
            for name in self.PRESETS:
                pl1, pl2 = PL_PRESETS[name]
                self._status = f"Setting {name} ({pl1}/{pl2} W)…"
                try:
                    fanctl_cmd("pl1", pl1)
                    fanctl_cmd("pl2", pl2)
                except Exception:
                    self._status = "Could not set power limit (helper missing?). Aborted."
                    return
                time.sleep(2)
                self._status = f"{name}: sustained load {BENCH_LOAD_SECS}s…"
                proc = subprocess.Popen(
                    ["stress-ng", "--cpu", "0", "--cpu-method", "matrixprod",
                     "-t", f"{BENCH_LOAD_SECS}s", "--metrics-brief"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                time.sleep(max(1, BENCH_LOAD_SECS - BENCH_SAMPLE_SECS))
                fsum = fn = tmax = 0
                for _ in range(BENCH_SAMPLE_SECS):
                    cp = read_cpu_power()
                    tmax = max(tmax, read_temps()["cpu"])
                    if cp["freq_avg"]:
                        fsum += cp["freq_avg"]
                        fn += 1
                    time.sleep(1)
                try:
                    out = proc.communicate(timeout=20)[0] or ""
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out = ""
                bogo = 0.0
                for ln in out.splitlines():
                    if " cpu " in ln:
                        parts = ln.split()
                        try:
                            bogo = float(parts[8])   # bogo-ops/s (real time)
                        except (IndexError, ValueError):
                            pass
                        break
                self._results.append({"name": name, "bogo": bogo,
                                      "mhz": (fsum // fn if fn else 0), "temp": tmax})
            self._status = "Done — power limit restored."
        except Exception as e:                       # noqa: BLE001 - report to UI
            self._status = f"Error: {e}"
        finally:
            if self._orig:
                try:
                    fanctl_cmd("pl1", self._orig["pl1"])
                    fanctl_cmd("pl2", self._orig["pl2"])
                except Exception:
                    pass
            self._done = True

    def _poll(self):
        self.status.setText(self._status)
        while self.table.rowCount() < len(self._results):
            r = self._results[self.table.rowCount()]
            i = self.table.rowCount()
            self.table.insertRow(i)
            cells = [r["name"], f'{r["bogo"]:.0f} ops/s',
                     f'{r["mhz"]} MHz', f'{r["temp"]}°C']
            for c, val in enumerate(cells):
                self.table.setItem(i, c, QTableWidgetItem(val))
        if self._done:
            self.poll.stop()
            self.parent()._bench_running = False
            self.start_btn.setEnabled(True)
            self.close_btn.setEnabled(True)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class FanControl(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fan Control")
        self.setMinimumSize(360, 420)

        cfg = load_config()
        self.curves = cfg["curves"]
        self.hysteresis = cfg["hysteresis"]
        self.power_auto = cfg["power_auto"]
        self.auto_profile = cfg["auto_profile"]
        self.preset_name = cfg["preset"]
        self.target_temp = cfg["target_temp"]
        self.telemetry = cfg["telemetry"]
        self.mode = cfg["mode"]              # "manual" | "curve" | "target"
        self._auto_candidate = None
        self._auto_count = 0
        self._auto_tick = 0
        self._auto_applying = False
        self._curve_idx = 0
        self._target_idx = CURVE_LEVELS.index("2")
        self._target_tick = 0
        self._applied_level = None
        self._desired_level = None
        self._reassert = REASSERT_TICKS
        self._push = 0
        self._alert_secs = 0
        self._alerted = False
        self._error = ""
        self.desktop_notify = cfg["desktop_notify"]
        self._notified_crit = False
        self._stats = {"cpu_max": 0, "cpu_min": 999, "rpm_max": 0, "secs": 0,
                       "level_secs": {}}
        self._geometry = cfg.get("geometry")
        self.power_adapt = cfg["power_adapt"]
        self.power_ac_preset = cfg["power_ac_preset"]
        self.power_batt_preset = cfg["power_batt_preset"]
        self._last_ac = None                    # track AC transitions for auto-adapt
        self._throttle_prev = read_throttle_count()
        self._throttling = False
        self._fan_warn = ""
        self._fan_low_ticks = 0
        self._history = deque(maxlen=HISTORY_MAX)   # (t, cpu, 2nd, rpm, level, pl1)
        self._max_rpm = MAX_RPM                 # auto-calibrated from observed peak
        self._bench_running = False

        self.has_dgpu = has_dgpu()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        header = QLabel("ThinkPad Fan Control")
        header.setFont(QFont("sans-serif", 15, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("color: #ffffff; letter-spacing: 0.5px;")
        root.addWidget(header)
        subtitle = QLabel(f"{os.uname().nodename} · v{VERSION}")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # ===================== Fan tab (existing UI) ===================== #
        fan_tab = QWidget()
        layout = QVBoxLayout(fan_tab)
        layout.setSpacing(10)
        layout.setContentsMargins(4, 8, 4, 4)
        self.tabs.addTab(self._scrollable(fan_tab), "Fan")

        # -- Monitoring --
        mon_group = QGroupBox("Monitoring")
        mon_layout = QVBoxLayout(mon_group)
        mon_layout.setSpacing(6)
        grid = QGridLayout()
        grid.setSpacing(4)
        fan2_label = "Fan 2 (GPU)" if self.has_dgpu else "Fan 2 (System)"
        for row, (text, attr) in enumerate([("Fan 1 (CPU)", "bar1"), (fan2_label, "bar2")]):
            lbl = QLabel(text)
            lbl.setFont(QFont("sans-serif", 9))
            grid.addWidget(lbl, row, 0)
            bar = QProgressBar()
            bar.setRange(0, MAX_RPM)
            bar.setTextVisible(True)
            bar.setFormat("%v RPM")
            grid.addWidget(bar, row, 1)
            setattr(self, attr, bar)
        mon_layout.addLayout(grid)

        self.temp_label = QLabel()
        self.temp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.temp_label.setFont(QFont("monospace", 11))
        self.temp_label.setTextFormat(Qt.TextFormat.RichText)
        mon_layout.addWidget(self.temp_label)

        self.graph = Sparkline()
        mon_layout.addWidget(self.graph)

        self.level_label = QLabel("Level: --")
        self.level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.level_label.setFont(QFont("monospace", 10))
        mon_layout.addWidget(self.level_label)
        layout.addWidget(mon_group)

        # -- Controls --
        ctrl_group = QGroupBox("Fan Level")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(8)

        self.group = QButtonGroup(self)
        levels = [("Auto", "auto"), ("Low (2)", "2"), ("Medium (4)", "4"),
                  ("High (6)", "6"), ("Max (7)", "7"), ("Full Speed", "full-speed")]
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for i, (label, value) in enumerate(levels):
            rb = QRadioButton(label)
            rb.setProperty("fan_level", value)
            rb.toggled.connect(self._on_level_changed)
            self.group.addButton(rb)
            (row1 if i < 3 else row2).addWidget(rb)
        ctrl_layout.addLayout(row1)
        ctrl_layout.addLayout(row2)

        # Smart Curve + preset selector + editor
        curve_row = QHBoxLayout()
        self.curve_rb = QRadioButton("Smart Curve")
        self.curve_rb.setProperty("fan_level", "curve")
        self.curve_rb.toggled.connect(self._on_level_changed)
        self.group.addButton(self.curve_rb)
        curve_row.addWidget(self.curve_rb)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(PRESET_NAMES)
        self.preset_combo.setCurrentText(self.preset_name)
        self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
        curve_row.addWidget(self.preset_combo, 1)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self._edit_curve)
        curve_row.addWidget(edit_btn)
        ctrl_layout.addLayout(curve_row)

        # Target temperature (thermostat) mode
        target_row = QHBoxLayout()
        self.target_rb = QRadioButton("Target Temp")
        self.target_rb.setProperty("fan_level", "target")
        self.target_rb.toggled.connect(self._on_level_changed)
        self.group.addButton(self.target_rb)
        target_row.addWidget(self.target_rb)
        self.target_spin = QSpinBox()
        self.target_spin.setRange(60, 90)
        self.target_spin.setSuffix(" °C")
        self.target_spin.setValue(self.target_temp)
        self.target_spin.valueChanged.connect(self._on_target_changed)
        target_row.addWidget(self.target_spin)
        target_row.addStretch()
        ctrl_layout.addLayout(target_row)

        self.auto_cb = QCheckBox("Auto-profile (switch preset by workload)")
        self.auto_cb.setToolTip("Detects games / compiles / idle and switches the curve preset automatically.")
        self.auto_cb.setChecked(self.auto_profile)
        self.auto_cb.toggled.connect(self._on_auto_profile)
        ctrl_layout.addWidget(self.auto_cb)

        self.power_cb = QCheckBox("Adapt curve to AC / battery")
        self.power_cb.setToolTip("Use the separate Battery curve when unplugged (quieter on the go).")
        self.power_cb.setChecked(self.power_auto)
        self.power_cb.toggled.connect(self._on_power_auto)
        ctrl_layout.addWidget(self.power_cb)

        self.autostart_cb = QCheckBox("Start at login")
        self.autostart_cb.setToolTip("Auto-launch this app (and your fan curve) at every login.")
        self.autostart_cb.setChecked(autostart_enabled())
        self.autostart_cb.toggled.connect(self._on_autostart)
        ctrl_layout.addWidget(self.autostart_cb)

        self.notify_cb = QCheckBox("Desktop notification on thermal alerts")
        self.notify_cb.setToolTip("Pop a native notification when the CPU reaches a critical temperature.")
        self.notify_cb.setChecked(self.desktop_notify)
        self.notify_cb.toggled.connect(self._on_notify)
        ctrl_layout.addWidget(self.notify_cb)
        layout.addWidget(ctrl_group)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setFont(QFont("sans-serif", 8))
        self.info_label.setStyleSheet("color: #888;")
        layout.addWidget(self.info_label)
        layout.addStretch()

        # ================ Sensors / Battery / Power tabs ================ #
        self.tabs.addTab(self._scrollable(self._build_sensors_tab()), "Sensors")
        self.tabs.addTab(self._scrollable(self._build_battery_tab()), "Battery")
        self.tabs.addTab(self._scrollable(self._build_power_tab()), "Power")
        self.tabs.currentChanged.connect(lambda _i: self._refresh_aux())

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            self.tray.setIcon(QIcon.fromTheme("sensors-fan-symbolic", QIcon.fromTheme("preferences-system")))
            self.tray.setToolTip("Fan Control")
            self.tray.activated.connect(self._tray_activated)
            self._build_tray_menu()
            self.tray.show()
        else:
            self.tray = None

        if self.mode == "curve":
            self.curve_rb.setChecked(True)
        elif self.mode == "target":
            self.target_rb.setChecked(True)

        if self._geometry:
            self.resize(self._geometry[0], self._geometry[1])
        else:
            self.resize(400, 820)

        self.timer = QTimer()
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()
        QTimer.singleShot(0, self._check_setup)

    # ---- config ----
    def _cfg(self) -> dict:
        return {
            "mode": self.mode, "curves": self.curves, "hysteresis": self.hysteresis,
            "power_auto": self.power_auto, "auto_profile": self.auto_profile,
            "preset": self.preset_name, "target_temp": self.target_temp,
            "telemetry": self.telemetry,
            "desktop_notify": self.desktop_notify,
            "geometry": [self.width(), self.height()],
            "power_adapt": self.power_adapt,
            "power_ac_preset": self.power_ac_preset,
            "power_batt_preset": self.power_batt_preset,
        }

    def _save(self):
        save_config(self._cfg())

    def active_curve(self) -> list:
        if self.power_auto and not read_on_ac():
            return self.curves["battery"]
        return self.curves["ac"]

    # ---- tray ----
    def _build_tray_menu(self):
        menu = QMenu()
        menu.addAction("Show / Hide", self._toggle_window)
        menu.addSeparator()
        labels = [("Auto", "auto"), ("Low (2)", "2"), ("Medium (4)", "4"),
                  ("High (6)", "6"), ("Max (7)", "7"), ("Full Speed", "full-speed"),
                  ("Smart Curve", "curve"), ("Target Temp", "target")]
        self._level_actions = {}
        for text, value in labels:
            act = QAction(text, menu, checkable=True)
            act.triggered.connect(lambda _c, v=value: self._select(v))
            menu.addAction(act)
            self._level_actions[value] = act
        presets = menu.addMenu("Curve preset")
        for name in PRESETS:
            presets.addAction(name, lambda n=name: self._apply_preset(n))
        menu.addSeparator()
        menu.addAction("Edit Curve…", self._edit_curve)
        menu.addSeparator()
        menu.addAction("About", self._about)
        menu.addAction("Quit", QApplication.quit)
        menu.aboutToShow.connect(self._sync_tray_menu)
        self.tray.setContextMenu(menu)

    def _sync_tray_menu(self):
        current = {"curve": "curve", "target": "target"}.get(self.mode, self._desired_level)
        for value, act in self._level_actions.items():
            act.setChecked(value == current)

    def _select(self, value: str):
        target = {"curve": self.curve_rb, "target": self.target_rb}.get(value)
        if target is None:
            target = next((b for b in self.group.buttons()
                           if b.property("fan_level") == value), None)
        if target:
            target.setChecked(True)

    # ---- presets ----
    def _set_preset_combo(self, name: str):
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentText(name)
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, name: str):
        if not self._auto_applying:
            self._disable_auto()        # manual preset pick wins over auto-profile
        if name == "Custom":
            return
        self._apply_preset(name)

    def _apply_preset(self, name: str):
        self.curves["ac"] = [dict(s) for s in PRESETS[name]]
        self.curves["battery"] = [dict(s) for s in PRESETS[name]]
        self.preset_name = name
        self._set_preset_combo(name)
        self._curve_idx = 0
        self._applied_level = None
        self._save()
        self._select("curve")   # presets imply curve mode
        self._refresh()

    # ---- window ----
    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def _check_setup(self):
        reason = setup_needed()
        if not reason:
            return
        resp = QMessageBox.question(
            self, "Fan Control Setup",
            f"{reason}\n\nFan levels can't be changed until this is installed. "
            "Run the one-time setup now? (You'll be asked for your password.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if resp != QMessageBox.StandardButton.Yes:
            self._error = "ERROR: setup not installed"
            return
        ok, err = run_setup()
        if ok:
            self._error = ""
            QMessageBox.information(self, "Fan Control", "Setup complete — fan control is ready.")
        else:
            QMessageBox.warning(self, "Fan Control", f"Setup did not complete:\n{err}")

    # ---- writing ----
    def _write_level(self, level: str) -> bool:
        try:
            set_fan(level)
            self._applied_level = level
            self._error = ""
            return True
        except FileNotFoundError:
            self._error = "ERROR: run setup-fan-control.sh"
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or b"").decode(errors="replace").strip()
            self._error = f"ERROR: {msg or 'fan write failed'}"
        return False

    def _disable_auto(self):
        if self.auto_profile:
            self.auto_profile = False
            self.auto_cb.blockSignals(True)
            self.auto_cb.setChecked(False)
            self.auto_cb.blockSignals(False)
            self._save()

    def _on_auto_profile(self, checked):
        self.auto_profile = checked
        self._auto_candidate = None
        self._auto_count = 0
        self._auto_tick = 0   # detect immediately on next refresh
        self._save()
        self._refresh()

    def _on_level_changed(self, checked):
        if not checked:
            return
        btn = self.group.checkedButton()
        if not btn:
            return
        if not self._auto_applying:
            self._disable_auto()        # manual mode pick wins over auto-profile
        value = btn.property("fan_level")
        if value == "curve":
            self.mode = "curve"
            self._curve_idx = 0
            self._applied_level = None
            self._desired_level = None
            self._error = ""
        elif value == "target":
            self.mode = "target"
            self._target_idx = CURVE_LEVELS.index("2")
            self._target_tick = 0
            self._applied_level = None
            self._desired_level = None
            self._error = ""
        else:
            self.mode = "manual"
            self._desired_level = value
            self._reassert = REASSERT_TICKS
            self._write_level(value)
        self._save()
        self._refresh()

    def _on_power_auto(self, checked):
        self.power_auto = checked
        self._curve_idx = 0
        self._save()
        self._refresh()

    def _on_autostart(self, checked):
        set_autostart(checked)

    def _on_notify(self, checked):
        self.desktop_notify = checked
        self._notified_crit = False
        self._save()

    # ---- auto power by AC/battery ----
    def _on_power_adapt(self, checked):
        self.power_adapt = checked
        self._last_ac = None            # force re-apply on the next tick
        self._save()

    def _on_power_ac_preset(self, name):
        self.power_ac_preset = name
        self._last_ac = None
        self._save()

    def _on_power_batt_preset(self, name):
        self.power_batt_preset = name
        self._last_ac = None
        self._save()

    def _apply_power_state(self, on_ac):
        preset = self.power_ac_preset if on_ac else self.power_batt_preset
        pl1, pl2 = PL_PRESETS.get(preset, (65, 109))
        try:
            fanctl_cmd("pl1", pl1)
            fanctl_cmd("pl2", pl2)
        except Exception:
            pass                        # helper unavailable — don't spam dialogs
        try:
            set_ppd(PL_PPD.get(preset, "balanced"))
        except Exception:
            pass
        if hasattr(self, "power_info"):
            self._refresh_power()

    # ---- tray icon shows current CPU temp ----
    def _paint_tray_icon(self, temp):
        if getattr(self, "_tray_temp", None) == temp:
            return                      # repaint only when the number changes
        self._tray_temp = temp
        pm = QPixmap(48, 48)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(QFont("sans-serif", 22, QFont.Weight.Bold))
        p.setPen(QColor(temp_color(temp)))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, str(temp))
        p.end()
        self.tray.setIcon(QIcon(pm))

    # ---- CSV export of session history ----
    def _export_csv(self):
        if not self._history:
            QMessageBox.information(self, "Export", "No history collected yet.")
            return
        default = os.path.join(os.path.expanduser("~"),
                               f"fan-control-{time.strftime('%Y%m%d-%H%M%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export history CSV", default,
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["epoch", "cpu_c", "second_c", "fan_rpm", "fan_level", "pl1_w"])
                w.writerows(self._history)
            QMessageBox.information(self, "Export",
                                    f"Wrote {len(self._history)} rows to\n{path}")
        except OSError as e:
            QMessageBox.warning(self, "Export", f"Could not write file:\n{e}")

    def _run_benchmark(self):
        BenchDialog(self).exec()

    def _on_target_changed(self, value):
        self.target_temp = value
        self._save()

    def _edit_curve(self):
        dlg = CurveDialog(self.curves, self.hysteresis, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.curves, self.hysteresis = dlg.result()
            self.preset_name = "Custom"
            self._set_preset_combo("Custom")
            self._curve_idx = 0
            self._applied_level = None
            self._save()
            self._refresh()

    # ---- main loop ----
    def _refresh(self):
        info = read_fan()
        self.bar1.setValue(info["fan1"])
        self.bar2.setValue(info["fan2"])

        level = info["level"]
        if level == "disengaged":
            level = "full-speed"

        temps = read_temps()
        maxt = max(temps["cpu"], temps["gpu"])   # fan curve: CPU + dGPU (if present)
        cc = temp_color(temps["cpu"])
        if self.has_dgpu:
            second_lbl, second_val = "GPU", temps["gpu"]
        else:                                    # no dGPU: show SSD instead of a fake 0°C
            second_lbl, second_val = "SSD", read_nvme_temp()
        sc = temp_color(second_val)
        self.temp_label.setText(
            f'CPU: <span style="color:{cc}">{temps["cpu"]}°C</span>'
            f'&nbsp;&nbsp;&nbsp;&nbsp;'
            f'{second_lbl}: <span style="color:{sc}">{second_val}°C</span>'
        )
        rpm = max(info["fan1"], info["fan2"])
        self.graph.add(temps["cpu"], second_val, rpm)
        self._update_stats(temps["cpu"], rpm, level)

        # history for CSV export
        pl1_now = (_read_int(f"{RAPL_PATH}/constraint_0_power_limit_uw") or 0) // 1_000_000
        self._history.append((int(time.time()), temps["cpu"], second_val, rpm, level, pl1_now))

        # auto-calibrate the RPM bar scale from the observed peak (fans top out
        # well below the 8000 default, so bars would never fill otherwise)
        cal = max(int(self._stats["rpm_max"] * 1.15), 4000)
        if cal != self._max_rpm:
            self._max_rpm = cal
            self.bar1.setRange(0, cal)
            self.bar2.setRange(0, cal)

        # thermal-throttle detection (rising per-core throttle counter)
        tc = read_throttle_count()
        self._throttling = tc > self._throttle_prev
        self._throttle_prev = tc

        # fan health: high commanded level but ~no RPM for a while = failing fan
        if level_to_num(level) >= 4 and rpm < 800:
            self._fan_low_ticks += 1
        else:
            self._fan_low_ticks = 0
        self._fan_warn = ("Fan may be failing — no RPM at high level"
                          if self._fan_low_ticks >= 5 else "")

        # auto power-profile (PL + PPD) by AC/battery
        if self.power_adapt and not self._bench_running:
            ac_now = read_on_ac()
            if ac_now != self._last_ac:
                self._last_ac = ac_now
                self._apply_power_state(ac_now)

        if self.auto_profile:
            self._run_auto(temps)

        if self.mode == "curve":
            self._apply_curve(temps)
        elif self.mode == "target":
            self._apply_target(temps)
        self._reassert_held()

        # status line
        if self._error:
            self.level_label.setText(self._error)
        elif self.mode == "curve":
            src = "AC" if (not self.power_auto or read_on_ac()) else "BAT"
            tgt = self._applied_level if self._applied_level is not None else level
            prefix = "Auto · " if self.auto_profile else ""
            self.level_label.setText(f"{prefix}{self.preset_name} [{src}]: {maxt}°C → level {tgt}")
        elif self.mode == "target":
            tgt = self._applied_level if self._applied_level is not None else level
            self.level_label.setText(f"Target {self.target_temp}°C: {maxt}°C → level {tgt}")
        else:
            self.level_label.setText(f"Level: {level}")

        crit = temps["cpu"] >= 90
        if crit and self.desktop_notify and self.tray and not self._notified_crit:
            self._notified_crit = True
            self.tray.showMessage(
                "Fan Control — thermal alert",
                f'CPU at {temps["cpu"]}°C (critical) — fan {level}',
                QSystemTrayIcon.MessageIcon.Critical, 8000)
        if temps["cpu"] < 85:
            self._notified_crit = False   # re-arm once cooled
        # warning banner, most-severe first: failing fan > critical > throttle > hot
        if self._fan_warn:
            self.info_label.setText("⚠ " + self._fan_warn)
            self.info_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        elif crit:
            self.info_label.setText("CPU temperature critical!")
            self.info_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        elif self._throttling:
            self.info_label.setText("CPU throttling (thermal cap)")
            self.info_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
        elif temps["cpu"] >= 80:
            self.info_label.setText("CPU temperature elevated")
            self.info_label.setStyleSheet("color: #f39c12;")
        else:
            self.info_label.setText("")
            self.info_label.setStyleSheet("color: #888;")

        if self.tray:
            self.tray.setToolTip(
                f"Fan 1: {info['fan1']} RPM  Fan 2: {info['fan2']} RPM\n"
                f"CPU: {temps['cpu']}°C  {second_lbl}: {second_val}°C  [{level}, {self.mode}]"
            )
            self._paint_tray_icon(temps["cpu"])

        if not self._error and self.mode == "manual":
            for btn in self.group.buttons():
                if btn.property("fan_level") == level:
                    btn.blockSignals(True)
                    btn.setChecked(True)
                    btn.blockSignals(False)
                    break

        self._telemetry(temps, info, level, maxt)
        self._refresh_aux()

    def _run_auto(self, temps):
        """Detect workload every AUTO_INTERVAL and switch preset after debounce."""
        self._auto_tick -= 1
        if self._auto_tick > 0:
            return
        self._auto_tick = AUTO_INTERVAL
        cand = detect_workload(temps["gpu"])
        if cand == self.preset_name and self.mode == "curve":
            self._auto_candidate = None
            self._auto_count = 0
            return
        if cand == self._auto_candidate:
            self._auto_count += 1
        else:
            self._auto_candidate = cand
            self._auto_count = 1
        if self._auto_count >= AUTO_DEBOUNCE:
            self._auto_candidate = None
            self._auto_count = 0
            self._auto_applying = True
            try:
                self._apply_preset(cand)
            finally:
                self._auto_applying = False

    def _apply_curve(self, temps):
        maxt = max(temps["cpu"], temps["gpu"])
        steps = self.active_curve()
        self._curve_idx = curve_index(maxt, steps, self._curve_idx, self.hysteresis)
        target = steps[self._curve_idx]["level"]
        if maxt >= CRITICAL_TEMP:
            target = "full-speed"
        self._set_desired(target)

    def _apply_target(self, temps):
        """Thermostat: hold max temp near the target. Ramps up responsively
        (bigger jumps the further over target), eases down gently."""
        maxt = max(temps["cpu"], temps["gpu"])
        last = len(CURVE_LEVELS) - 1
        err = maxt - self.target_temp
        if maxt >= CRITICAL_TEMP:
            self._target_idx = last
        elif err > 8:                                  # far too hot: jump 2 levels
            self._target_idx = min(self._target_idx + 2, last)
        elif err > 2:                                  # a bit hot: step up
            self._target_idx = min(self._target_idx + 1, last)
        elif err < -6:                                 # comfortably cool: ease down (gated)
            self._target_tick -= 1
            if self._target_tick <= 0:
                self._target_tick = TARGET_INTERVAL
                self._target_idx = max(self._target_idx - 1, 0)
        else:                                          # within deadband: hold steady
            self._target_tick = TARGET_INTERVAL
        self._set_desired(CURVE_LEVELS[self._target_idx])

    def _set_desired(self, target):
        self._desired_level = target
        if target != self._applied_level:
            self._write_level(target)
            self._reassert = REASSERT_TICKS

    def _reassert_held(self):
        if not self._desired_level or self._desired_level == "auto":
            return
        self._reassert -= 1
        if self._reassert <= 0:
            self._reassert = REASSERT_TICKS
            self._write_level(self._desired_level)

    def _telemetry(self, temps, info, level, maxt):
        tel = self.telemetry
        if not tel.get("enabled"):
            return
        self._push -= 1
        if self._push <= 0:
            self._push = max(1, int(tel.get("push_interval", 15)))
            push_metrics(tel, temps, info, level, self.mode)
        if maxt >= int(tel.get("alert_temp", 92)):
            self._alert_secs += 1
            if self._alert_secs >= int(tel.get("alert_sustain", 30)) and not self._alerted:
                self._alerted = True
                send_ntfy(tel, f"{os.uname().nodename} fan/thermal alert",
                          f"max(CPU,GPU) at {maxt}°C — fan level {level}")
        else:
            self._alert_secs = 0
            self._alerted = False

    # ---- tab scrolling (content scrolls instead of clipping when window is small) ----
    def _scrollable(self, inner):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    # ---- Sensors tab ----
    def _build_sensors_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)
        intro = QLabel("Live temperatures from every sensor on the machine.")
        intro.setStyleSheet("color:#888;")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.sensors_label = QLabel("Reading…")
        self.sensors_label.setFont(QFont("monospace", 9))
        self.sensors_label.setTextFormat(Qt.TextFormat.RichText)
        self.sensors_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sensors_label.setStyleSheet(READOUT_CSS)
        v.addWidget(self.sensors_label)

        stats_box = QGroupBox("Session stats")
        sv = QVBoxLayout(stats_box)
        self.stats_label = QLabel("…")
        self.stats_label.setFont(QFont("monospace", 9))
        self.stats_label.setTextFormat(Qt.TextFormat.RichText)
        sv.addWidget(self.stats_label)
        reset_row = QHBoxLayout()
        reset_btn = QPushButton("Reset stats")
        reset_btn.clicked.connect(self._reset_stats)
        reset_row.addWidget(reset_btn)
        export_btn = QPushButton("Export CSV…")
        export_btn.setToolTip("Save the recorded temp / RPM / power history to a CSV file.")
        export_btn.clicked.connect(self._export_csv)
        reset_row.addWidget(export_btn)
        reset_row.addStretch()
        sv.addLayout(reset_row)
        v.addWidget(stats_box)
        v.addStretch()
        return w

    def _refresh_sensors(self):
        parts = []
        for section, rows in read_all_sensors().items():
            if not rows:
                continue
            parts.append(f"<b>{section}</b>")
            for lbl, val in rows:
                parts.append(f'&nbsp;&nbsp;{lbl}: '
                             f'<span style="color:{temp_color(val)}">{val}°C</span>')
            parts.append("")
        self.sensors_label.setText("<br>".join(parts) or "no sensors found")
        self.stats_label.setText(self._stats_text())

    def _update_stats(self, cpu, rpm, level):
        s = self._stats
        s["secs"] += 1
        if cpu > s["cpu_max"]:
            s["cpu_max"] = cpu
        if 0 < cpu < s["cpu_min"]:
            s["cpu_min"] = cpu
        if rpm > s["rpm_max"]:
            s["rpm_max"] = rpm
        s["level_secs"][level] = s["level_secs"].get(level, 0) + 1

    def _stats_text(self):
        s = self._stats
        cmin = s["cpu_min"] if s["cpu_min"] < 999 else 0
        top = sorted(s["level_secs"].items(), key=lambda x: -x[1])[:3]
        lv = ", ".join(f"{k}: {v}s" for k, v in top) or "—"
        return (f'Uptime: {s["secs"] // 60}m {s["secs"] % 60}s<br>'
                f'CPU peak / min: {s["cpu_max"]}°C / {cmin}°C<br>'
                f'Fan peak: {s["rpm_max"]} RPM<br>'
                f'Time at level: {lv}')

    def _reset_stats(self):
        self._stats = {"cpu_max": 0, "cpu_min": 999, "rpm_max": 0, "secs": 0,
                       "level_secs": {}}
        if hasattr(self, "stats_label"):
            self.stats_label.setText(self._stats_text())

    # ---- Battery tab ----
    def _build_battery_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)
        self.bat_info = QLabel("Reading…")
        self.bat_info.setFont(QFont("monospace", 10))
        self.bat_info.setTextFormat(Qt.TextFormat.RichText)
        self.bat_info.setStyleSheet(READOUT_CSS)
        v.addWidget(self.bat_info)

        box = QGroupBox("Charge limit (battery longevity)")
        bl = QVBoxLayout(box)
        note = QLabel(
            "Capping charge at 60–80% greatly slows battery wear. Use 100% only "
            "when you need full runtime (travel). Applies on the next charge cycle."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        bl.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("Stop charging at:"))
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(50, 100)
        self.charge_spin.setSuffix(" %")
        self.charge_spin.setValue(read_battery().get("stop") or 100)
        row.addWidget(self.charge_spin)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(lambda: self._apply_charge())
        row.addWidget(apply_btn)
        row.addStretch()
        bl.addLayout(row)

        qp = QHBoxLayout()
        qp.addWidget(QLabel("Quick:"))
        for pct in (60, 80, 100):
            b = QPushButton(f"{pct}%")
            b.clicked.connect(lambda _c, p=pct: self._apply_charge(p))
            qp.addWidget(b)
        qp.addStretch()
        bl.addLayout(qp)
        v.addWidget(box)
        v.addStretch()
        return w

    def _apply_charge(self, pct=None):
        if pct is not None:
            self.charge_spin.setValue(pct)
        try:
            fanctl_cmd("charge-stop", self.charge_spin.value())
            self._refresh_battery()
        except FileNotFoundError:
            self._helper_error()
        except subprocess.CalledProcessError as e:
            self._helper_error((e.stderr or b"").decode(errors="replace").strip())

    def _refresh_battery(self):
        b = read_battery()
        health = f'{b["health"]}%' if b.get("health") is not None else "?"
        lines = [
            f'Charge now: {b.get("capacity", "?")}%  ({b.get("status", "?")})',
            f'Charge limit: stop {b.get("stop", "?")}%  (start {b.get("start", "?")}%)',
            f'Health: {health}',
        ]
        if b.get("full") is not None:
            lines.append(f'Capacity: {b["full"]} / {b["design"]} {b.get("unit", "Wh")}')
        if b.get("draw_w") is not None:
            dl = f'Power draw: {b["draw_w"]} W'
            if b.get("time"):
                dl += f'  ·  {b["time"]}'
            lines.append(dl)
        self.bat_info.setText("<br>".join(lines))

    # ---- Power tab ----
    def _build_power_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)
        self.power_info = QLabel("Reading…")
        self.power_info.setFont(QFont("monospace", 10))
        self.power_info.setTextFormat(Qt.TextFormat.RichText)
        self.power_info.setStyleSheet(READOUT_CSS)
        v.addWidget(self.power_info)

        ppd_box = QGroupBox("Power profile")
        pl = QHBoxLayout(ppd_box)
        pl.addWidget(QLabel("Profile:"))
        self.ppd_combo = QComboBox()
        profs = list_ppd() or ["power-saver", "balanced", "performance"]
        self.ppd_combo.addItems(profs)
        cur = read_ppd()
        if cur in profs:
            self.ppd_combo.setCurrentText(cur)
        self.ppd_combo.currentTextChanged.connect(self._apply_ppd)
        pl.addWidget(self.ppd_combo)
        pl.addStretch()
        v.addWidget(ppd_box)

        rapl_box = QGroupBox("CPU power limits")
        rl = QGridLayout(rapl_box)
        note = QLabel(
            "PL1 = sustained watts (governs steady heat & fan noise). PL2 = short "
            "burst. Lower PL1 for a cooler, quieter laptop; raise for max sustained "
            "speed. Firmware may clamp — the readout above shows what actually applied."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        rl.addWidget(note, 0, 0, 1, 3)
        cp = read_cpu_power()
        rl.addWidget(QLabel("PL1 sustained:"), 1, 0)
        self.pl1_spin = QSpinBox()
        self.pl1_spin.setRange(15, 125)
        self.pl1_spin.setSuffix(" W")
        self.pl1_spin.setValue(cp["pl1"] or 45)
        rl.addWidget(self.pl1_spin, 1, 1)
        rl.addWidget(QLabel("PL2 burst:"), 2, 0)
        self.pl2_spin = QSpinBox()
        self.pl2_spin.setRange(15, 135)
        self.pl2_spin.setSuffix(" W")
        self.pl2_spin.setValue(cp["pl2"] or 65)
        rl.addWidget(self.pl2_spin, 2, 1)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(lambda: self._apply_rapl())
        rl.addWidget(apply_btn, 1, 2, 2, 1)

        pr = QGridLayout()
        pr.setSpacing(6)
        for i, name in enumerate(PL_PRESET_ORDER):
            a, bb = PL_PRESETS[name]
            btn = QPushButton(f"{name}\n{a}/{bb} W")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _c, x=a, y=bb: self._apply_rapl(x, y))
            pr.addWidget(btn, i // 2, i % 2)
        rl.addLayout(pr, 3, 0, 1, 3)
        v.addWidget(rapl_box)

        adapt_box = QGroupBox("Auto power by AC / battery")
        ab = QGridLayout(adapt_box)
        self.power_adapt_cb = QCheckBox("Adapt PL + profile automatically")
        self.power_adapt_cb.setToolTip("On unplug, switch to the battery preset; on AC, the AC preset.")
        self.power_adapt_cb.setChecked(self.power_adapt)
        self.power_adapt_cb.toggled.connect(self._on_power_adapt)
        ab.addWidget(self.power_adapt_cb, 0, 0, 1, 2)
        ab.addWidget(QLabel("On AC:"), 1, 0)
        self.ac_preset_combo = QComboBox()
        self.ac_preset_combo.addItems(PL_PRESET_ORDER)
        self.ac_preset_combo.setCurrentText(self.power_ac_preset)
        self.ac_preset_combo.currentTextChanged.connect(self._on_power_ac_preset)
        ab.addWidget(self.ac_preset_combo, 1, 1)
        ab.addWidget(QLabel("On battery:"), 2, 0)
        self.batt_preset_combo = QComboBox()
        self.batt_preset_combo.addItems(PL_PRESET_ORDER)
        self.batt_preset_combo.setCurrentText(self.power_batt_preset)
        self.batt_preset_combo.currentTextChanged.connect(self._on_power_batt_preset)
        ab.addWidget(self.batt_preset_combo, 2, 1)
        v.addWidget(adapt_box)

        bench_btn = QPushButton("Compare presets (benchmark)…")
        bench_btn.setToolTip("Run a sustained load at each preset and compare "
                             "throughput, clock and temperature.")
        bench_btn.clicked.connect(self._run_benchmark)
        v.addWidget(bench_btn)

        v.addStretch()
        return w

    def _apply_ppd(self, name):
        try:
            set_ppd(name)
        except Exception as e:
            msg = getattr(e, "stderr", "") or str(e)
            QMessageBox.warning(self, "Power profile", f"Could not set profile:\n{msg}")

    def _apply_rapl(self, pl1=None, pl2=None):
        if pl1 is not None:
            self.pl1_spin.setValue(pl1)
        if pl2 is not None:
            self.pl2_spin.setValue(pl2)
        a = self.pl1_spin.value()
        b = max(self.pl2_spin.value(), a)          # PL2 must be >= PL1
        self.pl2_spin.setValue(b)
        try:
            fanctl_cmd("pl1", a)
            fanctl_cmd("pl2", b)
            self._refresh_power()
        except FileNotFoundError:
            self._helper_error()
        except subprocess.CalledProcessError as e:
            self._helper_error((e.stderr or b"").decode(errors="replace").strip())

    def _refresh_power(self):
        p = read_cpu_power()
        self.power_info.setText("<br>".join([
            f'PL1 sustained: {p["pl1"]} W&nbsp;&nbsp;&nbsp;PL2 burst: {p["pl2"]} W',
            f'Governor: {p.get("governor", "?")}&nbsp;&nbsp;&nbsp;EPP: {p.get("epp", "?")}',
            f'Turbo: {p.get("turbo", "?")}&nbsp;&nbsp;&nbsp;'
            f'Freq: {p.get("freq_avg", "?")}–{p.get("freq_max", "?")} MHz',
        ]))
        cur = read_ppd()
        if cur:
            self.ppd_combo.blockSignals(True)
            self.ppd_combo.setCurrentText(cur)
            self.ppd_combo.blockSignals(False)

    def _helper_error(self, msg=""):
        QMessageBox.warning(
            self, "Fan Control",
            (msg or "Privileged helper unavailable.") +
            "\n\nIf you just updated the app, deploy the new helper once with:\n"
            "    pkexec bash ~/setup-fan-control.sh"
        )

    # ---- dispatch: refresh only the visible aux tab (cheap) ----
    def _refresh_aux(self):
        if not hasattr(self, "tabs"):
            return
        name = self.tabs.tabText(self.tabs.currentIndex())
        if name == "Sensors":
            self._refresh_sensors()
        elif name == "Battery":
            self._refresh_battery()
        elif name == "Power":
            self._refresh_power()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def _about(self):
        QMessageBox.about(
            self, "About Fan Control",
            f"<b>ThinkPad Fan Control</b> v{VERSION}<br><br>"
            "Fan curves, thermal monitoring, battery care and CPU power limits "
            "for ThinkPad laptops."
        )

    def closeEvent(self, event):
        self._save()                      # persist window size + settings
        if self.tray and self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(False)
    win = FanControl()
    win.show()
    sys.exit(app.exec())
