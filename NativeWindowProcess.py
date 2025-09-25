# chrome_control.py
"""
Module exposing two simple functions:
- activate_chrome_by_path(chrome_path: str) -> bool
- close_processes_gracefully(chrome_path: str) -> bool
"""
from typing import Iterable, Set
import os
import time,sys
import subprocess
import psutil
import ctypes
from ctypes import wintypes

import win32gui
import win32con
import win32process
import win32api
import win32com.client

# Windows APIs
user32 = ctypes.windll.user32
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL

dwmapi = ctypes.WinDLL("dwmapi")
DWMWA_CLOAKED = 14

def _is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                     ctypes.byref(cloaked),
                                     ctypes.sizeof(cloaked))
        return cloaked.value != 0
    except Exception:
        return False

def _normalize_path(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))

def find_chrome_root_pids(chrome_path: str):
    wanted = _normalize_path(chrome_path)
    procs = []
    for p in psutil.process_iter(attrs=["pid", "exe"]):
        try:
            exe = p.info.get("exe") or ""
            if _normalize_path(exe) == wanted:
                procs.append(psutil.Process(p.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs

def collect_descendant_pids(roots: Iterable[psutil.Process]) -> Set[int]:
    pids = set()
    for rp in roots:
        try:
            pids.add(rp.pid)
            for child in rp.children(recursive=True):
                pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids

def _visible_user_window(hwnd: int) -> bool:
    try:
        if not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.GetAncestor(hwnd, win32con.GA_ROOT) != hwnd:
            return False
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & win32con.WS_EX_TOOLWINDOW:
            return False
        if _is_window_cloaked(hwnd):
            return False
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] <= 1 or rect[3] - rect[1] <= 1:
            return False
        return True
    except Exception:
        return False

def enumerate_visible_windows_for_pids(pid_set: Iterable[int]):
    matches = []
    def enum_handler(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pid_set and _visible_user_window(hwnd):
                matches.append(hwnd)
        except Exception:
            pass
    win32gui.EnumWindows(enum_handler, None)
    return matches

# --- activation helpers (internal) ---

def _uia_activate(hwnd: int) -> bool:
    """Use uiautomation package as primary UIA method. Return True on success."""
    try:
        import uiautomation as auto
    except Exception:
        return False

    try:
        ctrl = auto.ControlFromHandle(hwnd)
        if not ctrl:
            return False
        try:
            ctrl.SetFocus()
            try:
                ctrl.SetActive()
            except Exception:
                pass
            return True
        except Exception:
            return False
    except Exception:
        return False

def _win32_activate(hwnd: int) -> bool:
    """Try win32 activation with several fallbacks. Return True on success."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd) if fg_hwnd else (0, 0)
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        current_tid = win32api.GetCurrentThreadId()

        attached_fg = False
        attached_target = False
        try:
            if fg_tid and fg_tid != current_tid:
                attached_fg = user32.AttachThreadInput(current_tid, fg_tid, True)
            if target_tid and target_tid != current_tid:
                attached_target = user32.AttachThreadInput(current_tid, target_tid, True)
        except Exception:
            pass

        ok = False
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
            ok = bool(win32gui.SetForegroundWindow(hwnd))
        except Exception:
            ok = False

        try:
            if attached_target:
                user32.AttachThreadInput(current_tid, target_tid, False)
            if attached_fg:
                user32.AttachThreadInput(current_tid, fg_tid, False)
        except Exception:
            pass

        if ok:
            return True

        # Fallback to WScript.Shell.AppActivate (title then PID)
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            title = win32gui.GetWindowText(hwnd) or None
            if title:
                try:
                    if shell.AppActivate(title):
                        return True
                except Exception:
                    pass
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                if shell.AppActivate(pid):
                    return True
            except Exception:
                pass
        except Exception:
            pass

        # ALT-key trick
        try:
            VK_MENU = 0x12
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_MENU, 0, 0, 0)
            time.sleep(0.01)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            try:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetActiveWindow(hwnd)
                ok2 = bool(win32gui.SetForegroundWindow(hwnd))
                if ok2:
                    return True
            except Exception:
                pass
        except Exception:
            pass

        # Temporary topmost trick
        try:
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            win32gui.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            time.sleep(0.05)
            win32gui.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            try:
                ok3 = bool(win32gui.SetForegroundWindow(hwnd))
                if ok3:
                    return True
            except Exception:
                pass
        except Exception:
            pass

        return False
    except Exception:
        return False

def _wait_for_new_window(chrome_path: str, baseline_pids, timeout_sec: float):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        roots = find_chrome_root_pids(chrome_path)
        pid_set = collect_descendant_pids(roots)
        wins = enumerate_visible_windows_for_pids(pid_set)
        if wins:
            return wins[0]
        time.sleep(0.15)
    return None

def _ensure_window_for_chrome(chrome_path: str):
    roots = find_chrome_root_pids(chrome_path)
    if not roots:
        # launch a new window
        try:
            subprocess.Popen([chrome_path, "--new-window"], close_fds=True)
        except Exception:
            pass
        return _wait_for_new_window(chrome_path, set(), 10.0)

    pid_set = collect_descendant_pids(roots)
    wins = enumerate_visible_windows_for_pids(pid_set)
    if wins:
        return wins[0]

    try:
        subprocess.Popen([chrome_path, "--new-window"], close_fds=True)
    except Exception:
        pass
    return _wait_for_new_window(chrome_path, pid_set, 10.0)

# -------------------------
# Public functions you asked for (path -> bool)
# -------------------------
def activate_chrome_by_path(chrome_path: str) -> bool:
    """
    Ensure a window exists for chrome_path and try to activate it.
    Returns True on success, False otherwise.
    """
    if not chrome_path or not os.path.isfile(chrome_path):
        return False

    hwnd = _ensure_window_for_chrome(chrome_path)
    if not hwnd:
        return False

    # Try UIA first
    if _uia_activate(hwnd):
        # confirm foreground; if it's foreground we're done
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return True
        except Exception:
            pass
        # else fall through to win32 fallback
    # fallback to win32 activation
    return _win32_activate(hwnd)

def close_processes_gracefully(chrome_path: str) -> bool:
    """
    Simplified close function with default timeout.
    Returns True if all target processes exited cleanly, False otherwise.
    """
    DEFAULT_TIMEOUT = 6.0
    roots = find_chrome_root_pids(chrome_path)
    if not roots:
        return True  # nothing to do

    pids = collect_descendant_pids(roots)

    wins = enumerate_visible_windows_for_pids(pids)
    if wins:
        for hwnd in wins:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    deadline = time.time() + DEFAULT_TIMEOUT
    while time.time() < deadline:
        alive = [pid for pid in pids if psutil.pid_exists(pid)]
        if not alive:
            return True
        time.sleep(0.2)

    # attempt terminate then kill
    alive = [pid for pid in pids if psutil.pid_exists(pid)]
    if alive:
        proc_objs = []
        for pid in alive:
            try:
                p = psutil.Process(pid)
                p.terminate()
                proc_objs.append(p)
            except Exception:
                pass

        gone, alive_procs = psutil.wait_procs(proc_objs, timeout=3)
        still_alive = [p.pid for p in alive_procs if p.is_running()]
        if still_alive:
            for pid in still_alive:
                try:
                    p = psutil.Process(pid)
                    p.kill()
                except Exception:
                    pass
            any_left = any(psutil.pid_exists(pid) for pid in pids)
            return not any_left
        else:
            return True
    else:
        return True



def kill_processes_by_path(exe_path: str) -> bool:
    """
    强制终止由指定路径启动的所有进程。
    输入:
        exe_path: str - 可执行文件的完整路径
    输出:
        bool - True 表示所有相关进程已终止，False 表示有残留或失败
    """
    if not os.path.isfile(exe_path):
        return False

    exe_path = os.path.abspath(exe_path)
    success = True
    target_pids = []

    # 找到所有路径匹配的进程
    for proc in psutil.process_iter(['pid', 'exe']):
        try:
            if proc.info['exe'] and os.path.abspath(proc.info['exe']) == exe_path:
                target_pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not target_pids:
        return True  # 没有找到进程，视为成功

    # 强制 kill
    for pid in target_pids:
        try:
            psutil.Process(pid).kill()
        except Exception:
            success = False

    # 再次检查是否还有残留
    for pid in target_pids:
        if psutil.pid_exists(pid):
            success = False

    return success


import signal

def kill_process_and_parent_by_port(port, force=False):
    """
    杀掉占用指定端口的进程及其父进程

    :param port: 端口号 (int)
    :param force: True 则使用 SIGKILL / terminate() 强制杀死
    """
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        print(f"找到进程 {proc.pid} ({proc.name()}) 占用端口 {port}")

                        # 杀掉当前进程
                        if force:
                            proc.kill()
                        else:
                            proc.send_signal(signal.SIGTERM)

                        # 杀掉父进程（如果存在且不是系统进程）
                        parent = proc.parent()
                        if parent and parent.pid != 0:
                            print(f"→ 同时杀掉父进程 {parent.pid} ({parent.name()})")
                            if force:
                                parent.kill()
                            else:
                                parent.send_signal(signal.SIGTERM)
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        print(f"❌ 出错: {e}")
    return False

if __name__ == "__main__":
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit,
        QLineEdit, QLabel, QHBoxLayout, QFileDialog, QMessageBox
    )
    from PyQt6.QtCore import QTimer

    class ChromeActivator(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Chrome Activator (pywin32 + ctypes AttachThreadInput)")

            self.log_view = QTextEdit(readOnly=True)
            self.path_edit = QLineEdit()
            self.path_edit.setPlaceholderText("Path to chrome.exe")

            # ← pre-fill the path you asked for
            default_path = r"F:\BaiduNetdiskDownload\SoftwareProject\EngkuDict\Chrome\chrome.exe"
            self.path_edit.setText(default_path)

            browse_btn = QPushButton("Browse…")
            browse_btn.clicked.connect(self.browse)

            run_btn = QPushButton("Find / Launch / Activate")
            run_btn.clicked.connect(self.run)

            close_btn = QPushButton("Close All from Path")
            close_btn.clicked.connect(self.close_all_from_path)

            top = QHBoxLayout()
            top.addWidget(QLabel("Chrome path:"))
            top.addWidget(self.path_edit)
            top.addWidget(browse_btn)

            layout = QVBoxLayout(self)
            layout.addLayout(top)
            btn_row = QHBoxLayout()
            btn_row.addWidget(run_btn)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)
            layout.addWidget(self.log_view)

            self.resize(800, 520)

        def log(self, msg):
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            self.log_view.append(f"[{ts}] {msg}")

        def browse(self):
            fname, _ = QFileDialog.getOpenFileName(self, "Select chrome.exe", "", "Executables (*.exe)")
            if fname:
                self.path_edit.setText(fname)

        def run(self):
            chrome_path = self.path_edit.text().strip()
            activate_chrome_by_path(chrome_path)

        def close_all_from_path(self):
            chrome_path = self.path_edit.text().strip()
            close_processes_gracefully(chrome_path)


    app = QApplication(sys.argv)
    w = ChromeActivator()
    w.show()
    sys.exit(app.exec())
