# SPDX-License-Identifier: GPL-3.0-or-later
"""GTK4 / libadwaita front end: pick a preset per output, manage presets."""

from __future__ import annotations

import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import apo, chain, config, pw, service  # noqa: E402

APP_ID = "io.github.daer68.pweq"
NO_EQ = "No EQ"


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="pweq", default_width=560, default_height=720)
        self.graph = pw.Graph()
        self.groups: list[Adw.PreferencesGroup] = []
        self._rebuild_pending = 0

        header = Adw.HeaderBar()
        import_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Import preset (.txt)")
        import_btn.connect("clicked", lambda *_: self.import_dialog())
        header.pack_start(import_btn)
        menu = Gio.Menu()
        menu.append("Refresh", "win.refresh")
        menu.append("Import EasyEffects presets", "win.import-ee")
        menu.append("Open presets folder", "win.open-folder")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        for name, cb in [
            ("refresh", lambda *_: self.rebuild()),
            ("import-ee", lambda *_: self.import_easyeffects()),
            ("open-folder", lambda *_: self.open_folder()),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        self.banner = Adw.Banner(title="Auto-follow service is not running — EQ is inactive", button_label="Start")
        self.banner.connect("button-clicked", lambda *_: self.start_service())

        self.page = Adw.PreferencesPage()
        self.toasts = Adw.ToastOverlay(child=self.page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.set_content(view)

        config.presets_dir().mkdir(parents=True, exist_ok=True)
        presets = Gio.File.new_for_path(str(config.presets_dir()))
        self.monitor = presets.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_: self.schedule_rebuild())
        self.rebuild()

    # --- building ------------------------------------------------------------

    def schedule_rebuild(self, delay_ms: int = 300) -> None:
        if self._rebuild_pending:
            GLib.source_remove(self._rebuild_pending)
        self._rebuild_pending = GLib.timeout_add(delay_ms, self._rebuild_timeout)

    def _rebuild_timeout(self) -> bool:
        self._rebuild_pending = 0
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def rebuild(self) -> None:
        try:
            self.graph = pw.dump()
        except (OSError, subprocess.CalledProcessError) as e:
            self.toast(f"Cannot read PipeWire graph: {e}")
        self.banner.set_revealed(not service.is_active())
        for g in self.groups:
            self.page.remove(g)
        self.groups = [self.outputs_group(), self.presets_group()]
        for g in self.groups:
            self.page.add(g)

    def outputs_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Outputs",
            description="Audio played on an output passes through its preset. "
            "The EQ follows the output you select in your usual volume menu.",
        )
        presets = list(config.list_presets())
        choices = [NO_EQ, *presets]
        assignments = config.load_assignments()
        names = self.graph.node_names()
        default = self.graph.default_sink()
        seen = set()

        outputs = self.graph.outputs()
        devices = sorted({o.device for o in outputs if o.device}, key=len, reverse=True)
        rows = []
        for out in outputs:
            a = config.match(assignments, out.name, out.description)
            if a:
                seen.add(a.name)
            rows.append((out.name, out.description, a, True, default == out.name))
        for a in assignments:
            if a.name not in seen:
                rows.append((a.name, a.description or a.name, a, False, False))

        for name, desc, a, connected, is_default in rows:
            model_choices = list(choices)
            if a and a.preset not in presets:
                model_choices.append(a.preset)  # keep a missing preset visible
            label = pw.short_label(desc, devices)
            row = Adw.ComboRow(title=GLib.markup_escape_text(label), model=Gtk.StringList.new(model_choices))
            row.set_selected(model_choices.index(a.preset) if a else 0)
            status = self._output_status(a, connected, is_default, names, default, presets)
            card = desc[: -len(label)].strip() if label != desc else ""
            row.set_subtitle(GLib.markup_escape_text(" · ".join(s for s in (card, status) if s)))
            row.connect("notify::selected", self.on_output_changed, name, desc, model_choices)
            group.add(row)
        if not rows:
            group.add(Adw.ActionRow(title="No outputs found"))
        return group

    @staticmethod
    def _output_status(a, connected, is_default, names, default, presets) -> str:
        parts = []
        if not connected:
            parts.append("not connected")
        if a:
            key = chain.slug(a.name)
            sink = chain.sink_name(key)
            if a.preset not in presets:
                parts.append("preset missing")
            elif sink == default:
                parts.append("EQ active, default output")
            elif sink in names and key in config.load_bypassed():
                parts.append("EQ off (selected without EQ; pick its “EQ” output to re-enable)")
            elif sink in names:
                parts.append("EQ ready")
            elif connected:
                parts.append("EQ not running")
        if is_default:
            parts.append("default output")
        return " · ".join(parts)

    def presets_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Presets",
            description="Equalizer APO / AutoEQ .txt files. Edits are applied when saved.",
        )
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Import preset", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.import_dialog())
        group.set_header_suffix(add)

        for name, path in config.list_presets().items():
            row = Adw.ExpanderRow(title=GLib.markup_escape_text(name))
            try:
                preset = apo.parse(path.read_text())
                row.set_subtitle(preset.summary())
                for band in preset.bands:
                    band_row = Adw.ActionRow(title=GLib.markup_escape_text(band.describe()))
                    band_row.add_css_class("monospace")
                    row.add_row(band_row)
            except (OSError, apo.ParseError) as e:
                row.set_subtitle(GLib.markup_escape_text(f"Error: {e}"))
                row.add_css_class("error")
            # add_suffix() packs right-to-left before the expander arrow: delete ends up last.
            for icon, tip, cb in [
                ("user-trash-symbolic", "Delete", self.confirm_delete),
                ("document-edit-symbolic", "Edit in text editor", self.edit_preset),
            ]:
                btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _b, n=name, f=cb: f(n))
                row.add_suffix(btn)
            group.add(row)
        if not config.list_presets():
            group.add(Adw.ActionRow(title="No presets yet", subtitle="Import an AutoEQ / APO .txt file"))
        return group

    # --- actions -------------------------------------------------------------

    def on_output_changed(self, row: Adw.ComboRow, _pspec, name: str, desc: str, choices: list[str]) -> None:
        preset = choices[row.get_selected()]
        items = config.set_assignment(config.load_assignments(), name, desc, None if preset == NO_EQ else preset)
        config.save_assignments(items)
        self.apply()

    def apply(self) -> None:
        if not service.reload_daemon():
            self.banner.set_revealed(True)
        self.schedule_rebuild(1200)  # let the daemon start/stop EQ sinks first

    def start_service(self) -> None:
        if err := service.enable_now():
            self.toast(err)
        self.schedule_rebuild(1200)

    def import_dialog(self) -> None:
        filt = Gtk.FileFilter(name="EQ presets (*.txt)")
        filt.add_pattern("*.txt")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog = Gtk.FileDialog(title="Import EQ preset", filters=filters, default_filter=filt)
        dialog.open(self, None, self._on_import_chosen)

    def _on_import_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            f = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        try:
            name = config.import_preset(f.get_path())
            self.toast(f"Imported {name}")
        except (OSError, apo.ParseError) as e:
            self.toast(f"Not imported: {e}")
        self.apply()

    def import_easyeffects(self) -> None:
        from . import easyeffects

        try:
            report = easyeffects.import_all()
        except (OSError, ValueError) as e:
            self.toast(f"EasyEffects import failed: {e}")
            return
        dialog = Adw.AlertDialog(heading="EasyEffects import", body="\n".join(report) or "Nothing found")
        dialog.add_response("ok", "OK")
        dialog.present(self)
        self.apply()

    def edit_preset(self, name: str) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(config.preset_path(name)))).launch(self, None, None)

    def open_folder(self) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(config.presets_dir()))).launch(self, None, None)

    def confirm_delete(self, name: str) -> None:
        users = [a.description or a.name for a in config.load_assignments() if a.preset == name]
        body = f"Outputs using it will play without EQ: {', '.join(users)}." if users else ""
        dialog = Adw.AlertDialog(heading=f"Delete preset “{name}”?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_response, name)
        dialog.present(self)

    def _on_delete_response(self, _dialog, response: str, name: str) -> None:
        if response != "delete":
            return
        config.delete_preset(name)
        config.save_assignments([a for a in config.load_assignments() if a.preset != name])
        self.apply()

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=5))


def main() -> int:
    Gtk.Window.set_default_icon_name(APP_ID)
    app = Adw.Application(application_id=APP_ID)
    app.connect("activate", lambda a: (a.get_active_window() or Window(a)).present())
    return app.run([sys.argv[0]])
