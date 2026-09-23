# Migrating from EasyEffects

pweq can take over if you use EasyEffects mainly for a parametric EQ,
optionally with a different preset per output ("autoload").

## 1. Import

GUI: **menu → Import EasyEffects presets**. CLI:

```sh
pweq import-easyeffects
```

This reads `~/.local/share/easyeffects/output/*.json` and the autoload rules in
`~/.local/share/easyeffects/autoload/output/`, then prints what it did:

```text
clean: no active equalizer, treated as 'no EQ'
porta pro: imported (Preamp -6.7 dB · 10 bands)
… Headphones: assigned 'porta pro'
```

What's converted:

| EasyEffects | pweq |
|---|---|
| Equalizer bands: Bell, Lo-shelf, Hi-shelf, Lo-pass, Hi-pass, Band-pass, Notch, All-pass | `PK`, `LSC`, `HSC`, `LP`, `HP`, `BP`, `NO`, `AP` |
| Input gain + output gain | `Preamp` |
| Muted bands | skipped |
| Bypassed equalizer, or preset without one | "no EQ" |
| Autoload rule (device → preset) | assignment |

Not converted (a warning names each case): plugins other than the equalizer,
band types without a biquad equivalent (e.g. Resonance), slopes other than
x1, and separate left/right curves (the left one is used for both).

Existing pweq presets with the same name are overwritten. Other assignments
are kept.

## 2. Turn EasyEffects off

Quit EasyEffects (including its background service) and stop it from starting
at login:

- EasyEffects → Preferences → uncheck *Launch service at system startup*, or
- `rm ~/.config/autostart/com.github.wwmm.easyeffects.desktop`

If you use the Flatpak, check `~/.var/app/com.github.wwmm.easyeffects/` instead.

## 3. Check

```sh
pweq status
```

Play something. The default output should be the `(EQ: …)` sink for your
current device.
