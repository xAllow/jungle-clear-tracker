"""Tkinter window: start/stop the tracker, choose the data folder, see your games and the map."""

import logging
import os
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from . import __version__, analysis, lol_api, mapview, settings, startup  # noqa: E402
from .tracker import Tracker  # noqa: E402

STATUS_COLORS = {"Stopped": "#95a5a6", "Waiting": "#3498db", "Recording": "#e74c3c", "In game": "#f39c12"}
SIDES = {"Both": "both", "Blue": "blue", "Red": "red"}
VIEWS = {"Heatmap": "heat", "At an instant": "instant", "Routes": "routes"}
WINDOWS = {"Start (0:00-1:05)": "start", "First clear": "clear", "After the clear": "post", "Custom": "custom"}


class QueueHandler(logging.Handler):
    """Log records from the tracker thread go through a queue: Tk may only be touched from its own thread."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(("log", self.format(record)))


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


class App:
    def __init__(self, root: tk.Tk, background: bool = False):
        self.root = root
        self.settings = settings.load()
        self.patch = settings.load_patch_data()
        self.events: queue.Queue = queue.Queue()
        self.tracker: Tracker | None = None
        self.routes: list[dict[str, Any]] = []
        self._redraw_job = None

        root.title(f"Jungle Clear Tracker {__version__}")
        # Sizes in physical pixels: scale them with the Windows display scaling (100%, 125%, 150%...).
        scale = root.winfo_fpixels("1i") / 96
        width = min(int(1100 * scale), int(root.winfo_screenwidth() * 0.92))
        height = min(int(760 * scale), int(root.winfo_screenheight() * 0.88))  # leave room for the taskbar
        root.geometry(f"{width}x{height}+{int(20 * scale)}+{int(10 * scale)}")
        root.minsize(min(int(900 * scale), width), min(int(560 * scale), height))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log = logging.getLogger("jct")
        self.log.setLevel(logging.INFO)
        self._setup_logging()
        self._build_header()
        self._build_tabs()
        self.reload_data()
        self.root.after(200, self._poll_events)

        if background:
            self.start_tracking()
            root.iconify()
        elif not self.settings.get("first_run_done"):
            self.root.after(300, self.open_setup)

    # ------------------------------------------------------------------------------------------ logging
    def _setup_logging(self) -> None:
        for h in list(self.log.handlers):
            self.log.removeHandler(h)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        handler = QueueHandler(self.events)
        handler.setFormatter(formatter)
        self.log.addHandler(handler)
        try:
            data_dir = Path(self.settings["data_dir"])
            data_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(data_dir / "tracker.log", encoding="utf-8")
            file_handler.setFormatter(formatter)
            self.log.addHandler(file_handler)
        except OSError:
            pass

    # ------------------------------------------------------------------------------------------- header
    def _build_header(self) -> None:
        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill="x")

        self.dot = tk.Canvas(top, width=14, height=14, highlightthickness=0)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=STATUS_COLORS["Stopped"], outline="")
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).grid(row=0, column=1, sticky="w")
        self.toggle_btn = ttk.Button(top, text="Start", command=self.toggle_tracking, width=12)
        self.toggle_btn.grid(row=0, column=2, padx=12)
        ttk.Button(top, text="Setup check...", command=self.open_setup).grid(row=0, column=3)
        top.columnconfigure(4, weight=1)

        ttk.Label(top, text="Data folder:").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.folder_var = tk.StringVar(value=self.settings["data_dir"])
        ttk.Entry(top, textvariable=self.folder_var, state="readonly").grid(
            row=1, column=2, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(top, text="Change...", command=self.choose_folder).grid(row=1, column=5, padx=(6, 0), pady=(8, 0))
        ttk.Button(top, text="Open", command=self.open_folder).grid(row=1, column=6, padx=(6, 0), pady=(8, 0))

        options = ttk.Frame(top)
        options.grid(row=2, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self.replay_var = tk.BooleanVar(value=self.settings.get("replay", True))
        ttk.Checkbutton(options, text="Record positions from the replay after each game (camp order, scuttles, map)",
                        variable=self.replay_var, command=self.save_options).pack(side="left")
        self.autostart_var = tk.BooleanVar(value=startup.is_enabled())
        ttk.Checkbutton(options, text="Start with Windows", variable=self.autostart_var,
                        command=self.toggle_autostart).pack(side="left", padx=(16, 0))

    # --------------------------------------------------------------------------------------------- tabs
    def _build_tabs(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._build_games_tab(notebook)
        self._build_map_tab(notebook)
        self._build_log_tab(notebook)

    def _build_games_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Games")

        self.kpi_vars: dict[str, tk.StringVar] = {}
        kpis = ttk.Frame(tab)
        kpis.pack(fill="x")
        for i, name in enumerate(("Games", "Clean clears", "Average clear", "Best clear", "Win rate", "Early scuttles")):
            box = ttk.Frame(kpis, padding=(0, 0, 24, 6))
            box.grid(row=0, column=i, sticky="w")
            ttk.Label(box, text=name.upper(), foreground="#7f8c8d", font=("Segoe UI", 8)).pack(anchor="w")
            self.kpi_vars[name] = tk.StringVar(value="-")
            ttk.Label(box, textvariable=self.kpi_vars[name], font=("Segoe UI", 16, "bold")).pack(anchor="w")

        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(6, 0))

        columns = ("date", "champion", "vs", "side", "status", "clear", "route", "scuttles", "result")
        titles = ("Date", "Champion", "vs", "Side", "Status", "Clear end", "Route", "Scuttles", "Result")
        widths = (140, 95, 95, 50, 110, 75, 330, 70, 70)
        games = ttk.Frame(panes)
        self.games_tree = ttk.Treeview(games, columns=columns, show="headings", height=10)
        for c, t, w in zip(columns, titles, widths):
            self.games_tree.heading(c, text=t)
            self.games_tree.column(c, width=w, anchor="w", stretch=c == "route")
        scroll = ttk.Scrollbar(games, orient="vertical", command=self.games_tree.yview)
        self.games_tree.configure(yscrollcommand=scroll.set)
        self.games_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        panes.add(games, weight=3)

        breakdown = ttk.Frame(panes)
        ttk.Label(breakdown, text="Win rate by route, side of the clear and early scuttles "
                                  "(only games with a known result; few games = noisy)",
                  foreground="#7f8c8d").pack(anchor="w", pady=(8, 2))
        cols = ("group", "games", "winrate", "avg_clear")
        self.breakdown_tree = ttk.Treeview(breakdown, columns=cols, show="headings", height=7)
        for c, t, w in zip(cols, ("Group", "Games", "Win rate", "Average clear"), (480, 70, 120, 110)):
            self.breakdown_tree.heading(c, text=t)
            self.breakdown_tree.column(c, width=w, anchor="w", stretch=c == "group")
        self.breakdown_tree.pack(fill="both", expand=True)
        panes.add(breakdown, weight=2)

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Re-analyze all games", command=self.reanalyze).pack(side="left")
        ttk.Button(buttons, text="Reload", command=self.reload_data).pack(side="left", padx=6)

    def _build_map_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Map")
        controls = ttk.Frame(tab)
        controls.pack(fill="x")

        def combo(label: str, values: list[str], default: str, column: int) -> tk.StringVar:
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w", padx=(0, 4))
            var = tk.StringVar(value=default)
            box = ttk.Combobox(controls, textvariable=var, values=values, state="readonly", width=16)
            box.grid(row=0, column=column + 1, padx=(0, 12))
            box.bind("<<ComboboxSelected>>", lambda _e: self.schedule_redraw())
            return var

        self.side_var = combo("Side", list(SIDES), "Both", 0)
        self.view_var = combo("View", list(VIEWS), "Heatmap", 2)
        self.window_var = combo("Window", list(WINDOWS), "First clear", 4)
        self.player_var = combo("Player", ["Me", "Enemy jungler"], "Me", 6)
        self.compare_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Wins vs losses", variable=self.compare_var,
                        command=self.schedule_redraw).grid(row=0, column=8)

        sliders = ttk.Frame(tab)
        sliders.pack(fill="x", pady=(8, 0))
        self.instant_var = tk.DoubleVar(value=180)
        self.from_var = tk.DoubleVar(value=0)
        self.to_var = tk.DoubleVar(value=300)
        self.slider_label = tk.StringVar()
        for label, var in (("Instant", self.instant_var), ("From", self.from_var), ("To", self.to_var)):
            ttk.Label(sliders, text=label).pack(side="left", padx=(0, 4))
            ttk.Scale(sliders, from_=0, to=300, variable=var, length=170,
                      command=lambda _v: self.schedule_redraw()).pack(side="left", padx=(0, 14))
        ttk.Label(sliders, textvariable=self.slider_label, foreground="#7f8c8d").pack(side="left")

        self.figure = Figure(figsize=(7, 7), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, pady=(8, 0))

    def _build_log_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Log")
        self.log_text = ScrolledText(tab, height=20, state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    # -------------------------------------------------------------------------------------- tracker
    def toggle_tracking(self) -> None:
        if self.tracker and self.tracker.is_alive():
            self.stop_tracking()
        else:
            self.start_tracking()

    def start_tracking(self) -> None:
        if self.tracker and self.tracker.is_alive():
            return
        self.settings["replay"] = self.replay_var.get()
        self.tracker = Tracker(dict(self.settings), self.patch, self.log,
                               on_status=lambda s: self.events.put(("status", s)),
                               on_new_summary=lambda: self.events.put(("reload", None)))
        self.tracker.start()
        self.toggle_btn.configure(text="Stop")
        self._set_status("Starting")

    def stop_tracking(self) -> None:
        if self.tracker:
            self.tracker.stop()
        self.toggle_btn.configure(text="Start")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        color = next((c for key, c in STATUS_COLORS.items() if text.startswith(key)), "#3498db")
        self.dot.itemconfigure(self.dot_id, fill=color)

    def _poll_events(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._set_status(value)
                if value == "Stopped":
                    self.toggle_btn.configure(text="Start")
            elif kind == "log":
                self.log_text.configure(state="normal")
                self.log_text.insert("end", value + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            elif kind == "reload":
                self.reload_data()
        self.root.after(300, self._poll_events)

    # ------------------------------------------------------------------------------------------ options
    def save_options(self) -> None:
        self.settings["replay"] = self.replay_var.get()
        settings.save(self.settings)
        if self.tracker and self.tracker.is_alive():
            self.tracker.settings["replay"] = self.settings["replay"]

    def toggle_autostart(self) -> None:
        try:
            if self.autostart_var.get():
                startup.enable()
            else:
                startup.disable()
        except Exception as e:  # noqa: BLE001 - show any failure to the user
            messagebox.showerror("Start with Windows", f"Could not change it: {e}")
            self.autostart_var.set(startup.is_enabled())

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.settings["data_dir"], title="Folder for captures and summaries")
        if not chosen:
            return
        running = bool(self.tracker and self.tracker.is_alive())
        if running:
            self.stop_tracking()
            self.tracker.join(timeout=10)
        self.settings["data_dir"] = chosen
        settings.save(self.settings)
        self.folder_var.set(chosen)
        self._setup_logging()
        self.reload_data()
        if running:
            self.start_tracking()

    def open_folder(self) -> None:
        folder = Path(self.settings["data_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606 - Windows only, opens Explorer

    # --------------------------------------------------------------------------------------------- data
    def reload_data(self) -> None:
        data_dir = Path(self.settings["data_dir"])
        rows = analysis.load_summaries(data_dir)
        self._fill_games(rows)
        self.routes = analysis.load_routes(data_dir) if data_dir.exists() else []
        self.schedule_redraw()

    def reanalyze(self) -> None:
        lol_dir = lol_api.find_lol_dir(self.settings.get("lol_dir", ""))
        rows = analysis.analyze_folder(Path(self.settings["data_dir"]), lol_dir, self.patch)
        self.log.info("Re-analyzed %d game(s).", len(rows))
        self.reload_data()

    def _fill_games(self, rows: list[dict[str, Any]]) -> None:
        self.games_tree.delete(*self.games_tree.get_children())
        for r in reversed(rows):
            result = {True: "Win", False: "Loss"}.get(r.get("win"), "-")
            route = r.get("route") or "-"
            if r.get("route") and r.get("route_reliable") is False:
                route += " (approx.)"
            self.games_tree.insert("", "end", values=(
                (r.get("date") or "").replace("T", " ")[:16], r.get("champion") or "-", r.get("enemy_jungler") or "-",
                (r.get("side") or "-").title(), analysis.STATUS_TEXT.get(r.get("status"), r.get("status") or "-"),
                r.get("clear_end_mmss") or "-", route,
                "-" if r.get("early_scuttles") is None else r["early_scuttles"], result,
            ))
        clean = [r for r in rows if r.get("status") == "clean" and r.get("clear_end") is not None]
        known = [r for r in rows if r.get("win") is not None]
        with_scuttles = [r for r in rows if r.get("early_scuttles") is not None]
        self.kpi_vars["Games"].set(str(len(rows)))
        self.kpi_vars["Clean clears"].set(str(len(clean)))
        self.kpi_vars["Average clear"].set(
            analysis.mmss(sum(r["clear_end"] for r in clean) / len(clean)) if clean else "-")
        self.kpi_vars["Best clear"].set(analysis.mmss(min(r["clear_end"] for r in clean)) if clean else "-")
        self.kpi_vars["Win rate"].set(
            f"{100 * sum(r['win'] for r in known) / len(known):.0f}%" if known else "-")
        self.kpi_vars["Early scuttles"].set(
            f"{sum(r['early_scuttles'] for r in with_scuttles) / len(with_scuttles):.1f}" if with_scuttles else "-")

        self.breakdown_tree.delete(*self.breakdown_tree.get_children())
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            keys = []
            if r.get("route"):
                keys.append(f"Route: {r['route']}")
            if r.get("starts"):
                keys.append(f"Starts {r['starts']} side")
            if r.get("ends"):
                keys.append(f"Ends {r['ends']} side")
            if r.get("early_scuttles") is not None:
                keys.append(f"{r['early_scuttles']} early scuttle(s)")
            for k in keys:
                groups.setdefault(k, []).append(r)
        for key, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            decided = [r for r in items if r.get("win") is not None]
            wins = sum(r["win"] for r in decided)
            clean_items = [r for r in items if r.get("status") == "clean" and r.get("clear_end") is not None]
            self.breakdown_tree.insert("", "end", values=(
                key, len(items),
                f"{100 * wins / len(decided):.0f}% ({wins}-{len(decided) - wins})" if decided else "-",
                analysis.mmss(sum(r["clear_end"] for r in clean_items) / len(clean_items)) if clean_items else "-",
            ))

    # ---------------------------------------------------------------------------------------------- map
    def schedule_redraw(self) -> None:
        if self._redraw_job:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(150, self.redraw_map)

    def redraw_map(self) -> None:
        self._redraw_job = None
        start, end = sorted((self.from_var.get(), self.to_var.get()))
        view, window = VIEWS[self.view_var.get()], WINDOWS[self.window_var.get()]
        if view == "instant":
            self.slider_label.set(f"Showing everyone's position at {mmss(self.instant_var.get())}")
        elif window == "custom":
            self.slider_label.set(f"Custom window {mmss(start)} - {mmss(end)}")
        else:
            self.slider_label.set(mapview.WINDOWS[window])
        mapview.draw(self.figure, self.routes, side=SIDES[self.side_var.get()], view=view, window=window,
                     custom=(start, end), instant=self.instant_var.get(),
                     player="me" if self.player_var.get() == "Me" else "rival", compare=self.compare_var.get())
        self.canvas.draw_idle()

    # -------------------------------------------------------------------------------------------- setup
    def open_setup(self) -> None:
        SetupDialog(self)

    def on_close(self) -> None:
        if self.tracker and self.tracker.is_alive():
            if not messagebox.askyesno("Jungle Clear Tracker", "Tracking is running. Stop it and exit?"):
                return
            self.tracker.stop()
        self.root.destroy()


class SetupDialog:
    """First-run / setup check: client, install folder, account, Replay API and data folder."""

    def __init__(self, app: App):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Setup check")
        self.win.resizable(False, False)  # sized to its content
        self.win.transient(app.root)
        self.frame = ttk.Frame(self.win, padding=14)
        self.frame.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        s = self.app.settings
        lol_dir = lol_api.find_lol_dir(s.get("lol_dir", ""))
        installed = (lol_dir / "Config").exists()
        client_open = lol_api.client(lol_dir) is not None
        riot_id = lol_api.current_riot_id(lol_dir) if client_open else None
        replay_ok = lol_api.replay_api_enabled(lol_dir)
        try:
            Path(s["data_dir"]).mkdir(parents=True, exist_ok=True)
            writable = os.access(s["data_dir"], os.W_OK)
        except OSError:
            writable = False

        ttk.Label(self.frame, text="Everything should be green before playing.",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        rows = [
            (installed, "League of Legends folder", str(lol_dir) if installed else "Not found",
             ("Browse...", self.browse_lol)),
            (client_open, "League client running", "Open" if client_open else "Open the League client", None),
            (bool(riot_id), "Account", riot_id or "Detected once the client is open", None),
            (replay_ok, "Replay API (needed for positions)",
             "Enabled" if replay_ok else "Disabled: adds EnableReplayApi=1 to game.cfg (a backup is kept)",
             None if replay_ok else ("Enable", self.enable_replay)),
            (writable, "Data folder", s["data_dir"], None),
        ]
        for i, (ok, name, detail, action) in enumerate(rows, start=1):
            tk.Label(self.frame, text="✔" if ok else "✘", fg="#27ae60" if ok else "#c0392b",
                     font=("Segoe UI", 12, "bold")).grid(row=i, column=0, sticky="nw", padx=(0, 8), pady=4)
            box = ttk.Frame(self.frame)
            box.grid(row=i, column=1, sticky="w", pady=4)
            ttk.Label(box, text=name, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            ttk.Label(box, text=detail, foreground="#7f8c8d", wraplength=440).pack(anchor="w")
            if action:
                ttk.Button(self.frame, text=action[0], command=action[1]).grid(row=i, column=2, sticky="e", padx=(10, 0))
        self.frame.columnconfigure(1, weight=1)
        ttk.Label(self.frame, foreground="#7f8c8d", wraplength=620, text=(
            "Only the game's local APIs are used (Live Client Data, Replay API and the client API). Nothing is "
            "shown or changed during your games; replays are opened after the game, only while you are out of queue."
        )).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=(14, 8))
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=len(rows) + 2, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Check again", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.close).pack(side="left")

    def browse_lol(self) -> None:
        chosen = filedialog.askdirectory(title="League of Legends folder (the one containing 'Config')")
        if chosen:
            self.app.settings["lol_dir"] = chosen
            settings.save(self.app.settings)
            self.refresh()

    def enable_replay(self) -> None:
        lol_dir = lol_api.find_lol_dir(self.app.settings.get("lol_dir", ""))
        if not messagebox.askyesno("Replay API", "Add EnableReplayApi=1 to game.cfg? A backup (game.cfg.bak) is kept.",
                                   parent=self.win):
            return
        messagebox.showinfo("Replay API", lol_api.enable_replay_api(lol_dir), parent=self.win)
        self.refresh()

    def close(self) -> None:
        self.app.settings["first_run_done"] = True
        settings.save(self.app.settings)
        self.win.destroy()


def main(background: bool = False) -> None:
    try:
        # Crisp text on scaled displays instead of Windows stretching a blurry bitmap.
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root, background=background)
    root.mainloop()
