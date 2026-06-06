import sys, json, os, time, threading
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import scrolledtext as tkst
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Callable
from pynput import keyboard as pynput_kbd
from pynput.keyboard import Controller as KbdCtrl, Key
from PIL import Image, ImageTk
from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem as TrayItem
from ttkbootstrap import Style, Frame, Button, Entry, Checkbutton, Spinbox, Label
from ttkbootstrap.widgets import ToolTip

CONFIG_DIR = Path(os.environ.get("APPDATA", ".")) / "AutoTypeTool"
CONFIG_FILE = CONFIG_DIR / "config.json"
APP_TITLE = "ZZP自动输入2.0"
MIN_WIN_W, MIN_WIN_H = 700, 580
ICO_PATH = Path(__file__).parent / "app.ico"
THEME = "minty"

HOTKEY_OPTIONS = {
    "F1": Key.f1, "F2": Key.f2, "F3": Key.f3, "F4": Key.f4,
    "F5": Key.f5, "F6": Key.f6, "F7": Key.f7, "F8": Key.f8,
    "F9": Key.f9, "F10": Key.f10, "F11": Key.f11, "F12": Key.f12,
    "F13": Key.f13, "F14": Key.f14, "F15": Key.f15,
    "Insert": Key.insert, "Home": Key.home, "End": Key.end,
    "Pause": Key.pause, "Scroll Lock": Key.scroll_lock,
    "Page Up": Key.page_up, "Page Down": Key.page_down,
}

@dataclass
class Config:
    separator: str = "-"
    filter_chars: str = ""
    group_mode: bool = True
    delay_between_groups: float = 0.3
    delay_between_chars: float = 0.02
    send_enter_after: bool = False
    window_always_on_top: bool = True
    minimize_to_tray: bool = True
    input_history: List[str] = field(default_factory=list)
    hotkey: str = "F9"
    @classmethod
    def load(cls):
        if CONFIG_FILE.exists():
            try:
                d = json.loads(CONFIG_FILE.read_text("utf-8"))
                out = cls()
                for k, v in d.items():
                    if hasattr(out, k): setattr(out, k, v)
                if out.hotkey not in HOTKEY_OPTIONS: out.hotkey = "F9"
                return out
            except: pass
        return cls()
    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")

class AutoTyper:
    def __init__(self, config: Config):
        self.cfg = config; self.kbd = KbdCtrl()
        self._stop_flag = threading.Event(); self._running = False
    def stop(self): self._stop_flag.set()
    @property
    def is_running(self): return self._running
    def run(self, raw_text, on_status=None, on_done=None):
        if self._running: return
        self._running = True; self._stop_flag.clear()
        threading.Thread(target=self._worker, args=(raw_text, on_status, on_done), daemon=True).start()
    def _worker(self, raw_text, on_status, on_done):
        try:
            sep = self.cfg.separator.strip(); fc = self.cfg.filter_chars
            gm = self.cfg.group_mode; dg = self.cfg.delay_between_groups
            dc = self.cfg.delay_between_chars; se = self.cfg.send_enter_after
            t = raw_text.strip()
            if not t: self._notify(on_status, "输入内容为空"); return
            if fc:
                for ch in fc: t = t.replace(ch, "")
            if gm and sep:
                parts = [p.strip() for p in t.split(sep) if p.strip()]
            else:
                parts = [t.strip()]
            if not parts: self._notify(on_status, "过滤后内容为空"); return
            n = len(parts)
            self._notify(on_status, f"输出中... ({n} 组) 按 ESC 中断")
            time.sleep(0.3)
            for i, part in enumerate(parts):
                if self._stop_flag.is_set(): self._notify(on_status, "已中断"); return
                for ch in part:
                    if self._stop_flag.is_set(): self._notify(on_status, "已中断"); return
                    self.kbd.type(ch); time.sleep(dc)
                if i < n - 1: time.sleep(dg)
            if se: self.kbd.tap(Key.enter)
            self._notify(on_status, f"完成！共输出 {n} 组")
            if on_done: self._notify(on_done, parts)
        except Exception as e: self._notify(on_status, f"出错: {e}")
        finally: self._running = False
    @staticmethod
    def _notify(callback, msg):
        if callback:
            try: callback(msg)
            except: pass

class Card:
    def __init__(self, parent, title="", bootstyle="primary", padding=12):
        self.outer = Frame(parent, bootstyle=bootstyle)
        if title:
            Label(self.outer, text=title, font=("微软雅黑", 10, "bold"),
                  bootstyle=f"inverse-{bootstyle}", anchor="w", padding=(12, 6, 12, 2)).pack(fill=tk.X)
        self.inner = Frame(self.outer, padding=padding)
        self.inner.pack(fill=tk.BOTH, expand=True)

class AutoTypeApp:
    def __init__(self, root):
        self.root = root
        self.style = Style(theme=THEME)
        self.cfg = Config.load()
        self.typer = AutoTyper(self.cfg)
        self._tray_icon = None
        self._is_quitting = False

        self.root.title(APP_TITLE)
        self.root.geometry(f"{MIN_WIN_W}x{MIN_WIN_H}")
        self.root.minsize(MIN_WIN_W, MIN_WIN_H)
        if self.cfg.window_always_on_top: self.root.attributes("-topmost", True)

        # 拦截关闭事件 -> 最小化到托盘
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self._set_window_icon()
        self._build_ui()
        self._hotkey_listener = None
        self._restart_hotkey_listener()
        self._refresh_history()

        # 启动系统托盘
        self._start_tray_icon()

    def _set_window_icon(self):
        try:
            if ICO_PATH.exists(): self.root.iconbitmap(str(ICO_PATH))
        except: pass

    # ── 系统托盘 ──────────────────────────────────────────
    def _get_tray_image(self):
        """获取托盘图标 Image 对象"""
        try:
            img = Image.open(str(ICO_PATH))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            return img.resize((16, 16), Image.LANCZOS)
        except:
            img = Image.new("RGBA", (16, 16), (0, 120, 200, 255))
            return img

    def _show_window(self):
        """显示主窗口（从托盘恢复）"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _hide_window(self):
        """隐藏窗口到托盘"""
        self.root.withdraw()

    def _on_window_close(self):
        """窗口关闭按钮 -> 最小化到托盘或退出"""
        self._save_cfg()
        if self.cfg.minimize_to_tray and self._tray_icon:
            self._hide_window()
            # 发送通知提示
            self._set_status("已最小化到系统托盘")
        else:
            self._quit_app()

    def _on_tray_show(self, icon, item):
        """托盘菜单：显示窗口"""
        icon.notify("已恢复窗口", APP_TITLE)
        self.root.after(0, self._show_window)

    def _on_tray_hide(self, icon, item):
        """托盘菜单：隐藏到托盘"""
        self.root.after(0, self._hide_window)

    def _on_tray_quit(self, icon, item):
        """托盘菜单：退出程序"""
        self.root.after(0, self._quit_app)

    def _on_tray_double_click(self, icon):
        """双击托盘图标恢复窗口"""
        self.root.after(0, self._show_window)

    def _start_tray_icon(self):
        """使用 run_detached 启动系统托盘（不阻塞、无需独立线程）"""
        if self._tray_icon:
            try: self._tray_icon.stop()
            except: pass
        img = self._get_tray_image()
        menu = TrayMenu(
            TrayItem("显示窗口", self._on_tray_show, default=True),
            TrayItem("隐藏到托盘", self._on_tray_hide),
            TrayMenu.SEPARATOR,
            TrayItem("退出程序", self._on_tray_quit),
        )
        self._tray_icon = TrayIcon("zzp_auto_input", img, APP_TITLE, menu)
        self._tray_icon.run_detached()
        # 发送启动通知
        try:
            self._tray_icon.notify("程序已启动，关闭窗口可最小化到托盘", APP_TITLE)
        except:
            pass

    def _quit_app(self):
        """完全退出程序"""
        self._is_quitting = True
        self._save_cfg()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        import time
        time.sleep(0.1)
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        self.root.quit()
        self.root.destroy()
        os._exit(0)

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=0)

        # Header
        hdr = Frame(self.root, padding=(15, 10), bootstyle="info")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(1, weight=1)
        try:
            img = Image.open(str(ICO_PATH)).resize((28, 28), Image.LANCZOS)
            self._ico_tk = ImageTk.PhotoImage(img)
            Label(hdr, image=self._ico_tk).grid(row=0, column=0, padx=(0, 10))
        except: pass
        Label(hdr, text=APP_TITLE, font=("微软雅黑", 16, "bold"),
              bootstyle="inverse-info").grid(row=0, column=1, sticky="w")
        self._header_subtitle = Label(hdr, text="", font=("微软雅黑", 9), bootstyle="secondary")
        self._header_subtitle.grid(row=0, column=2, sticky="e", padx=(10, 0))
        hdr.columnconfigure(2, weight=0)

        # Main
        main = Frame(self.root, padding=15)
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        main.rowconfigure(1, weight=0)
        main.rowconfigure(2, weight=0)

        # Input Card
        ci = Card(main, "输入内容", "primary", padding=8)
        ci.outer.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        ci.inner.columnconfigure(0, weight=1); ci.inner.rowconfigure(0, weight=1)
        self.txt_input = tkst.ScrolledText(ci.inner, height=6, font=("Consolas", 11),
                                            wrap=tk.WORD, relief="flat", borderwidth=1, bg="#f8f9fa")
        self.txt_input.grid(row=0, column=0, sticky="nsew")

        br = Frame(ci.outer, padding=(8, 0, 8, 8)); br.pack(fill=tk.X)
        Button(br, text="粘贴", command=self._paste_clip, bootstyle="info-outline", width=8).pack(side=tk.LEFT, padx=(0, 4))
        Button(br, text="清空", command=lambda: self.txt_input.delete("1.0", tk.END), bootstyle="secondary-outline", width=8).pack(side=tk.LEFT, padx=4)
        right = Frame(br); right.pack(side=tk.RIGHT)
        self.btn_stop = Button(right, text="中断 (ESC)", command=self._stop_output, bootstyle="danger-outline", state="disabled", width=12)
        self.btn_stop.pack(side=tk.RIGHT, padx=4)
        self.btn_go = Button(right, text="输出", command=self._start_output, bootstyle="success", width=12)
        self.btn_go.pack(side=tk.RIGHT, padx=4)
        ToolTip(self.btn_go, text="开始输出文本框中的内容")
        ToolTip(self.btn_stop, text="中断正在进行的输出")

        # Config Card
        cc = Card(main, "输出设置", "primary", padding=12)
        cc.outer.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        r1 = Frame(cc.inner); r1.pack(fill=tk.X, pady=2)
        Label(r1, text="触发键", width=8, anchor="e").pack(side=tk.LEFT, padx=(0, 4))
        self.var_hotkey = tk.StringVar(value=self.cfg.hotkey)
        self.hotkey_combo = ttk.Combobox(r1, textvariable=self.var_hotkey,
                                          values=list(HOTKEY_OPTIONS.keys()),
                                          state="readonly", width=10)
        self.hotkey_combo.pack(side=tk.LEFT, padx=(0, 14))
        self.hotkey_combo.bind("<<ComboboxSelected>>", lambda e: self._on_hotkey_changed())

        Label(r1, text="分隔符", width=6, anchor="e").pack(side=tk.LEFT, padx=(0, 4))
        self.var_sep = tk.StringVar(value=self.cfg.separator)
        Entry(r1, textvariable=self.var_sep, width=5).pack(side=tk.LEFT, padx=(0, 14))
        Label(r1, text="过滤字符", width=6, anchor="e").pack(side=tk.LEFT, padx=(0, 4))
        self.var_filter = tk.StringVar(value=self.cfg.filter_chars)
        fe = Entry(r1, textvariable=self.var_filter, width=14)
        fe.pack(side=tk.LEFT)
        ToolTip(fe, text="要移除的字符，如：- , . 空格")

        r2 = Frame(cc.inner); r2.pack(fill=tk.X, pady=2)
        self.var_group = tk.BooleanVar(value=self.cfg.group_mode)
        Checkbutton(r2, text="按分隔符分组", variable=self.var_group, bootstyle="success-round-toggle").pack(side=tk.LEFT, padx=(0, 12))
        Label(r2, text="组间延时").pack(side=tk.LEFT, padx=(0, 4))
        self.var_dg = tk.DoubleVar(value=self.cfg.delay_between_groups)
        Spinbox(r2, from_=0.0, to=3.0, increment=0.05, textvariable=self.var_dg, width=6).pack(side=tk.LEFT, padx=(0, 12))
        Label(r2, text="字符延时").pack(side=tk.LEFT, padx=(0, 4))
        self.var_dc = tk.DoubleVar(value=self.cfg.delay_between_chars)
        Spinbox(r2, from_=0.0, to=0.5, increment=0.01, textvariable=self.var_dc, width=6).pack(side=tk.LEFT)

        r3 = Frame(cc.inner); r3.pack(fill=tk.X, pady=2)
        self.var_enter = tk.BooleanVar(value=self.cfg.send_enter_after)
        Checkbutton(r3, text="结束后按 Enter", variable=self.var_enter, bootstyle="success-round-toggle").pack(side=tk.LEFT, padx=(0, 12))
        self.var_tray = tk.BooleanVar(value=self.cfg.minimize_to_tray)
        Checkbutton(r3, text="关闭时最小化到托盘", variable=self.var_tray, bootstyle="success-round-toggle").pack(side=tk.LEFT, padx=(0, 12))
        self.var_top = tk.BooleanVar(value=self.cfg.window_always_on_top)
        Checkbutton(r3, text="窗口置顶", variable=self.var_top, command=self._toggle_top, bootstyle="success-round-toggle").pack(side=tk.LEFT, padx=(0, 12))
        Button(r3, text="保存设置", command=self._save_cfg, bootstyle="info-outline", width=12).pack(side=tk.RIGHT)

        # History Card
        ch = Card(main, "历史记录（双击填充）", "primary", padding=8)
        ch.outer.grid(row=2, column=0, sticky="ew", pady=(0, 0))
        ch.inner.columnconfigure(0, weight=1)
        self.lst_hist = tk.Listbox(ch.inner, height=4, font=("Consolas", 10),
                                    relief="flat", borderwidth=0, selectbackground="#d4edda",
                                    selectforeground="#155724", activestyle="none")
        self.lst_hist.grid(row=0, column=0, sticky="ew")
        self.lst_hist.bind("<Double-Button-1>", self._on_hist_double_click)
        Button(ch.inner, text="清空历史", command=self._clear_history, bootstyle="secondary-outline", width=10).grid(row=0, column=1, padx=(8, 0), sticky="n")

        # Status
        sf = Frame(self.root, padding=(15, 4), bootstyle="light")
        sf.grid(row=2, column=0, sticky="ew"); sf.columnconfigure(0, weight=1)
        self.var_status = tk.StringVar(value="就绪 - 关闭窗口将最小化到系统托盘")
        Label(sf, textvariable=self.var_status, font=("微软雅黑", 9), anchor="w", padding=4).pack(fill=tk.X)

        self._update_hotkey_display()

    def _update_hotkey_display(self):
        hotkey = self.var_hotkey.get()
        self.btn_go.configure(text=f"输出 ({hotkey})")
        hk = hotkey.replace("Page Up", "PageUp").replace("Page Down", "PageDown").replace("Scroll Lock", "ScrLk")
        self._header_subtitle.configure(text=f"粘贴内容 | {hk} 自动输出 | ESC 中断")

    def _on_hotkey_changed(self):
        self._update_hotkey_display()
        self._restart_hotkey_listener()
        self._set_status(f"触发键已切换为 {self.var_hotkey.get()}")

    def _resolve_hotkey(self):
        return HOTKEY_OPTIONS.get(self.var_hotkey.get(), Key.f9)

    # ── 功能方法 ──────────────────────────────────────────
    def _paste_clip(self):
        try:
            t = self.root.clipboard_get()
            self.txt_input.delete("1.0", tk.END); self.txt_input.insert("1.0", t)
            self._set_status("已粘贴剪贴板内容")
        except tk.TclError: self._set_status("剪贴板为空")
    def _set_status(self, msg): self.var_status.set(msg); self.root.update_idletasks()
    def _toggle_top(self): self.root.attributes("-topmost", self.var_top.get())
    def _start_output(self):
        self._sync_cfg()
        raw = self.txt_input.get("1.0", tk.END)
        if not raw.strip(): self._set_status("输入内容为空"); return
        self._add_history(raw.strip())
        self.btn_go.configure(state="disabled"); self.btn_stop.configure(state="normal")
        self.typer.run(raw.strip(), on_status=self._set_status, on_done=self._on_done)
    def _on_done(self, parts):
        self.root.after(0, lambda: self.btn_go.configure(state="normal"))
        self.root.after(0, lambda: self.btn_stop.configure(state="disabled"))
    def _stop_output(self):
        if self.typer.is_running: self.typer.stop(); self._set_status("已中断")
        self.btn_go.configure(state="normal"); self.btn_stop.configure(state="disabled")
    def _sync_cfg(self):
        c = self.typer.cfg
        c.hotkey = self.var_hotkey.get()
        c.separator = self.var_sep.get().strip(); c.filter_chars = self.var_filter.get()
        c.group_mode = self.var_group.get(); c.delay_between_groups = self.var_dg.get()
        c.delay_between_chars = self.var_dc.get(); c.send_enter_after = self.var_enter.get()
        c.minimize_to_tray = self.var_tray.get()
    def _save_cfg(self):
        self._sync_cfg(); self.typer.cfg.window_always_on_top = self.var_top.get()
        self.typer.cfg.save(); self._set_status("设置已保存")
    def _add_history(self, text):
        h = self.typer.cfg.input_history
        if text in h: h.remove(text)
        h.insert(0, text); self.typer.cfg.input_history = h[:50]
        self.typer.cfg.save(); self._refresh_history()
    def _refresh_history(self):
        self.lst_hist.delete(0, tk.END)
        for item in self.typer.cfg.input_history[:30]:
            d = item[:50] + "..." if len(item) > 50 else item
            self.lst_hist.insert(tk.END, d)
    def _clear_history(self):
        self.typer.cfg.input_history.clear(); self.typer.cfg.save()
        self._refresh_history(); self._set_status("历史已清空")
    def _on_hist_double_click(self, e):
        sel = self.lst_hist.curselection()
        if sel:
            idx = sel[0]; h = self.typer.cfg.input_history
            if idx < len(h):
                self.txt_input.delete("1.0", tk.END); self.txt_input.insert("1.0", h[idx])
                self._set_status("已从历史记录填充")

    # ── 热键 ──────────────────────────────────────────────
    def _restart_hotkey_listener(self):
        if self._hotkey_listener:
            self._hotkey_listener.stop(); self._hotkey_listener = None
        trigger = self._resolve_hotkey()
        def on_press(key):
            try:
                if key == trigger: self.root.after(0, self._start_output)
                elif key == pynput_kbd.Key.esc: self.root.after(0, self._stop_output)
            except: pass
        self._hotkey_listener = pynput_kbd.Listener(on_press=on_press)
        self._hotkey_listener.daemon = True; self._hotkey_listener.start()

    def on_close(self):
        self._on_window_close()

def main():
    root = tk.Tk()
    app = AutoTypeApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app._quit_app()

if __name__ == "__main__":
    main()
