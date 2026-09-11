"""Compact two-step desktop interface for FileDrop."""

import ipaddress
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

import customtkinter as ctk
from tkinter import TclError, filedialog, messagebox

from .assets import ensure_web_assets
from .config import DEFAULT_PORT, load_config, save_config
from .network import human_size, list_local_ips
from .paths import cleanup_temporary_folder, create_temporary_folder, downloads_folder
from .security import sanitize_filename, unique_name
from .server import start_server
from .state import ServerState

try:
    import qrcode
    from PIL import Image
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    import pystray
    from PIL import Image as PILImage
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False


ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# Muted palette: surfaces are separated by tone and borders, with one accent.
APP_BG = "#e9edef"
PANEL = "#fafbfa"
PANEL_ALT = "#f1f4f4"
CONTROL = "#e3e8ea"
BORDER = "#cbd3d6"
TEXT = "#22292d"
MUTED = "#667178"
ACCENT = "#526b78"
ACCENT_HOVER = "#425965"
ACCENT_SOFT = "#e2e9ec"
SUCCESS = "#4f705d"
DANGER = "#9b5050"
DANGER_SOFT = "#f5e7e7"

FONT_DISPLAY = ("Segoe UI", 26, "bold")
FONT_TITLE = ("Segoe UI", 20, "bold")
FONT_HEADING = ("Segoe UI", 15, "bold")
FONT_BODY = ("Segoe UI", 12)
FONT_BODY_BOLD = ("Segoe UI", 12, "bold")
FONT_SMALL = ("Segoe UI", 11)
FONT_MONO = ("Consolas", 14, "bold")


class FileDropAppCTk:
    def __init__(self):
        self.cfg = load_config()
        self.httpd = None
        self.port = int(self.cfg.get("port", DEFAULT_PORT))
        self.ips = list_local_ips()
        self.url = ""
        self.qr_image = None
        self.tray_icon = None
        self._drag_overlay = None
        self._file_rows = []
        self._temp_folder = None

        self.settings_win = None
        self.files_win = None
        self.notes_win = None
        self.files_scroll = None
        self.progress = None
        self.notes_text = None

        self.root = ctk.CTk()
        self.root.title("FileDrop")
        self._center_root(520, 420)
        self.root.resizable(False, False)
        self.root.configure(fg_color=APP_BG)

        default_ip = self.ips[0] if self.ips else "127.0.0.1"
        self.ip_var = ctk.StringVar(value=default_ip)
        self.port_var = ctk.StringVar(value=str(self.port))
        self.url_var = ctk.StringVar(value="")
        self.status_var = ctk.StringVar(value="Не запущено")
        self.files_meta_var = ctk.StringVar(value="Файлов пока нет")
        self.count_var = ctk.StringVar(value="Нет файлов")
        self.folder_var = ctk.StringVar(value="")
        self.sessions_var = ctk.StringVar(value="PIN отключён")
        self.pin_var = ctk.StringVar(value="")
        self.interface_hint_var = ctk.StringVar(value=self._interface_hint(default_ip))
        self.notes_status_var = ctk.StringVar(value="")
        self.pin_enabled = ctk.BooleanVar(value=False)
        self.pin_on_start_var = ctk.BooleanVar(value=bool(self.cfg.get("pin_on_start")))
        self.temp_storage_var = ctk.BooleanVar(
            value=bool(self.cfg.get("temporary_folder", True))
        )

        self._init_storage()
        self._sync_folder_label()
        ServerState.theme = "light"
        ServerState.on_file_received = self.on_file_received
        ServerState.on_progress = self.on_progress
        ServerState.on_notes_changed = self.on_notes_changed
        ensure_web_assets()
        os.makedirs(ServerState.upload_folder, exist_ok=True)
        self._build_interface_step()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Map>", self._on_mapped)
        if HAS_DND:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind("<<Drop>>", self.on_drop)
                self.root.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                self.root.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            except Exception:
                pass

    # Storage ----------------------------------------------------------

    def _init_storage(self):
        if self.temp_storage_var.get():
            self._temp_folder = create_temporary_folder()
            ServerState.upload_folder = self._temp_folder
        else:
            ServerState.upload_folder = self.cfg.get("folder") or downloads_folder()
            os.makedirs(ServerState.upload_folder, exist_ok=True)

    def _sync_folder_label(self):
        if self.temp_storage_var.get():
            self.folder_var.set("Временная папка — удалится при выходе")
        else:
            self.folder_var.set(ServerState.upload_folder)

    def _switch_to_temporary_storage(self):
        if not self._temp_folder or not os.path.isdir(self._temp_folder):
            self._temp_folder = create_temporary_folder()
        ServerState.upload_folder = self._temp_folder
        self._sync_folder_label()
        self.check_folder()
        self.refresh_list()
        self._update_storage_hint()

    def _switch_to_persistent_storage(self):
        old_temp = self._temp_folder
        folder = self.cfg.get("folder") or downloads_folder()
        os.makedirs(folder, exist_ok=True)
        ServerState.upload_folder = folder
        if old_temp:
            cleanup_temporary_folder(old_temp)
            self._temp_folder = None
        self._sync_folder_label()
        self.check_folder()
        self.refresh_list()
        self._update_storage_hint()

    def toggle_storage_mode(self):
        if self.temp_storage_var.get():
            self._switch_to_temporary_storage()
        else:
            self._switch_to_persistent_storage()

    # Step 1: network interface ---------------------------------------

    def _clear_root(self):
        for child in self.root.winfo_children():
            child.destroy()

    def _center_root(self, width, height):
        self.root.update_idletasks()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry("{}x{}+{}+{}".format(width, height, x, y))

    def _place_window(self, window, width, height):
        """Places a secondary window predictably near the main window."""
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_y() + 44
        x = max(8, min(x, screen_w - width - 8))
        y = max(8, min(y, screen_h - height - 48))
        window.geometry("{}x{}+{}+{}".format(width, height, x, y))

    @staticmethod
    def _interface_hint(ip):
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            address = None
        if address and address.is_loopback:
            return "Доступ будет только с этого компьютера"
        if address and address.is_private:
            return "Подходит для устройств в вашей домашней сети"
        return "Адрес, по которому другие устройства откроют FileDrop"

    def _on_interface_selected(self, value):
        self.interface_hint_var.set(self._interface_hint(value))
        self.refresh_url()

    def _build_interface_step(self):
        self._clear_root()
        self.root.title("FileDrop — подключение")

        shell = ctk.CTkFrame(
            self.root, fg_color=PANEL, corner_radius=16,
            border_width=1, border_color=BORDER
        )
        shell.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(
            shell, text="FD", width=48, height=48, corner_radius=12,
            fg_color=ACCENT, text_color="#ffffff", font=("Segoe UI", 13, "bold")
        ).pack(anchor="w", padx=32, pady=(24, 12))
        ctk.CTkLabel(
            shell, text="Выберите подключение", font=FONT_DISPLAY,
            text_color=TEXT, anchor="w", height=32
        ).pack(fill="x", padx=32)
        ctk.CTkLabel(
            shell,
            text="По этому адресу телефон или другой компьютер откроет FileDrop.",
            font=FONT_BODY, text_color=MUTED, justify="left", wraplength=390,
            anchor="w", height=32
        ).pack(fill="x", padx=32, pady=(8, 16))

        select = ctk.CTkFrame(shell, fg_color=PANEL_ALT, corner_radius=10)
        select.pack(fill="x", padx=32)
        ctk.CTkLabel(
            select, text="Адрес этого компьютера", font=FONT_SMALL,
            text_color=MUTED, anchor="w", height=18
        ).pack(fill="x", padx=14, pady=(8, 4))
        self.selection_ip_menu = ctk.CTkOptionMenu(
            select, values=self.ips or ["127.0.0.1"], variable=self.ip_var,
            command=self._on_interface_selected,
            font=FONT_BODY_BOLD, text_color=TEXT, fg_color=PANEL_ALT,
            button_color=CONTROL, button_hover_color=BORDER,
            dropdown_fg_color=PANEL, dropdown_text_color=TEXT,
            dropdown_hover_color=ACCENT_SOFT, height=40, corner_radius=8
        )
        self.selection_ip_menu.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(
            shell, textvariable=self.interface_hint_var,
            font=FONT_SMALL, text_color=MUTED, justify="left",
            wraplength=390, anchor="w", height=18
        ).pack(fill="x", padx=32, pady=(8, 0))

        actions = ctk.CTkFrame(shell, fg_color="transparent")
        actions.pack(fill="x", padx=32, pady=(16, 24))
        ctk.CTkButton(
            actions, text="Найти заново", command=self.refresh_network,
            font=FONT_BODY, text_color=TEXT, fg_color=CONTROL,
            hover_color=BORDER, width=120, height=40, corner_radius=9
        ).pack(side="left")
        ctk.CTkButton(
            actions, text="Продолжить", command=self._start_main,
            font=FONT_BODY_BOLD, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            height=40, corner_radius=9
        ).pack(side="right", fill="x", expand=True, padx=(10, 0))
        self.root.bind("<Return>", lambda _event: self._start_main())

    def _start_main(self):
        if self.httpd is None:
            self._launch_server()
        if not self.httpd:
            return
        if self.cfg.get("pin_on_start"):
            self.pin_enabled.set(True)
            self.apply_pin()
        self._build_main_ui()
        self.refresh_url()
        self.refresh_list()

    # Step 2: compact main window -------------------------------------

    def _build_main_ui(self):
        self._clear_root()
        self.root.unbind("<Return>")
        self.root.title("FileDrop")
        self._center_root(576, 690)
        self.root.resizable(False, False)

        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=24, pady=16)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))
        header.grid_columnconfigure(0, weight=1)
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_box, text="FileDrop", font=FONT_TITLE, text_color=TEXT, height=24
        ).pack(anchor="w")
        self.status_header = ctk.CTkLabel(
            title_box, textvariable=self.status_var, font=FONT_SMALL,
            text_color=SUCCESS, height=16
        )
        self.status_header.pack(anchor="w", pady=(2, 0))
        ctk.CTkButton(
            header, text="Сменить сеть", command=self._change_interface,
            font=FONT_SMALL, text_color=TEXT, fg_color=CONTROL,
            hover_color=BORDER, width=112, height=36, corner_radius=8
        ).grid(row=0, column=1, sticky="e")

        qr_card = ctk.CTkFrame(
            main, fg_color=PANEL, corner_radius=16,
            border_width=1, border_color=BORDER
        )
        qr_card.pack(fill="x")
        ctk.CTkLabel(
            qr_card, text="Сканируйте камерой телефона", font=FONT_HEADING,
            text_color=TEXT, height=22
        ).pack(pady=(16, 2))
        ctk.CTkLabel(
            qr_card, text="Устройства должны быть подключены к одной сети",
            font=FONT_SMALL, text_color=MUTED, height=18
        ).pack(pady=(0, 8))
        self.qr_label = ctk.CTkLabel(
            qr_card, text="", fg_color="#ffffff", width=224, height=224,
            corner_radius=10
        )
        self.qr_label.pack(padx=22, pady=(0, 10))
        self.url_label = ctk.CTkLabel(
            qr_card, textvariable=self.url_var, font=FONT_MONO,
            text_color=TEXT, fg_color=PANEL_ALT, corner_radius=8,
            height=40, cursor="hand2"
        )
        self.url_label.pack(fill="x", padx=20)
        self.url_label.bind("<Button-1>", lambda _event: self.copy_url())

        qr_actions = ctk.CTkFrame(qr_card, fg_color="transparent")
        qr_actions.pack(pady=(8, 14))
        self.copy_btn = ctk.CTkButton(
            qr_actions, text="Копировать адрес", command=self.copy_url,
            font=FONT_SMALL, text_color=TEXT, fg_color=CONTROL,
            hover_color=BORDER, width=142, height=36, corner_radius=8
        )
        self.copy_btn.pack(side="left")
        ctk.CTkButton(
            qr_actions, text="Открыть здесь", command=self.open_browser,
            font=FONT_SMALL, text_color="#ffffff", fg_color=ACCENT,
            hover_color=ACCENT_HOVER, width=132, height=36, corner_radius=8
        ).pack(side="left", padx=(8, 0))

        tools = ctk.CTkFrame(main, fg_color="transparent")
        tools.pack(fill="x", pady=(16, 0))
        tools.grid_columnconfigure(0, weight=1, uniform="tools")
        tools.grid_columnconfigure(1, weight=1, uniform="tools")
        self._tool_button(tools, 0, 0, "Файлы этой сессии", self.files_meta_var, self.open_files)
        self._tool_button(tools, 0, 1, "Заметка", "Ссылка или текст для устройств", self.open_notes)
        self._tool_button(tools, 1, 0, "Открыть папку", "Показать файлы на компьютере", self.open_folder)
        self._tool_button(tools, 1, 1, "Настройки", "Порт, хранение и PIN-код", self.open_settings)

        self.storage_hint = ctk.CTkLabel(
            main, text="", font=FONT_SMALL, text_color=MUTED, height=18
        )
        self.storage_hint.pack(pady=(8, 0))
        self._update_storage_hint()

    def _tool_button(self, parent, row, column, title, detail, command):
        card = ctk.CTkFrame(
            parent, height=72, fg_color=PANEL, corner_radius=11,
            border_width=1, border_color=BORDER
        )
        card.grid(
            row=row, column=column, sticky="ew",
            padx=(0, 6) if column == 0 else (6, 0),
            pady=(0, 8) if row == 0 else 0
        )
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)
        title_label = ctk.CTkLabel(
            card, text=title, font=FONT_BODY_BOLD, text_color=TEXT,
            anchor="w", cursor="hand2", height=20
        )
        title_label.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=(13, 1))
        detail_label = ctk.CTkLabel(
            card, textvariable=detail if hasattr(detail, "get") else None,
            text=detail if not hasattr(detail, "get") else "",
            font=FONT_SMALL, text_color=MUTED, anchor="w", justify="left",
            wraplength=210, cursor="hand2", height=18
        )
        detail_label.grid(row=1, column=0, sticky="ew", padx=(16, 8))
        arrow = ctk.CTkLabel(
            card, text="›", font=("Segoe UI", 20), text_color=ACCENT,
            width=20, cursor="hand2"
        )
        arrow.grid(row=0, column=1, rowspan=2, padx=(0, 12))

        def hover(active):
            card.configure(
                fg_color=PANEL_ALT if active else PANEL,
                border_color="#aebbc0" if active else BORDER
            )

        for widget in (card, title_label, detail_label, arrow):
            widget.bind("<Button-1>", lambda _event, fn=command: fn())
            widget.bind("<Enter>", lambda _event: hover(True))
            widget.bind("<Leave>", lambda _event: hover(False))

    def _change_interface(self):
        self._close_auxiliary_windows()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.status_var.set("Не запущено")
        self._center_root(520, 420)
        self._build_interface_step()

    # Files window -----------------------------------------------------

    def open_files(self):
        if self._focus_existing("files_win"):
            return
        win = ctk.CTkToplevel(self.root)
        win.title("FileDrop — файлы этой сессии")
        self._place_window(win, 720, 540)
        win.minsize(600, 440)
        win.configure(fg_color=APP_BG)
        win.transient(self.root)
        self.files_win = win

        shell = ctk.CTkFrame(
            win, fg_color=PANEL, corner_radius=14,
            border_width=1, border_color=BORDER
        )
        shell.pack(fill="both", expand=True, padx=16, pady=16)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(shell, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 12))
        head.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title, text="Файлы этой сессии", font=FONT_TITLE, text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(title, textvariable=self.count_var, font=FONT_SMALL, text_color=MUTED).pack(anchor="w")
        ctk.CTkButton(
            head, text="Открыть папку", command=self.open_folder,
            font=FONT_SMALL, text_color=TEXT, fg_color=CONTROL,
            hover_color=BORDER, width=120, height=36, corner_radius=8
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            head, text="Обновить", command=self.refresh_list,
            font=FONT_SMALL, text_color="#ffffff", fg_color=ACCENT,
            hover_color=ACCENT_HOVER, width=92, height=36, corner_radius=8
        ).grid(row=0, column=2, padx=(8, 0))
        self.progress = ctk.CTkProgressBar(
            shell, height=5, fg_color=CONTROL, progress_color=ACCENT, corner_radius=3
        )
        self.progress.set(0)
        self.files_scroll = ctk.CTkScrollableFrame(
            shell, fg_color=PANEL_ALT, corner_radius=10,
            scrollbar_button_color=ACCENT, scrollbar_button_hover_color=ACCENT_HOVER
        )
        self.files_scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.files_scroll.grid_columnconfigure(0, weight=1)
        win.protocol("WM_DELETE_WINDOW", self._close_files)
        self.refresh_list()

    def _close_files(self):
        if self._window_exists(self.files_win):
            self.files_win.destroy()
        self.files_win = None
        self.files_scroll = None
        self.progress = None
        self._file_rows.clear()

    def refresh_list(self):
        items = []
        try:
            for entry in os.scandir(ServerState.upload_folder):
                if entry.is_file():
                    stat = entry.stat()
                    items.append((
                        entry.name,
                        stat.st_size,
                        time.strftime("%H:%M", time.localtime(stat.st_mtime)),
                        stat.st_mtime,
                    ))
        except OSError:
            pass
        items.sort(key=lambda item: item[3], reverse=True)

        count = len(items)
        if count == 0:
            count_text = "Нет файлов"
            self.files_meta_var.set("Файлов пока нет")
        else:
            ending = "" if count % 10 == 1 and count % 100 != 11 else (
                "а" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "ов"
            )
            count_text = "{} файл{}".format(count, ending)
            self.files_meta_var.set(
                "{} · {}".format(count_text, human_size(sum(item[1] for item in items)))
            )
        self.count_var.set(count_text)

        if not self.files_scroll or not self._window_exists(self.files_win):
            return
        for widget in self.files_scroll.winfo_children():
            widget.destroy()
        self._file_rows.clear()
        if not items:
            empty = ctk.CTkLabel(
                self.files_scroll,
                text="Здесь появятся файлы, отправленные с другого устройства.",
                font=FONT_BODY, text_color=MUTED, wraplength=400, justify="center"
            )
            empty.grid(row=0, column=0, pady=90)
            return
        for idx, (name, size, when, _stamp) in enumerate(items):
            self._file_rows.append(self._build_file_row(name, size, when, idx))

    def _build_file_row(self, name, size, when, idx):
        row = ctk.CTkFrame(
            self.files_scroll, fg_color=PANEL, corner_radius=9,
            border_width=1, border_color=BORDER, height=64
        )
        row.grid(row=idx, column=0, sticky="ew", pady=(0, 7), padx=2)
        row.grid_columnconfigure(1, weight=1)
        row.grid_propagate(False)
        ctk.CTkLabel(
            row, text=self._file_icon(name), font=("Segoe UI", 9, "bold"),
            text_color=ACCENT, fg_color=ACCENT_SOFT, corner_radius=7,
            width=38, height=30
        ).grid(row=0, column=0, rowspan=2, padx=(10, 9), pady=9)
        ctk.CTkLabel(
            row, text=self._short_filename(name), font=FONT_BODY,
            text_color=TEXT, anchor="w", height=18
        ).grid(row=0, column=1, sticky="sw", pady=(9, 0))
        ctk.CTkLabel(
            row, text="{} · {}".format(when, human_size(size)),
            font=FONT_SMALL, text_color=MUTED, anchor="w", height=16
        ).grid(row=1, column=1, sticky="nw", pady=(0, 9))
        ctk.CTkButton(
            row, text="Открыть", width=72, height=34, corner_radius=8,
            text_color=TEXT, fg_color=CONTROL, hover_color=BORDER,
            command=lambda n=name: self._open_file(n)
        ).grid(row=0, column=2, rowspan=2, padx=(8, 4))
        ctk.CTkButton(
            row, text="Удалить", width=70, height=34, corner_radius=8,
            text_color=DANGER, fg_color=DANGER_SOFT, hover_color="#edd5d5",
            command=lambda n=name: self._delete_file(n)
        ).grid(row=0, column=3, rowspan=2, padx=(0, 9))
        return row

    @staticmethod
    def _file_icon(name):
        ext = os.path.splitext(name)[1].lower()
        groups = {
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"): "IMG",
            (".mp4", ".mov", ".avi", ".mkv"): "VID",
            (".mp3", ".wav", ".flac", ".aac"): "AUD",
            (".zip", ".rar", ".7z", ".tar", ".gz"): "ZIP",
            (".pdf", ".doc", ".docx", ".txt", ".rtf"): "DOC",
            (".exe", ".msi", ".dmg", ".appimage"): "APP",
        }
        for exts, label in groups.items():
            if ext in exts:
                return label
        return "FILE"

    @staticmethod
    def _short_filename(name, limit=58):
        if len(name) <= limit:
            return name
        stem, ext = os.path.splitext(name)
        tail = ext[:12]
        room = max(18, limit - len(tail) - 1)
        return stem[:room] + "…" + tail

    def _open_file(self, name):
        full = os.path.join(ServerState.upload_folder, os.path.basename(name))
        if not os.path.isfile(full):
            return
        try:
            if sys.platform == "win32":
                os.startfile(full)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", full])
            else:
                subprocess.Popen(["xdg-open", full])
        except OSError as exc:
            messagebox.showerror("FileDrop", "Не удалось открыть:\n{}".format(exc))

    def _delete_file(self, name):
        full = os.path.join(ServerState.upload_folder, os.path.basename(name))
        if not os.path.isfile(full):
            self.refresh_list()
            return
        if not messagebox.askyesno(
            "FileDrop", "Удалить '{}' с диска?".format(name), parent=self.files_win
        ):
            return
        try:
            os.remove(full)
        except OSError as exc:
            messagebox.showerror("FileDrop", "Не удалось удалить:\n{}".format(exc))
        self.refresh_list()

    # Notes window -----------------------------------------------------

    def open_notes(self):
        if self._focus_existing("notes_win"):
            return
        self.notes_status_var.set("")
        win = ctk.CTkToplevel(self.root)
        win.title("FileDrop — заметка")
        self._place_window(win, 560, 430)
        win.minsize(480, 360)
        win.configure(fg_color=APP_BG)
        win.transient(self.root)
        self.notes_win = win

        shell = ctk.CTkFrame(
            win, fg_color=PANEL, corner_radius=14,
            border_width=1, border_color=BORDER
        )
        shell.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(shell, text="Общая заметка", font=FONT_TITLE, text_color=TEXT).pack(
            anchor="w", padx=20, pady=(18, 2)
        )
        ctk.CTkLabel(
            shell, text="Ссылка, адрес или короткий текст будет виден на всех устройствах.",
            font=FONT_SMALL, text_color=MUTED, anchor="w"
        ).pack(fill="x", padx=20, pady=(0, 16))
        self.notes_text = ctk.CTkTextbox(
            shell, font=FONT_BODY, fg_color=PANEL_ALT, text_color=TEXT,
            border_width=1, border_color=BORDER, corner_radius=9, wrap="word"
        )
        self.notes_text.pack(fill="both", expand=True, padx=20)
        self.notes_text.insert("0.0", ServerState.notes)
        note_footer = ctk.CTkFrame(shell, fg_color="transparent")
        note_footer.pack(fill="x", padx=20, pady=16)
        ctk.CTkLabel(
            note_footer, textvariable=self.notes_status_var,
            font=FONT_SMALL, text_color=SUCCESS
        ).pack(side="left")
        ctk.CTkButton(
            note_footer, text="Сохранить", command=self.save_notes,
            font=FONT_BODY_BOLD, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            width=128, height=40, corner_radius=9
        ).pack(side="right")
        win.bind("<Control-s>", lambda _event: self.save_notes())
        win.protocol("WM_DELETE_WINDOW", self._close_notes)

    def _close_notes(self):
        if self.notes_text:
            ServerState.notes = self.notes_text.get("0.0", "end")[:100000]
        if self._window_exists(self.notes_win):
            self.notes_win.destroy()
        self.notes_win = None
        self.notes_text = None

    def save_notes(self):
        if self.notes_text:
            ServerState.notes = self.notes_text.get("0.0", "end")[:100000]
        self.set_status("Заметка сохранена")
        self.notes_status_var.set("Сохранено")
        self.root.after(1800, lambda: self.set_status("Готов к обмену"))
        self.root.after(1800, lambda: self.notes_status_var.set(""))

    def on_notes_changed(self, text):
        self.root.after(0, lambda value=text: self._set_notes_text(value))

    def _set_notes_text(self, text):
        if not self.notes_text or not self._window_exists(self.notes_win):
            return
        self.notes_text.delete("0.0", "end")
        self.notes_text.insert("0.0", text)

    # Settings window --------------------------------------------------

    def open_settings(self):
        if self._focus_existing("settings_win"):
            return
        win = ctk.CTkToplevel(self.root)
        win.title("FileDrop — настройки")
        self._place_window(win, 540, 620)
        win.resizable(False, False)
        win.configure(fg_color=APP_BG)
        win.transient(self.root)
        self.settings_win = win

        shell = ctk.CTkFrame(win, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(shell, text="Настройки", font=FONT_TITLE, text_color=TEXT).pack(
            anchor="w", pady=(0, 10)
        )

        net = self._settings_card(shell, "Подключение")
        net_row = ctk.CTkFrame(net, fg_color="transparent")
        net_row.pack(fill="x", padx=14, pady=(0, 12))
        net_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            net_row, text="IP-адрес", font=FONT_SMALL,
            text_color=MUTED, anchor="w", height=18
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ctk.CTkLabel(
            net_row, text="Порт", font=FONT_SMALL,
            text_color=MUTED, anchor="w", height=18
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))
        self.ip_menu = ctk.CTkOptionMenu(
            net_row, values=self.ips or ["127.0.0.1"], variable=self.ip_var,
            command=lambda _value: self.refresh_url(), font=FONT_BODY,
            text_color=TEXT, fg_color=PANEL_ALT, button_color=CONTROL,
            button_hover_color=BORDER, dropdown_fg_color=PANEL,
            dropdown_text_color=TEXT, dropdown_hover_color=ACCENT_SOFT,
            width=205, height=34
        )
        self.ip_menu.grid(row=1, column=0, sticky="ew")
        port_entry = ctk.CTkEntry(
            net_row, textvariable=self.port_var, width=82, height=34,
            font=FONT_BODY, fg_color=PANEL_ALT, border_color=BORDER,
            text_color=TEXT
        )
        port_entry.grid(row=1, column=1, padx=(8, 0))
        ctk.CTkButton(
            net_row, text="Применить", command=self.apply_port,
            font=FONT_SMALL, text_color="#ffffff", fg_color=ACCENT,
            hover_color=ACCENT_HOVER, width=88, height=34, corner_radius=8
        ).grid(row=1, column=2, padx=(8, 0))
        port_entry.bind("<Return>", lambda _event: self.apply_port())

        storage = self._settings_card(shell, "Хранение файлов")
        ctk.CTkCheckBox(
            storage, text="Использовать временную папку до закрытия",
            variable=self.temp_storage_var, command=self.toggle_storage_mode,
            font=FONT_BODY, text_color=TEXT, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, border_color=BORDER
        ).pack(anchor="w", padx=14, pady=(0, 9))
        ctk.CTkLabel(
            storage, textvariable=self.folder_var, font=FONT_SMALL,
            text_color=MUTED, fg_color=PANEL_ALT, corner_radius=7,
            wraplength=440, justify="left", anchor="w", height=38
        ).pack(fill="x", padx=14)
        folder_actions = ctk.CTkFrame(storage, fg_color="transparent")
        folder_actions.pack(fill="x", padx=14, pady=(9, 12))
        ctk.CTkButton(
            folder_actions, text="Выбрать другую", command=self.choose_folder,
            font=FONT_SMALL, text_color=TEXT, fg_color=CONTROL,
            hover_color=BORDER, width=124, height=34, corner_radius=8
        ).pack(side="left")
        ctk.CTkButton(
            folder_actions, text="Открыть папку", command=self.open_folder,
            font=FONT_SMALL, text_color=TEXT, fg_color="transparent",
            hover_color=PANEL_ALT, width=120, height=34, corner_radius=8
        ).pack(side="left", padx=(6, 0))
        self.folder_warn = ctk.CTkLabel(
            storage, text="", font=FONT_SMALL, text_color=DANGER
        )

        access = self._settings_card(shell, "Доступ")
        pin_row = ctk.CTkFrame(access, fg_color="transparent")
        pin_row.pack(fill="x", padx=14, pady=(0, 7))
        ctk.CTkCheckBox(
            pin_row, text="Защитить PIN-кодом", variable=self.pin_enabled,
            command=self.apply_pin, font=FONT_BODY, text_color=TEXT,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=BORDER
        ).pack(side="left")
        ctk.CTkLabel(
            pin_row, textvariable=self.pin_var, font=FONT_MONO, text_color=ACCENT
        ).pack(side="right")
        ctk.CTkCheckBox(
            access, text="Включать PIN при каждом запуске",
            variable=self.pin_on_start_var, command=self.apply_pin_on_start,
            font=FONT_SMALL, text_color=MUTED, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, border_color=BORDER
        ).pack(anchor="w", padx=14, pady=(0, 6))
        ctk.CTkLabel(
            access, textvariable=self.sessions_var, font=FONT_SMALL, text_color=MUTED
        ).pack(anchor="w", padx=14, pady=(0, 12))

        ctk.CTkButton(
            shell, text="Готово", command=self._close_settings,
            font=FONT_BODY_BOLD, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            height=40, corner_radius=9
        ).pack(fill="x", pady=(2, 0))
        self.check_folder()
        self.update_sessions()
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

    def _settings_card(self, parent, title):
        card = ctk.CTkFrame(
            parent, fg_color=PANEL, corner_radius=11,
            border_width=1, border_color=BORDER
        )
        card.pack(fill="x", pady=(0, 9))
        ctk.CTkLabel(card, text=title, font=FONT_HEADING, text_color=TEXT).pack(
            anchor="w", padx=14, pady=(11, 9)
        )
        return card

    def _close_settings(self):
        self.persist_config()
        if self._window_exists(self.settings_win):
            self.settings_win.destroy()
        self.settings_win = None

    # Server and network -----------------------------------------------

    def _launch_server(self):
        self.httpd, port = start_server(self.port)
        if not self.httpd:
            messagebox.showerror("FileDrop", "Не удалось занять свободный порт.")
            return
        self.port = port
        self.port_var.set(str(port))
        self.status_var.set("Готов к обмену")
        self.refresh_url()

    def apply_port(self):
        try:
            new_port = int(self.port_var.get())
            if not 1 <= new_port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("FileDrop", "Введите номер порта от 1 до 65535.")
            return
        if new_port == self.port and self.httpd:
            return
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.port = new_port
        self._launch_server()
        self.cfg["port"] = self.port
        save_config(self.cfg)

    def refresh_url(self):
        self.url = "http://{}:{}".format(self.ip_var.get(), self.port)
        self.url_var.set(self.url)
        qr_label = getattr(self, "qr_label", None)
        try:
            if qr_label and qr_label.winfo_exists():
                self.render_qr()
        except TclError:
            pass

    def refresh_network(self):
        current = self.ip_var.get()
        self.ips = list_local_ips()
        values = self.ips or ["127.0.0.1"]
        self.ip_var.set(current if current in values else values[0])
        self.interface_hint_var.set(self._interface_hint(self.ip_var.get()))
        for name in ("selection_ip_menu", "ip_menu"):
            menu = getattr(self, name, None)
            try:
                if menu and menu.winfo_exists():
                    menu.configure(values=values)
            except TclError:
                pass
        self.refresh_url()

    def render_qr(self):
        if not HAS_QR:
            self.qr_label.configure(
                text="QR-код недоступен\nИспользуйте адрес ниже",
                text_color=MUTED, font=FONT_BODY, image=None
            )
            return
        qr = qrcode.QRCode(box_size=7, border=3)
        qr.add_data(self.url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#20272b", back_color="#ffffff")
        img = img.resize((220, 220), Image.Resampling.NEAREST)
        self.qr_image = ctk.CTkImage(light_image=img, dark_image=img, size=(220, 220))
        self.qr_label.configure(image=self.qr_image, text="")

    def set_status(self, text, error=False):
        self.status_var.set(text)
        status = getattr(self, "status_header", None)
        try:
            if status and status.winfo_exists():
                status.configure(text_color=DANGER if error else SUCCESS)
        except TclError:
            pass

    # PIN --------------------------------------------------------------

    def apply_pin(self):
        ServerState.pin = "{:04d}".format(secrets.randbelow(10000)) if self.pin_enabled.get() else None
        ServerState.tokens.revoke_all()
        self.pin_var.set(ServerState.pin or "")
        self.update_sessions()

    def apply_pin_on_start(self):
        self.cfg["pin_on_start"] = bool(self.pin_on_start_var.get())
        save_config(self.cfg)

    def update_sessions(self):
        if ServerState.pin:
            self.sessions_var.set("Активных сессий: {}".format(ServerState.tokens.count()))
        else:
            self.sessions_var.set("PIN отключён")

    # Transfer callbacks and local file operations --------------------

    def on_file_received(self, filename, size):
        self.root.after(0, self.refresh_list)
        if HAS_TRAY and self.tray_icon:
            self.tray_icon.notify("Получен: {}".format(filename), "FileDrop")

    def on_progress(self, done, total):
        self.root.after(0, lambda: self._set_progress(done, total))

    def _set_progress(self, done, total):
        if total <= 0:
            if self.progress and self._window_exists(self.files_win):
                self.progress.grid_forget()
            self.set_status("Готов к обмену")
            return
        if self.progress and self._window_exists(self.files_win):
            if not self.progress.winfo_ismapped():
                self.progress.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
            self.progress.set(min(1, done / total))
        self.set_status("Приём файла — {:.0f}%".format(done / total * 100))

    def on_drop(self, event):
        if self._drag_overlay:
            self._drag_overlay.destroy()
            self._drag_overlay = None
        for path in self.root.tk.splitlist(event.data):
            if not os.path.isfile(path):
                continue
            clean = sanitize_filename(os.path.basename(path))
            name = unique_name(ServerState.upload_folder, clean)
            dst = os.path.join(ServerState.upload_folder, name)
            try:
                shutil.copy2(path, dst)
                self.on_file_received(name, os.path.getsize(dst))
            except OSError as exc:
                messagebox.showerror("FileDrop", "Не удалось добавить файл:\n{}".format(exc))

    def _on_drag_enter(self, _event):
        if self.httpd is None or self._drag_overlay is not None:
            return
        self._drag_overlay = ctk.CTkFrame(self.root, fg_color=ACCENT, corner_radius=0)
        self._drag_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        ctk.CTkLabel(
            self._drag_overlay, text="Отпустите файлы", font=FONT_TITLE,
            text_color="#ffffff"
        ).place(relx=0.5, rely=0.5, anchor="center")
        self._drag_overlay.lift()

    def _on_drag_leave(self, _event):
        if self._drag_overlay:
            self._drag_overlay.destroy()
            self._drag_overlay = None

    def choose_folder(self):
        folder = filedialog.askdirectory(
            title="Папка для сохранения файлов",
            initialdir=ServerState.upload_folder,
            parent=self.settings_win
        )
        if not folder:
            return
        self.temp_storage_var.set(False)
        self._cleanup_temp_folder()
        ServerState.upload_folder = folder
        self.cfg["folder"] = folder
        save_config(self.cfg)
        os.makedirs(folder, exist_ok=True)
        self._sync_folder_label()
        self.check_folder()
        self.refresh_list()
        self._update_storage_hint()

    def check_folder(self):
        widget = getattr(self, "folder_warn", None)
        if not widget:
            return
        try:
            if not widget.winfo_exists():
                return
        except TclError:
            return
        folder = ServerState.upload_folder
        ok = os.path.isdir(folder) and os.access(folder, os.W_OK)
        if not ok:
            try:
                os.makedirs(folder, exist_ok=True)
                ok = os.access(folder, os.W_OK)
            except OSError:
                ok = False
        if ok:
            widget.configure(text="")
            widget.pack_forget()
        else:
            widget.configure(text="Папка недоступна для записи")
            widget.pack(anchor="w", padx=14, pady=(0, 10))

    def _update_storage_hint(self):
        hint = getattr(self, "storage_hint", None)
        if not hint:
            return
        text = (
            "Временные файлы удалятся при выходе"
            if self.temp_storage_var.get()
            else "Файлы сохраняются в выбранной папке"
        )
        try:
            hint.configure(text=text)
        except TclError:
            pass

    def open_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(ServerState.upload_folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", ServerState.upload_folder])
            else:
                subprocess.Popen(["xdg-open", ServerState.upload_folder])
        except OSError as exc:
            messagebox.showerror("FileDrop", "Не удалось открыть папку:\n{}".format(exc))

    def copy_url(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url)
        button = getattr(self, "copy_btn", None)
        if button:
            button.configure(text="Скопировано")
            self.root.after(1400, lambda: self._restore_copy_button(button))

    @staticmethod
    def _restore_copy_button(button):
        try:
            if button.winfo_exists():
                button.configure(text="Копировать адрес")
        except TclError:
            pass

    def open_browser(self):
        try:
            webbrowser.open(self.url)
        except Exception as exc:
            messagebox.showerror("FileDrop", "Не удалось открыть браузер:\n{}".format(exc))

    # Windows and lifecycle -------------------------------------------

    @staticmethod
    def _window_exists(window):
        try:
            return bool(window and window.winfo_exists())
        except TclError:
            return False

    def _focus_existing(self, attribute):
        window = getattr(self, attribute, None)
        if not self._window_exists(window):
            setattr(self, attribute, None)
            return False
        window.lift()
        window.focus_force()
        return True

    def _close_auxiliary_windows(self):
        for closer in (self._close_files, self._close_notes, self._close_settings):
            closer()

    def hide_to_tray(self):
        self.root.withdraw()
        if HAS_TRAY and not self.tray_icon:
            self._create_tray()

    def _create_tray(self):
        def on_show(icon, _item):
            self.root.after(0, self._show_window)

        def on_exit(icon, _item):
            icon.stop()
            self.root.after(0, self.on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Показать FileDrop", on_show),
            pystray.MenuItem("Выход", on_exit)
        )
        img = PILImage.new("RGBA", (64, 64), ACCENT)
        self.tray_icon = pystray.Icon("FileDrop", img, "FileDrop", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_mapped(self, _event=None):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None

    def persist_config(self):
        self.cfg["port"] = self.port
        self.cfg["folder"] = "" if self.temp_storage_var.get() else ServerState.upload_folder
        self.cfg["temporary_folder"] = bool(self.temp_storage_var.get())
        self.cfg["pin_on_start"] = bool(self.pin_on_start_var.get())
        self.cfg["theme"] = "light"
        save_config(self.cfg)

    def _cleanup_temp_folder(self):
        if self._temp_folder:
            cleanup_temporary_folder(self._temp_folder)
            self._temp_folder = None

    def on_close(self):
        self.persist_config()
        if self.notes_text:
            ServerState.notes = self.notes_text.get("0.0", "end")[:100000]
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self._cleanup_temp_folder()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    FileDropAppCTk().run()
