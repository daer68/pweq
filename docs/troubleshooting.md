# Troubleshooting

Start with:

```sh
pweq status
systemctl --user status pweq
journalctl --user -u pweq -b
```

`pweq status` marks the default output with `*` and shows, for each output
with a preset, whether its EQ sink is `active` or `not running`.

## The GUI says "Auto-follow service is not running"

Click **Start**, or run:

```sh
systemctl --user enable --now pweq.service pweq-reload.path
```

If it keeps failing, `journalctl --user -u pweq -b` shows why. The most common
cause is a missing `pw-dump` or `pw-metadata` (install your distribution's
PipeWire tools package).

## An output has a preset but its EQ is "not running"

- The preset file has an error. The GUI shows it under the preset, and
  `journalctl --user -u pweq` logs `preset '…' skipped: line N: …`.
- The output was renamed. Re-select the preset in the GUI.

## Audio doesn't go through the EQ

- Check the default output: `wpctl status` (the `*` in *Sinks*). It should be
  the `(EQ: …)` sink.
- An app may be pinned to a specific output (WirePlumber remembers per-app
  choices you made in pavucontrol and similar tools). Move that app to the EQ
  sink once in pavucontrol, or clear its remembered target.
- EasyEffects is still running and grabbing every stream. Quit it; see
  [Migrating from EasyEffects](migrating-from-easyeffects.md).

## Audio is equalized twice / sounds wrong

EasyEffects (or another EQ) is still active. pweq can't create a feedback
loop with it, but both EQs apply. Quit the other one.

## Distortion on loud passages

The preset boosts without enough negative preamp. See
[Avoiding clipping](presets.md#avoiding-clipping).

## AirPlay speaker lost its preset

pweq matches by node name, then by unique description. If two outputs share a
description (two speakers named "Living Room"), the fallback can't choose and
the preset isn't applied. Rename one speaker, or re-select the preset after
the IP change.

## Reset everything

```sh
systemctl --user stop pweq
rm -rf ~/.config/pweq                 # presets and assignments
systemctl --user start pweq
```

Stopping the service always moves the default output back to the real device.
