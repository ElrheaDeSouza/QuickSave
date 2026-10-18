import ctypes
import hashlib
import json
import msvcrt
import re
import sys
import os
import threading
import tkinter as tk

from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

import keyboard
import pyperclip
import pystray
import requests
from PIL import Image, ImageDraw

from dotenv import load_dotenv

load_dotenv()


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

WEBHOOK_URL = os.getenv("QUICKSAVE_WEBHOOK_URL")
HOTKEY = "ctrl+shift+s"

# Keep local data beside the script or packaged executable.
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)

LOCK_PATH = APP_DIR / ".quicksave.lock"
HISTORY_PATH = APP_DIR / ".quicksave_recent.json"
LOCAL_SAVES_PATH = APP_DIR / "quicksave_local_history.json"

MAX_RECENT_HASHES = 100
MAX_LOCAL_SAVES = 100

COLORS = {
    "background": "#F2FAF8",
    "surface": "#FFFFFF",
    "primary": "#146E69",
    "primary_hover": "#105853",
    "text": "#000000",
    "muted": "#718985",
    "border": "#D9EAE5",
    "accent": "#DDF5EF",
}


# --------------------------------------------------
# WINDOWS DPI AWARENESS
# --------------------------------------------------

def enable_dpi_awareness():
    """Improve Tkinter clarity on Windows high-DPI displays."""
    if sys.platform != "win32":
        return

    try:
        # Per-monitor DPI awareness on supported Windows versions.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def detect_type(text):
    text = text.strip()

    if re.match(r"^https?://\S+$", text, re.IGNORECASE):
        return "link"

    code_patterns = (
        "function ",
        "const ",
        "def ",
        "class ",
        "import ",
        "=>",
        "print(",
    )

    if any(pattern in text for pattern in code_patterns):
        return "code"

    return "text"


def content_fingerprint(content):
    """Hash content for duplicate detection without storing it in the hash file."""
    return hashlib.sha256(
        content.strip().encode("utf-8")
    ).hexdigest()


def create_icon():
    image = Image.new("RGB", (64, 64), COLORS["primary"])
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (7, 7, 57, 57),
        radius=13,
        fill="#FFFFFF",
    )

    draw.text(
        (15, 23),
        "QS",
        fill=COLORS["primary"],
    )

    return image


def read_json_list(path):
    """Read a JSON list safely; return an empty list if unavailable or invalid."""
    try:
        if not path.exists():
            return []

        data = json.loads(path.read_text(encoding="utf-8"))

        return data if isinstance(data, list) else []

    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read {path.name}: {exc}")
        return []


def acquire_single_instance_lock():
    """Prevent multiple QuickSave instances from running simultaneously."""
    lock_file = open(LOCK_PATH, "a+", encoding="utf-8")

    lock_file.seek(0)

    if not lock_file.read(1):
        lock_file.seek(0)
        lock_file.write("0")
        lock_file.flush()

    lock_file.seek(0)

    try:
        msvcrt.locking(
            lock_file.fileno(),
            msvcrt.LK_NBLCK,
            1,
        )
        return lock_file

    except OSError:
        lock_file.close()

        temp_root = tk.Tk()
        temp_root.withdraw()

        messagebox.showinfo(
            "QuickSave is already running",
            "QuickSave is already open in your system tray.",
            parent=temp_root,
        )

        temp_root.destroy()
        raise SystemExit(0)


# --------------------------------------------------
# QUICKSAVE APPLICATION
# --------------------------------------------------

class QuickSave:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("QuickSave")

        self.window = None
        self.recent_window = None
        self.icon = None
        self.saving = False
        self.shutting_down = False

        self.recent_hashes = self.load_history()
        self.local_saves = self.load_local_saves()
        self.filtered_recent_items = []

        self.configure_styles()
        self.register_hotkey()
        self.create_tray_icon()

    # --------------------------------------------------
    # STYLING
    # --------------------------------------------------

    def configure_styles(self):
        style = ttk.Style(self.root)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            font=("Segoe UI", 9),
            background=COLORS["background"],
            foreground=COLORS["text"],
        )

        style.configure(
            "App.TFrame",
            background=COLORS["background"],
        )

        style.configure(
            "Card.TFrame",
            background=COLORS["surface"],
        )

        style.configure(
            "Title.TLabel",
            font=("Segoe UI", 18, "bold"),
            background=COLORS["background"],
            foreground=COLORS["primary"],
        )

        style.configure(
            "Subtitle.TLabel",
            font=("Segoe UI", 9),
            background=COLORS["background"],
            foreground=COLORS["muted"],
        )

        style.configure(
            "Section.TLabel",
            font=("Segoe UI", 9, "bold"),
            background=COLORS["surface"],
            foreground=COLORS["text"],
        )

        style.configure(
            "Field.TLabel",
            font=("Segoe UI", 9, "bold"),
            background=COLORS["surface"],
            foreground=COLORS["muted"],
        )

        style.configure(
            "Status.TLabel",
            font=("Segoe UI", 9),
            background=COLORS["background"],
            foreground=COLORS["muted"],
        )

        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 9, "bold"),
            padding=(11, 6),
            background=COLORS["primary"],
            foreground="#FFFFFF",
            borderwidth=0,
        )

        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#A9C7C0"),
                ("active", COLORS["primary_hover"]),
            ],
            foreground=[("disabled", "#FFFFFF")],
        )

        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 9),
            padding=(9, 5),
            background=COLORS["accent"],
            foreground=COLORS["primary"],
            borderwidth=0,
        )

        style.map(
            "Secondary.TButton",
            background=[("active", COLORS["border"])],
        )

        style.configure(
            "TEntry",
            padding=5,
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
        )

        style.configure(
            "TCombobox",
            padding=4,
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
        )

        style.configure(
            "Recent.Treeview",
            font=("Segoe UI", 9),
            rowheight=25,
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
        )

        style.configure(
            "Recent.Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background=COLORS["accent"],
            foreground=COLORS["primary"],
            padding=5,
        )

        style.map(
            "Recent.Treeview",
            background=[("selected", COLORS["accent"])],
            foreground=[("selected", COLORS["text"])],
        )

    # --------------------------------------------------
    # SYSTEM TRAY AND HOTKEY
    # --------------------------------------------------

    def register_hotkey(self):
        keyboard.add_hotkey(
            HOTKEY,
            lambda: self.schedule_ui(self.open_window),
        )

    def create_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem(
                "Save clipboard",
                lambda icon, item: self.schedule_ui(
                    self.open_window
                ),
                default=True,
            ),
            pystray.MenuItem(
                "Recent Saves",
                lambda icon, item: self.schedule_ui(
                    self.open_recent_window
                ),
            ),
            pystray.MenuItem(
                "Quit",
                self.quit,
            ),
        )

        self.icon = pystray.Icon(
            "QuickSave",
            create_icon(),
            "QuickSave",
            menu,
        )

        threading.Thread(
            target=self.icon.run,
            daemon=True,
        ).start()

    def schedule_ui(self, callback):
        """Schedule UI work on Tkinter's main thread."""
        if self.shutting_down:
            return

        try:
            self.root.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    # --------------------------------------------------
    # DUPLICATE HISTORY
    # --------------------------------------------------

    def load_history(self):
        return {
            item
            for item in read_json_list(HISTORY_PATH)
            if isinstance(item, str)
        }

    def remember_saved_content(self, fingerprint):
        existing = [
            item
            for item in read_json_list(HISTORY_PATH)
            if isinstance(item, str)
        ]

        ordered = list(
            dict.fromkeys(existing + [fingerprint])
        )[-MAX_RECENT_HASHES:]

        try:
            HISTORY_PATH.write_text(
                json.dumps(ordered, indent=2),
                encoding="utf-8",
            )
            self.recent_hashes = set(ordered)

        except OSError as exc:
            print(f"Could not update duplicate history: {exc}")
            self.recent_hashes.add(fingerprint)

    # --------------------------------------------------
    # LOCAL SAVED-ITEM HISTORY
    # --------------------------------------------------

    def load_local_saves(self):
        items = read_json_list(LOCAL_SAVES_PATH)

        return [
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("content"), str)
        ][:MAX_LOCAL_SAVES]

    def remember_local_save(self, payload):
        """Store an item locally after the webhook confirms success."""
        item = {
            "title": payload.get("title") or "Untitled",
            "content": payload.get("content", ""),
            "type": payload.get("type", "text"),
            "category": payload.get("category", "General"),
            "tags": payload.get("tags", ""),
            "notes": payload.get("notes", ""),
            "saved_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        }

        self.local_saves.insert(0, item)
        self.local_saves = self.local_saves[:MAX_LOCAL_SAVES]

        try:
            LOCAL_SAVES_PATH.write_text(
                json.dumps(
                    self.local_saves,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        except OSError as exc:
            print(f"Could not write local saves: {exc}")

    # --------------------------------------------------
    # MAIN SAVE WINDOW
    # --------------------------------------------------

    def open_window(self):
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        try:
            clipboard = pyperclip.paste()
        except Exception:
            clipboard = ""

        win = tk.Toplevel(self.root)
        self.window = win

        win.title("QuickSave")
        win.geometry("540x600")
        win.minsize(480, 480)
        win.configure(bg=COLORS["background"])
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close_window)

        self.title = tk.StringVar(
            value=" ".join(clipboard.split())[:70]
        )
        self.kind = tk.StringVar(value=detect_type(clipboard))
        self.category = tk.StringVar(value="General")
        self.tags = tk.StringVar()
        self.notes = tk.StringVar()
        self.status = tk.StringVar(value="Ready when you are.")

        outer = ttk.Frame(
            win,
            style="App.TFrame",
            padding=(16, 12, 16, 12),
        )
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 7))

        ttk.Label(
            header,
            text="QuickSave",
            style="Title.TLabel",
        ).pack(side="left", anchor="w")

        ttk.Button(
            header,
            text="Recent Saves ↗",
            style="Secondary.TButton",
            command=self.open_recent_window,
        ).pack(side="right")

        ttk.Label(
            outer,
            text="A little place for things worth keeping.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        card = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=12,
        )
        card.pack(fill="both", expand=True)

        ttk.Label(
            card,
            text="YOUR SAVE",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(0, 7))

        ttk.Label(
            card,
            text="Title",
            style="Field.TLabel",
        ).pack(anchor="w")

        ttk.Entry(
            card,
            textvariable=self.title,
        ).pack(fill="x", pady=(3, 8))

        ttk.Label(
            card,
            text="Content",
            style="Field.TLabel",
        ).pack(anchor="w")

        text_frame = tk.Frame(
            card,
            bg=COLORS["border"],
            padx=1,
            pady=1,
        )
        text_frame.pack(fill="both", expand=True, pady=(3, 8))

        self.content = tk.Text(
            text_frame,
            height=3,
            wrap="word",
            font=("Segoe UI", 10),
            bg=COLORS["surface"],
            fg=COLORS["text"],
            insertbackground=COLORS["primary"],
            selectbackground=COLORS["accent"],
            selectforeground=COLORS["text"],
            relief="flat",
            padx=8,
            pady=8,
            undo=True,
        )
        self.content.pack(fill="both", expand=True)
        self.content.insert("1.0", clipboard)

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 8))

        left = ttk.Frame(row, style="Card.TFrame")
        left.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 6),
        )

        right = ttk.Frame(row, style="Card.TFrame")
        right.pack(side="left", fill="x", expand=True)

        ttk.Label(
            left,
            text="Type",
            style="Field.TLabel",
        ).pack(anchor="w")

        ttk.Combobox(
            left,
            textvariable=self.kind,
            values=("text", "link", "code", "note"),
            state="readonly",
        ).pack(fill="x", pady=(3, 0))

        ttk.Label(
            right,
            text="Category",
            style="Field.TLabel",
        ).pack(anchor="w")

        ttk.Combobox(
            right,
            textvariable=self.category,
            values=(
                "General",
                "Work",
                "Research",
                "Learning",
                "Code",
                "Ideas",
                "Personal",
            ),
        ).pack(fill="x", pady=(3, 0))

        ttk.Label(
            card,
            text="Tags · separated by commas",
            style="Field.TLabel",
        ).pack(anchor="w")

        ttk.Entry(
            card,
            textvariable=self.tags,
        ).pack(fill="x", pady=(3, 8))

        ttk.Label(
            card,
            text="Notes · optional",
            style="Field.TLabel",
        ).pack(anchor="w")

        ttk.Entry(
            card,
            textvariable=self.notes,
        ).pack(fill="x", pady=(3, 0))

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(9, 0))

        ttk.Label(
            footer,
            textvariable=self.status,
            style="Status.TLabel",
        ).pack(side="left", fill="x", expand=True)

        self.cancel_button = ttk.Button(
            footer,
            text="Cancel",
            style="Secondary.TButton",
            command=self.close_window,
        )
        self.cancel_button.pack(side="right", padx=(6, 0))

        self.save_button = ttk.Button(
            footer,
            text="Save to QuickSave  →",
            style="Primary.TButton",
            command=self.save,
        )
        self.save_button.pack(side="right")

    # --------------------------------------------------
    # SAVE AND DUPLICATE CHECK
    # --------------------------------------------------

    def save(self):
        if self.saving:
            return

        content = self.content.get("1.0", "end-1c").strip()

        if not content:
            messagebox.showwarning(
                "Nothing to save",
                "Add some content before saving.",
                parent=self.window,
            )
            return

        fingerprint = content_fingerprint(content)

        if fingerprint in self.recent_hashes:
            save_again = messagebox.askyesno(
                "Looks familiar! ♡",
                "You recently saved identical content.\n\n"
                "Would you like to save it again anyway?",
                parent=self.window,
            )

            if not save_again:
                self.status.set(
                    "Save cancelled. Your content is safe."
                )
                return

        # No ID is generated here.
        # The Google Sheets workflow handles the ID column.
        payload = {
            "title": self.title.get().strip() or "Untitled",
            "content": content,
            "type": self.kind.get(),
            "category": self.category.get().strip() or "General",
            "tags": self.tags.get().strip(),
            "source": "QuickSave Desktop",
            "fileUrl": "",
            "notes": self.notes.get().strip(),
        }

        self.saving = True
        self.status.set("Saving your item…")

        self.save_button.configure(
            state="disabled",
            text="Saving…",
        )
        self.cancel_button.configure(state="disabled")

        threading.Thread(
            target=self.send_to_n8n,
            args=(payload, fingerprint),
            daemon=True,
        ).start()

    def send_to_n8n(self, payload, fingerprint):
        try:
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                timeout=15,
            )
            response.raise_for_status()

            self.schedule_ui(
                lambda p=payload, f=fingerprint: self.saved(p, f)
            )

        except requests.RequestException as exc:
            error = str(exc)
            self.schedule_ui(lambda err=error: self.failed(err))

    def saved(self, payload, fingerprint):
        self.remember_saved_content(fingerprint)
        self.remember_local_save(payload)

        self.saving = False

        if self.window and self.window.winfo_exists():
            messagebox.showinfo(
                "Saved successfully!",
                "Your item was sent to n8n and added to local history.\n\n"
                "Open Recent Saves to find it again.",
                parent=self.window,
            )

        self.close_window()

        if self.recent_window and self.recent_window.winfo_exists():
            self.refresh_recent_list()

    def failed(self, error):
        self.saving = False

        if not (self.window and self.window.winfo_exists()):
            return

        self.save_button.configure(
            state="normal",
            text="Try saving again  →",
        )
        self.cancel_button.configure(state="normal")
        self.status.set("Couldn't save. Your content is still here.")

        messagebox.showerror(
            "Couldn't save this time",
            "Your content is still in the window.\n\n"
            f"Details: {error}\n\n"
            "Check that Docker Desktop and n8n are running, "
            "then try again.\n\n"
            "If the request timed out, check your Google Sheet "
            "before retrying: the server may have received it "
            "even though QuickSave didn't receive the response.",
            parent=self.window,
        )

    # --------------------------------------------------
    # RECENT SAVES WINDOW
    # --------------------------------------------------

    def open_recent_window(self):
        if self.recent_window and self.recent_window.winfo_exists():
            self.refresh_recent_list()
            self.recent_window.lift()
            self.recent_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.recent_window = win

        win.title("QuickSave · Recent Saves")
        win.geometry("760x560")
        win.minsize(590, 440)
        win.configure(bg=COLORS["background"])
        win.protocol("WM_DELETE_WINDOW", self.close_recent_window)

        outer = ttk.Frame(
            win,
            style="App.TFrame",
            padding=14,
        )
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 4))

        ttk.Label(
            header,
            text="Recent Saves",
            style="Title.TLabel",
        ).pack(side="left")

        ttk.Label(
            header,
            text=f"Up to {MAX_LOCAL_SAVES} items",
            style="Subtitle.TLabel",
        ).pack(side="right", anchor="s")

        ttk.Label(
            outer,
            text="Your saved snippets, ready to find and reuse.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(outer, style="App.TFrame")
        controls.pack(fill="x", pady=(0, 8))

        self.recent_search_var = tk.StringVar()
        self.recent_filter_var = tk.StringVar(value="All types")

        search = ttk.Entry(
            controls,
            textvariable=self.recent_search_var,
        )
        search.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 8),
        )

        filter_box = ttk.Combobox(
            controls,
            textvariable=self.recent_filter_var,
            values=("All types", "text", "link", "code", "note"),
            state="readonly",
            width=12,
        )
        filter_box.pack(side="right")

        list_frame = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=1,
        )
        list_frame.pack(fill="both", expand=True)

        columns = ("title", "type", "saved_at")

        self.recent_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Recent.Treeview",
        )

        self.recent_tree.heading("title", text="TITLE")
        self.recent_tree.heading("type", text="TYPE")
        self.recent_tree.heading("saved_at", text="SAVED AT")

        self.recent_tree.column("title", width=320, minwidth=150)
        self.recent_tree.column("type", width=75, minwidth=60)
        self.recent_tree.column("saved_at", width=155, minwidth=120)

        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.recent_tree.yview,
        )
        self.recent_tree.configure(yscrollcommand=scrollbar.set)

        self.recent_tree.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(6, 0),
            pady=6,
        )
        scrollbar.pack(side="right", fill="y", padx=(0, 6), pady=6)

        self.recent_tree.bind(
            "<<TreeviewSelect>>",
            self.show_recent_selection,
        )
        self.recent_tree.bind(
            "<Double-1>",
            lambda event: self.copy_selected_recent(),
        )

        self.recent_search_var.trace_add(
            "write",
            lambda *_: self.refresh_recent_list(),
        )
        filter_box.bind(
            "<<ComboboxSelected>>",
            lambda event: self.refresh_recent_list(),
        )

        preview_card = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=9,
        )
        preview_card.pack(fill="x", pady=(8, 0))

        ttk.Label(
            preview_card,
            text="CONTENT PREVIEW",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(0, 4))

        self.recent_preview = tk.Text(
            preview_card,
            height=3,
            wrap="word",
            font=("Segoe UI", 9),
            bg=COLORS["surface"],
            fg=COLORS["text"],
            relief="flat",
            padx=7,
            pady=5,
        )
        self.recent_preview.pack(fill="x")
        self.recent_preview.configure(state="disabled")

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(8, 0))

        self.recent_status = tk.StringVar(
            value="Select an item to preview it."
        )

        ttk.Label(
            footer,
            textvariable=self.recent_status,
            style="Status.TLabel",
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            footer,
            text="Refresh",
            style="Secondary.TButton",
            command=self.refresh_recent_list,
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            footer,
            text="Copy selected  ↗",
            style="Primary.TButton",
            command=self.copy_selected_recent,
        ).pack(side="right")

        self.refresh_recent_list()
        search.focus_set()

    def refresh_recent_list(self):
        if not (
            self.recent_window
            and self.recent_window.winfo_exists()
        ):
            return

        query = self.recent_search_var.get().strip().casefold()
        selected_type = self.recent_filter_var.get()

        for row in self.recent_tree.get_children():
            self.recent_tree.delete(row)

        self.filtered_recent_items = []

        for item in self.local_saves:
            title = str(item.get("title", "Untitled"))
            content = str(item.get("content", ""))
            item_type = str(item.get("type", "text"))

            if (
                selected_type != "All types"
                and item_type != selected_type
            ):
                continue

            if query and query not in f"{title}\n{content}".casefold():
                continue

            self.filtered_recent_items.append(item)

        for index, item in enumerate(self.filtered_recent_items):
            saved_at = str(item.get("saved_at", ""))

            try:
                parsed = datetime.fromisoformat(saved_at)
                saved_at = parsed.astimezone().strftime(
                    "%d %b %Y, %I:%M %p"
                )
            except (ValueError, TypeError, OverflowError):
                saved_at = saved_at or "Unknown"

            self.recent_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item.get("title", "Untitled"),
                    str(item.get("type", "text")).upper(),
                    saved_at,
                ),
            )

        count = len(self.filtered_recent_items)

        if count:
            self.recent_status.set(
                f"{count} item{'s' if count != 1 else ''} found."
            )

            first = self.recent_tree.get_children()[0]
            self.recent_tree.selection_set(first)
            self.recent_tree.focus(first)
            self.show_recent_selection()
        else:
            self.recent_status.set(
                "No saved items found. Save something to get started."
            )
            self.set_recent_preview("No items match your search.")

    def show_recent_selection(self, event=None):
        if not (
            self.recent_window
            and self.recent_window.winfo_exists()
        ):
            return

        selection = self.recent_tree.selection()

        if not selection:
            self.set_recent_preview(
                "Select an item to preview its content."
            )
            return

        try:
            item = self.filtered_recent_items[int(selection[0])]
        except (ValueError, IndexError):
            return

        content = str(item.get("content", ""))
        category = item.get("category", "General")
        tags = item.get("tags", "")

        details = f"Category: {category}"

        if tags:
            details += f"   ·   Tags: {tags}"

        self.set_recent_preview(f"{details}\n\n{content}")

    def set_recent_preview(self, text):
        if not (
            hasattr(self, "recent_preview")
            and self.recent_preview.winfo_exists()
        ):
            return

        self.recent_preview.configure(state="normal")
        self.recent_preview.delete("1.0", "end")
        self.recent_preview.insert("1.0", text)
        self.recent_preview.configure(state="disabled")

    def copy_selected_recent(self):
        if not (
            self.recent_window
            and self.recent_window.winfo_exists()
        ):
            return

        selection = self.recent_tree.selection()

        if not selection:
            messagebox.showinfo(
                "Choose an item",
                "Select a saved item first.",
                parent=self.recent_window,
            )
            return

        try:
            item = self.filtered_recent_items[int(selection[0])]
            pyperclip.copy(item.get("content", ""))

            self.recent_status.set(
                f"Copied '{item.get('title', 'Untitled')}' to clipboard."
            )

        except (ValueError, IndexError):
            self.recent_status.set("Could not find that saved item.")

        except Exception as exc:
            messagebox.showerror(
                "Clipboard error",
                f"Couldn't copy this item.\n\n{exc}",
                parent=self.recent_window,
            )

    def close_recent_window(self):
        if self.recent_window and self.recent_window.winfo_exists():
            self.recent_window.destroy()

        self.recent_window = None

    # --------------------------------------------------
    # WINDOW AND APP LIFECYCLE
    # --------------------------------------------------

    def close_window(self):
        if self.saving:
            return

        if self.window and self.window.winfo_exists():
            self.window.destroy()

        self.window = None

    def quit(self, icon=None, item=None):
        if self.shutting_down:
            return

        self.shutting_down = True

        def shutdown():
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass

            if self.icon:
                try:
                    self.icon.stop()
                except Exception:
                    pass

            if self.recent_window and self.recent_window.winfo_exists():
                self.recent_window.destroy()

            if self.window and self.window.winfo_exists():
                self.window.destroy()

            self.root.quit()
            self.root.destroy()

        try:
            self.root.after(0, shutdown)
        except (tk.TclError, RuntimeError):
            pass

    def run(self):
        self.root.mainloop()


# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    enable_dpi_awareness()
    instance_lock = acquire_single_instance_lock()

    try:
        app = QuickSave()
        app.run()

    finally:
        try:
            instance_lock.seek(0)
            msvcrt.locking(
                instance_lock.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        except OSError:
            pass

        instance_lock.close()