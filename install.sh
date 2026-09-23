#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# pweq installer. Run ./install.sh --help for options.
set -eu

APP_ID=io.github.daer68.pweq
SRC=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

usage() {
    cat <<EOF
Usage: ./install.sh [options]

Install pweq for the current user (default) or system-wide. Re-running
upgrades an existing installation in place.

  --user          install into ~/.local (default)
  --system        install into /usr/local (see --prefix); needs root unless --destdir
  --prefix DIR    installation prefix
  --destdir DIR   stage files under DIR for packaging; skips checks and systemctl
  --no-enable     do not enable/start the systemd user service
  --uninstall     remove an installation (honours --user/--system/--prefix)
  --purge         with --uninstall: also delete your presets (~/.config/pweq)
  -h, --help      show this help
EOF
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }

mode=user prefix='' destdir='' enable=1 action=install purge=0
while [ $# -gt 0 ]; do
    case $1 in
        --user) mode=user ;;
        --system) mode=system ;;
        --prefix) [ $# -ge 2 ] || die "--prefix needs a value"; prefix=$2; shift ;;
        --prefix=*) prefix=${1#*=} ;;
        --destdir) [ $# -ge 2 ] || die "--destdir needs a value"; destdir=$2; shift ;;
        --destdir=*) destdir=${1#*=} ;;
        --no-enable) enable=0 ;;
        --uninstall) action=uninstall ;;
        --purge) purge=1 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

config_home=${XDG_CONFIG_HOME:-$HOME/.config}
if [ "$mode" = user ]; then
    prefix=${prefix:-$HOME/.local}
    unitdir=$config_home/systemd/user
else
    prefix=${prefix:-/usr/local}
    unitdir=$prefix/lib/systemd/user
fi
bindir=$prefix/bin
libdir=$prefix/lib/pweq
appsdir=$prefix/share/applications
icondir=$prefix/share/icons/hicolor/scalable/apps
units="pweq.service pweq-reload.path pweq-reload.service"
D=$destdir

# Run systemctl --user only for real (non-staged) per-user installs.
user_systemctl() {
    [ "$mode" = user ] && [ -z "$D" ] || return 0
    systemctl --user "$@" || warn "systemctl --user $* failed"
}

check_deps() {
    command -v python3 >/dev/null || die "python3 is required"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"
    for tool in pipewire pw-dump pw-metadata; do
        command -v "$tool" >/dev/null || die "'$tool' not found; install PipeWire and its command-line tools"
    done
    pw_major=$(pipewire --version 2>/dev/null | sed -n 's/.*libpipewire \([0-9]*\)\..*/\1/p' | head -n1)
    if [ -n "$pw_major" ] && [ "$pw_major" -lt 1 ]; then
        warn "PipeWire 1.0 or newer is recommended (found $(pipewire --version | tail -n1))"
    fi
    command -v wireplumber >/dev/null || warn "WirePlumber not found; automatic output switching needs it"
    if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); from gi.repository import Adw, Gtk" 2>/dev/null; then
        warn "GTK 4 / libadwaita Python bindings not found: the GUI will not start (CLI and daemon work)."
        warn "  Arch: pacman -S python-gobject gtk4 libadwaita"
        warn "  Debian/Ubuntu: apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
        warn "  Fedora: dnf install python3-gobject gtk4 libadwaita"
    fi
}

do_install() {
    if [ -z "$D" ]; then
        check_deps
        if [ "$mode" = system ] && [ "$(id -u)" -ne 0 ]; then
            die "--system needs root (or use --destdir for packaging)"
        fi
    fi

    install -d "$D$bindir" "$D$libdir" "$D$unitdir" "$D$appsdir" "$D$icondir"

    rm -rf "$D$libdir/pweq"
    cp -R "$SRC/pweq" "$D$libdir/pweq"
    find "$D$libdir/pweq" -name __pycache__ -prune -exec rm -rf {} +
    if command -v python3 >/dev/null; then
        python3 -m compileall -q -d "$libdir/pweq" "$D$libdir/pweq" >/dev/null || true
    fi

    cat >"$D$bindir/pweq" <<EOF
#!/bin/sh
PYTHONPATH="$libdir\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m pweq "\$@"
EOF
    chmod 755 "$D$bindir/pweq"

    sed "s|@BINDIR@|$bindir|g" "$SRC/data/pweq.service.in" >"$D$unitdir/pweq.service"
    chmod 644 "$D$unitdir/pweq.service"
    install -m644 "$SRC/data/pweq-reload.path" "$SRC/data/pweq-reload.service" "$D$unitdir/"
    install -m644 "$SRC/data/$APP_ID.desktop" "$D$appsdir/$APP_ID.desktop"
    install -m644 "$SRC/data/$APP_ID.svg" "$D$icondir/$APP_ID.svg"

    if [ -n "$D" ]; then
        info "pweq staged under $D$prefix"
        return
    fi
    if [ "$mode" = system ]; then
        info "pweq installed to $prefix. Each user enables it with:"
        info "  systemctl --user daemon-reload && systemctl --user enable --now pweq.service pweq-reload.path"
        return
    fi

    user_systemctl daemon-reload
    if [ "$enable" = 1 ]; then
        user_systemctl enable pweq.service pweq-reload.path
        user_systemctl restart pweq.service pweq-reload.path
    fi
    case ":$PATH:" in
        *":$bindir:"*) ;;
        *) warn "$bindir is not in your PATH" ;;
    esac
    info "pweq installed. Open the GUI with 'pweq' (or from your app menu), or run 'pweq status'."
    [ "$enable" = 1 ] || info "Enable the service later with: systemctl --user enable --now pweq.service pweq-reload.path"
}

do_uninstall() {
    if [ "$mode" = system ] && [ -z "$D" ] && [ "$(id -u)" -ne 0 ]; then
        die "--system --uninstall needs root"
    fi
    if [ "$mode" = user ] && [ -z "$D" ]; then
        # Stops the daemon, which moves the default output back off the EQ sink.
        systemctl --user disable --now pweq.service pweq-reload.path 2>/dev/null || true
    fi
    rm -rf "$D$libdir"
    rm -f "$D$bindir/pweq" "$D$appsdir/$APP_ID.desktop" "$D$icondir/$APP_ID.svg"
    for u in $units; do rm -f "$D$unitdir/$u"; done
    user_systemctl daemon-reload
    if [ "$purge" = 1 ]; then
        rm -rf "$config_home/pweq"
        info "pweq removed, including presets and assignments"
    else
        info "pweq removed (presets kept in $config_home/pweq; use --purge to delete them)"
    fi
}

if [ "$action" = install ]; then do_install; else do_uninstall; fi
