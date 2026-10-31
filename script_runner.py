import builtins
import keyword
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

if sys.platform.startswith("win"):
    MONO, UI = "Consolas", "Segoe UI"
elif sys.platform == "darwin":
    MONO, UI = "Menlo", "Helvetica Neue"
else:
    MONO, UI = "DejaVu Sans Mono", "DejaVu Sans"

C = {
    "bg": "#0f1117",
    "panel": "#161a23",
    "editor": "#1b1f2a",
    "border": "#262b38",
    "text": "#e4e7ef",
    "muted": "#6b7385",
    "accent": "#7c5cff",
    "accent_hover": "#9277ff",
    "danger": "#ef4f5f",
    "warn": "#f5b94a",
    "success": "#3ccf91",
    "button": "#232836",
    "button_hover": "#2d3344",
    "select": "#2f3650",
}

SYNTAX = {
    "keyword": "#c792ea",
    "builtin": "#82aaff",
    "defname": "#ffcb6b",
    "number": "#f78c6c",
    "string": "#c3e88d",
    "comment": "#5c6478",
}

_BUILTIN_NAMES = [name for name in dir(builtins) if not name.startswith("_")]

# Strings and comments come first so keywords inside them are not highlighted.
TOKEN_RE = re.compile(
    r"(?P<string>\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
    r"|(?P<comment>#[^\n]*)"
    r"|(?P<defname>(?<=\bdef )\w+|(?<=\bclass )\w+)"
    r"|(?P<keyword>\b(?:" + "|".join(keyword.kwlist) + r")\b)"
    r"|(?P<builtin>\b(?:" + "|".join(_BUILTIN_NAMES) + r")\b)"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)"
)

STARTER_CODE = '''# Welcome to PyRunner!
# Write some Python and press Execute (or Ctrl+Enter / F5).

def greet(name):
    return f"Hello, {name}!"

for name in ["Ada", "Linus", "Grace"]:
    print(greet(name))

print("2 ** 10 =", 2 ** 10)
'''


def make_button(parent, text, command, kind="default"):
    bg, hover, fg = {
        "primary": (C["accent"], C["accent_hover"], "#ffffff"),
        "danger": (C["button"], C["danger"], C["text"]),
        "default": (C["button"], C["button_hover"], C["text"]),
    }[kind]
    weight = "bold" if kind == "primary" else "normal"
    btn = tk.Label(
        parent, text=text, bg=bg, fg=fg, font=(UI, 10, weight),
        padx=14, pady=6, cursor="hand2",
    )
    btn.bind("<Button-1>", lambda _e: command())
    btn.bind("<Enter>", lambda _e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda _e: btn.config(bg=bg))
    return btn


class LineNumbers(tk.Canvas):
    def __init__(self, master, text_widget):
        super().__init__(master, width=48, bg=C["editor"], highlightthickness=0, bd=0)
        self.text_widget = text_widget

    def redraw(self):
        self.delete("all")
        current_line = self.text_widget.index("insert").split(".")[0]
        index = self.text_widget.index("@0,0")
        while True:
            info = self.text_widget.dlineinfo(index)
            if info is None:
                break
            line = index.split(".")[0]
            color = C["text"] if line == current_line else C["muted"]
            self.create_text(40, info[1], anchor="ne", text=line, fill=color, font=(MONO, 11))
            next_index = self.text_widget.index(f"{index}+1line")
            if next_index == index:
                break
            index = next_index


class EditorTab(tk.Frame):
    def __init__(self, master, name, on_change, on_cursor, shortcuts, path=None, content=""):
        super().__init__(master, bg=C["editor"])
        self.name = name
        self.path = path
        self.dirty = False
        self.on_change = on_change
        self._highlight_job = None

        self.text = tk.Text(
            self, bg=C["editor"], fg=C["text"], insertbackground=C["accent"],
            selectbackground=C["select"], selectforeground=C["text"],
            font=(MONO, 11), undo=True, maxundo=-1, autoseparators=True,
            wrap="none", bd=0, highlightthickness=0, padx=8, pady=6,
        )
        self.lines = LineNumbers(self, self.text)
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=lambda *a: self._on_yscroll(yscroll, *a), xscrollcommand=xscroll.set)

        self.lines.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        yscroll.grid(row=0, column=2, sticky="ns")
        xscroll.grid(row=1, column=1, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        for tag, color in SYNTAX.items():
            self.text.tag_configure(tag, foreground=color)

        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.highlight()

        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Tab>", self._insert_tab)
        self.text.bind("<Return>", self._newline)
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Configure>", lambda _e: self.lines.redraw())
        for sequence in ("<KeyRelease>", "<ButtonRelease-1>"):
            self.text.bind(sequence, lambda _e: (self.lines.redraw(), on_cursor(self)), add="+")
        # Bind app shortcuts on the widget itself so Text's built-in Ctrl+O / Ctrl+N bindings don't also fire.
        for sequence, callback in shortcuts.items():
            self.text.bind(sequence, lambda _e, cb=callback: (cb(), "break")[1])

    def display_name(self):
        return f"{self.name}  \u2022" if self.dirty else self.name

    def get_code(self):
        return self.text.get("1.0", "end-1c")

    def _on_yscroll(self, scrollbar, *args):
        scrollbar.set(*args)
        self.lines.redraw()

    def _on_modified(self, _event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        self.lines.redraw()
        if self._highlight_job:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(120, self.highlight)
        if not self.dirty:
            self.dirty = True
            self.on_change(self)

    def highlight(self):
        self._highlight_job = None
        content = self.get_code()
        for tag in SYNTAX:
            self.text.tag_remove(tag, "1.0", "end")
        for match in TOKEN_RE.finditer(content):
            self.text.tag_add(match.lastgroup, f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def _insert_tab(self, _event):
        self.text.insert("insert", "    ")
        return "break"

    def _newline(self, _event):
        line = self.text.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group(0)
        if line.rstrip().endswith(":"):
            indent += "    "
        self.text.insert("insert", "\n" + indent)
        self.text.see("insert")
        return "break"

    def _select_all(self, _event):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"


class ScriptRunnerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PyRunner")
        self.root.geometry("1120x720")
        self.root.minsize(820, 520)
        self.root.configure(bg=C["bg"])

        self.process = None
        self.stopped = False
        self.temp_path = None
        self.run_name = ""
        self.start_time = 0.0
        self.output_queue = queue.Queue()
        self.untitled_count = 0

        self.shortcuts = {
            "<Control-Return>": self.execute,
            "<F5>": self.execute,
            "<Shift-F5>": self.stop,
            "<Control-s>": self.save,
            "<Control-o>": self.open_file,
            "<Control-n>": self.new_tab,
            "<Control-w>": self.close_tab,
        }

        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        self._setup_styles()
        self._build_header()
        self._build_statusbar()
        self._build_body()

        for sequence, callback in self.shortcuts.items():
            self.root.bind(sequence, lambda _e, cb=callback: cb())

        self.new_tab(content=STARTER_CODE)
        self.refresh_scripts()
        self.log("PyRunner ready. Scripts run locally with " + os.path.basename(sys.executable) + ".", "info")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(40, self._poll_output)

    # ---------- UI construction ----------

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=C["panel"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "TNotebook.Tab", background=C["panel"], foreground=C["muted"],
            padding=(16, 7), borderwidth=0, font=(UI, 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", C["editor"]), ("active", C["button"])],
            foreground=[("selected", C["text"]), ("active", C["text"])],
        )
        style.layout("TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""}),
                ]}),
            ]}),
        ])
        for orient in ("Vertical", "Horizontal"):
            style.configure(
                f"{orient}.TScrollbar", background=C["button"], troughcolor=C["editor"],
                bordercolor=C["editor"], arrowcolor=C["muted"],
                lightcolor=C["button"], darkcolor=C["button"],
            )
            style.map(f"{orient}.TScrollbar", background=[("active", C["button_hover"])])

    def _build_header(self):
        header = tk.Frame(self.root, bg=C["panel"], padx=16, pady=10)
        header.pack(side="top", fill="x")

        tk.Label(header, text="\u25CF", fg=C["accent"], bg=C["panel"], font=(UI, 14)).pack(side="left")
        tk.Label(header, text="PyRunner", fg=C["text"], bg=C["panel"], font=(UI, 14, "bold")).pack(side="left", padx=(6, 10))
        tk.Label(header, text="Local Python script runner", fg=C["muted"], bg=C["panel"], font=(UI, 10)).pack(side="left")

        self.run_indicator = tk.Label(header, text="Idle", fg=C["muted"], bg=C["button"], font=(UI, 9, "bold"), padx=10, pady=3)
        self.run_indicator.pack(side="right")

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=C["panel"], padx=12, pady=4)
        bar.pack(side="bottom", fill="x")
        self.status_label = tk.Label(bar, text="Ready", fg=C["muted"], bg=C["panel"], font=(UI, 9))
        self.status_label.pack(side="left")
        tk.Label(
            bar, text="Ctrl+Enter run  \u00B7  Shift+F5 stop  \u00B7  Ctrl+S save  \u00B7  Ctrl+N new tab",
            fg=C["muted"], bg=C["panel"], font=(UI, 9),
        ).pack(side="left", padx=20)
        self.cursor_label = tk.Label(bar, text="Ln 1, Col 1", fg=C["muted"], bg=C["panel"], font=(UI, 9))
        self.cursor_label.pack(side="right")

    def _build_body(self):
        body = tk.PanedWindow(self.root, orient="horizontal", bg=C["border"], sashwidth=3, bd=0, sashrelief="flat")
        body.pack(fill="both", expand=True)

        main = tk.PanedWindow(body, orient="vertical", bg=C["border"], sashwidth=3, bd=0, sashrelief="flat")
        body.add(main, stretch="always", minsize=500)

        editor_area = tk.Frame(main, bg=C["panel"])
        self.notebook = ttk.Notebook(editor_area)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab_selected())
        self.notebook.bind("<Button-2>", self._middle_click_close)

        toolbar = tk.Frame(editor_area, bg=C["panel"], padx=10, pady=8)
        toolbar.pack(fill="x")
        self.execute_btn = make_button(toolbar, "\u25B6  Execute", self.execute, "primary")
        self.execute_btn.pack(side="left")
        make_button(toolbar, "\u25A0  Stop", self.stop, "danger").pack(side="left", padx=(6, 0))
        make_button(toolbar, "Clear", self.clear_editor).pack(side="left", padx=(6, 0))
        make_button(toolbar, "Open File", self.open_file).pack(side="left", padx=(6, 0))
        make_button(toolbar, "Save File", self.save).pack(side="left", padx=(6, 0))
        make_button(toolbar, "Close Tab", self.close_tab).pack(side="right")
        make_button(toolbar, "+  New Tab", self.new_tab).pack(side="right", padx=(0, 6))
        main.add(editor_area, stretch="always", minsize=260)

        console_area = tk.Frame(main, bg=C["panel"])
        console_header = tk.Frame(console_area, bg=C["panel"], padx=12, pady=6)
        console_header.pack(fill="x")
        tk.Label(console_header, text="CONSOLE", fg=C["muted"], bg=C["panel"], font=(UI, 9, "bold")).pack(side="left")
        clear_console = tk.Label(console_header, text="Clear output", fg=C["muted"], bg=C["panel"], font=(UI, 9), cursor="hand2")
        clear_console.pack(side="right")
        clear_console.bind("<Button-1>", lambda _e: self.clear_console())
        clear_console.bind("<Enter>", lambda _e: clear_console.config(fg=C["text"]))
        clear_console.bind("<Leave>", lambda _e: clear_console.config(fg=C["muted"]))

        console_body = tk.Frame(console_area, bg=C["bg"])
        console_body.pack(fill="both", expand=True)
        self.console = tk.Text(
            console_body, bg=C["bg"], fg=C["text"], font=(MONO, 10), wrap="word",
            bd=0, highlightthickness=0, padx=12, pady=8, state="disabled",
            selectbackground=C["select"],
        )
        console_scroll = ttk.Scrollbar(console_body, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=console_scroll.set)
        console_scroll.pack(side="right", fill="y")
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_configure("out", foreground=C["text"])
        self.console.tag_configure("err", foreground=C["danger"])
        self.console.tag_configure("info", foreground=C["accent_hover"])
        self.console.tag_configure("warn", foreground=C["warn"])
        self.console.tag_configure("success", foreground=C["success"])
        main.add(console_area, stretch="never", minsize=120, height=200)

        sidebar = tk.Frame(body, bg=C["panel"], padx=10, pady=10)
        tk.Label(sidebar, text="SCRIPTS", fg=C["muted"], bg=C["panel"], font=(UI, 9, "bold")).pack(anchor="w")
        tk.Label(sidebar, text="Double-click to open", fg=C["muted"], bg=C["panel"], font=(UI, 8)).pack(anchor="w", pady=(0, 8))
        self.script_list = tk.Listbox(
            sidebar, bg=C["editor"], fg=C["text"], font=(UI, 10), bd=0,
            highlightthickness=0, selectbackground=C["select"],
            selectforeground=C["text"], activestyle="none",
        )
        self.script_list.pack(fill="both", expand=True)
        self.script_list.bind("<Double-Button-1>", lambda _e: self._open_selected_script())
        self.script_list.bind("<Return>", lambda _e: self._open_selected_script())
        make_button(sidebar, "Refresh", self.refresh_scripts).pack(fill="x", pady=(8, 0))
        body.add(sidebar, stretch="never", minsize=180, width=220)

    # ---------- Tabs ----------

    def current_tab(self):
        selected = self.notebook.select()
        return self.notebook.nametowidget(selected) if selected else None

    def new_tab(self, path=None, content=""):
        if path:
            name = os.path.basename(path)
        else:
            self.untitled_count += 1
            name = f"Script {self.untitled_count}"
        tab = EditorTab(
            self.notebook, name, self._on_tab_dirty, self._update_cursor,
            self.shortcuts, path=path, content=content,
        )
        self.notebook.add(tab, text=tab.display_name())
        self.notebook.select(tab)
        tab.text.focus_set()
        return tab

    def close_tab(self, tab=None):
        tab = tab or self.current_tab()
        if tab is None:
            return
        if tab.dirty:
            answer = messagebox.askyesnocancel("Unsaved changes", f"Save changes to {tab.name}?")
            if answer is None:
                return
            if answer and not self.save(tab):
                return
        self.notebook.forget(tab)
        tab.destroy()
        if not self.notebook.tabs():
            self.new_tab()

    def _middle_click_close(self, event):
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.close_tab(self.notebook.nametowidget(self.notebook.tabs()[index]))

    def _on_tab_dirty(self, tab):
        self.notebook.tab(tab, text=tab.display_name())

    def _on_tab_selected(self):
        tab = self.current_tab()
        if tab:
            tab.text.focus_set()
            tab.lines.redraw()
            self._update_cursor(tab)

    def _update_cursor(self, tab):
        line, col = tab.text.index("insert").split(".")
        self.cursor_label.config(text=f"Ln {line}, Col {int(col) + 1}")

    def clear_editor(self):
        tab = self.current_tab()
        if tab:
            tab.text.delete("1.0", "end")
            tab.text.focus_set()

    # ---------- Files ----------

    def open_file(self, path=None):
        if not path:
            path = filedialog.askopenfilename(
                initialdir=SCRIPTS_DIR,
                filetypes=[("Python files", "*.py"), ("Text files", "*.txt"), ("All files", "*.*")],
            )
            if not path:
                return
        path = os.path.abspath(path)
        for tab_id in self.notebook.tabs():
            tab = self.notebook.nametowidget(tab_id)
            if tab.path == path:
                self.notebook.select(tab)
                return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            self.log(f"Could not open {path}: {exc}", "err")
            return
        self.new_tab(path=path, content=content)
        self.set_status(f"Opened {os.path.basename(path)}")

    def save(self, tab=None):
        tab = tab or self.current_tab()
        if tab is None:
            return False
        path = tab.path
        if not path:
            path = filedialog.asksaveasfilename(
                initialdir=SCRIPTS_DIR, initialfile=tab.name.replace(" ", "_").lower() + ".py",
                defaultextension=".py", filetypes=[("Python files", "*.py"), ("All files", "*.*")],
            )
            if not path:
                return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(tab.get_code())
        except OSError as exc:
            self.log(f"Could not save {path}: {exc}", "err")
            return False
        tab.path = os.path.abspath(path)
        tab.name = os.path.basename(path)
        tab.dirty = False
        self.notebook.tab(tab, text=tab.display_name())
        self.set_status(f"Saved {tab.name}")
        self.refresh_scripts()
        return True

    def refresh_scripts(self):
        self.script_list.delete(0, "end")
        try:
            files = sorted(f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".py"))
        except OSError:
            files = []
        for name in files:
            self.script_list.insert("end", "  " + name)
        if not files:
            self.script_list.insert("end", "  (no saved scripts)")

    def _open_selected_script(self):
        selection = self.script_list.curselection()
        if not selection:
            return
        name = self.script_list.get(selection[0]).strip()
        path = os.path.join(SCRIPTS_DIR, name)
        if os.path.isfile(path):
            self.open_file(path)

    # ---------- Console ----------

    def log(self, message, tag="out"):
        self.write_console(message + "\n", tag)

    def write_console(self, text, tag="out"):
        self.console.config(state="normal")
        self.console.insert("end", text, tag)
        self.console.config(state="disabled")
        self.console.see("end")

    def clear_console(self):
        self.console.config(state="normal")
        self.console.delete("1.0", "end")
        self.console.config(state="disabled")

    def set_status(self, text, color=None):
        self.status_label.config(text=text, fg=color or C["muted"])

    # ---------- Runner ----------

    def execute(self):
        if self.process:
            self.log("A script is already running. Stop it first.", "warn")
            return
        tab = self.current_tab()
        if tab is None:
            return
        code = tab.get_code()
        if not code.strip():
            self.log("Nothing to run: the editor is empty.", "warn")
            return

        fd, self.temp_path = tempfile.mkstemp(suffix=".py", prefix="pyrunner_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)

        cwd = os.path.dirname(tab.path) if tab.path else SCRIPTS_DIR
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", self.temp_path], cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1, **extra,
            )
        except OSError as exc:
            self.log(f"Failed to start Python: {exc}", "err")
            self._cleanup_temp()
            return

        self.stopped = False
        self.run_name = tab.name
        self.start_time = time.perf_counter()
        self.log(f"> Running {tab.name}", "info")
        self._set_running(True)

        proc = self.process
        readers = [
            threading.Thread(target=self._read_stream, args=(proc.stdout, "out"), daemon=True),
            threading.Thread(target=self._read_stream, args=(proc.stderr, "err"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        threading.Thread(target=self._wait_for_exit, args=(proc, readers), daemon=True).start()

    def stop(self):
        if not self.process:
            self.set_status("Nothing is running")
            return
        self.stopped = True
        try:
            self.process.kill()
        except OSError:
            pass

    def _read_stream(self, stream, tag):
        for line in iter(stream.readline, ""):
            self.output_queue.put((tag, line))
        stream.close()

    def _wait_for_exit(self, proc, readers):
        code = proc.wait()
        for reader in readers:
            reader.join()
        self.output_queue.put(("exit", code))

    def _poll_output(self):
        try:
            for _ in range(500):
                tag, payload = self.output_queue.get_nowait()
                if tag == "exit":
                    self._on_process_exit(payload)
                else:
                    if tag == "err" and self.temp_path:
                        payload = payload.replace(self.temp_path, self.run_name)
                    self.write_console(payload, tag)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_output)

    def _on_process_exit(self, code):
        elapsed = time.perf_counter() - self.start_time
        if self.stopped:
            self.log(f"Stopped after {elapsed:.2f}s", "warn")
        elif code == 0:
            self.log(f"Finished in {elapsed:.2f}s", "success")
        else:
            self.log(f"Exited with code {code} after {elapsed:.2f}s", "err")
        self.process = None
        self._cleanup_temp()
        self._set_running(False)

    def _cleanup_temp(self):
        if self.temp_path:
            try:
                os.remove(self.temp_path)
            except OSError:
                pass
            self.temp_path = None

    def _set_running(self, running):
        if running:
            self.run_indicator.config(text="Running", fg="#ffffff", bg=C["accent"])
            self.set_status(f"Running {self.run_name}...", C["accent_hover"])
        else:
            self.run_indicator.config(text="Idle", fg=C["muted"], bg=C["button"])
            self.set_status("Ready")

    def on_close(self):
        has_unsaved = any(self.notebook.nametowidget(t).dirty for t in self.notebook.tabs())
        if has_unsaved and not messagebox.askokcancel("Quit", "You have unsaved changes. Quit anyway?"):
            return
        if self.process:
            self.process.kill()
        self._cleanup_temp()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    ScriptRunnerApp(root)
    root.mainloop()
