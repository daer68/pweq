# Preset format

pweq presets are plain text in the format used by
[Equalizer APO](https://sourceforge.net/projects/equalizerapo/) and exported
by [AutoEQ](https://github.com/jaakkopasanen/AutoEq) (`ParametricEQ.txt`),
[squig.link](https://squig.link) and REW.

```text
Preamp: -6.8 dB
Filter 1: ON PK Fc 21 Hz Gain 6.7 dB Q 1.100
Filter 2: ON LSC Fc 105 Hz Gain 7.6 dB Q 0.70
Filter 3: OFF HSC Fc 10000 Hz Gain 4.7 dB Q 0.70
Filter 4: ON HP Fc 20 Hz Q 0.707
```

Presets live in `~/.config/pweq/presets/`; the file name (without `.txt`) is
the preset name. Import with the GUI's **+** button or `pweq import FILE [NAME]`.
Either way the file is validated first. You can also drop files into the folder
directly; the GUI marks broken ones and shows the error.

## Syntax

- `Preamp: <dB> dB`: gain applied before the filters. Several preamp lines add up.
- `Filter [N]: ON|OFF <TYPE> Fc <Hz> Hz [Gain <dB> dB] [Q <q>]`
  - The number after `Filter` is optional and ignored; filters apply in file order.
  - `OFF` filters are kept in the file but not applied.
  - `Hz` / `dB` units are optional. Keywords are case-insensitive.
  - `Q` defaults to 0.707 when omitted.
  - `Gain` is required for peaking and shelf filters and ignored for the others.
- `#` starts a comment. Blank lines are ignored.
- Anything else is an error, reported with its line number.

## Filter types

| Type | Meaning | PipeWire builtin |
|---|---|---|
| `PK`, `PEQ` | peaking (bell) | `bq_peaking` |
| `LSC`, `LS` | low shelf | `bq_lowshelf` |
| `HSC`, `HS` | high shelf | `bq_highshelf` |
| `LP`, `LPQ` | low-pass | `bq_lowpass` |
| `HP`, `HPQ` | high-pass | `bq_highpass` |
| `BP` | band-pass | `bq_bandpass` |
| `NO` | notch | `bq_notch` |
| `AP` | all-pass | `bq_allpass` |

Every filter is a second-order (12 dB/oct) biquad. Equalizer APO's variable-slope
shelves (`LS 6dB`, `HS 12dB`…) and graphic EQ lines are not supported.

## Avoiding clipping

Boosting any band can push loud passages past 0 dBFS, which distorts. Set
`Preamp` to at least minus your largest positive gain, e.g. a +6 dB bass
shelf wants `Preamp: -6 dB`. AutoEQ exports already include the right preamp.

## Where to get presets

- **Headphones and IEMs:** [autoeq.app](https://autoeq.app) or the
  [AutoEQ results](https://github.com/jaakkopasanen/AutoEq/tree/master/results);
  choose *Parametric EQ* / *Equalizer APO* and download `ParametricEQ.txt`.
  [squig.link](https://squig.link) exports the same format.
- **Speakers and rooms:** measure with [REW](https://www.roomeqwizard.com/)
  and export filters as *Equalizer APO* text.
- **By hand:** start from [`examples/template.txt`](../examples/template.txt).
