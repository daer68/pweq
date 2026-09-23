# How it works

```text
          app ──▶ Speaker (EQ: laptop-speakers) ──▶ Speaker
                   └─ pipewire -c pweq/<speaker>.conf
                      linear (preamp) → biquad → biquad → …

 pweq daemon ◀── pw-dump --monitor (graph events)
     │
     ├─ starts/stops one EQ process per connected output that has a preset
     └─ default output = real output with a preset?  →  switch default to its EQ sink
```

## Components

| Piece | Role |
|---|---|
| `pweq daemon` (`pweq.service`) | Watches the PipeWire graph, runs the EQ processes, redirects the default output. |
| EQ process | `pipewire -c $XDG_RUNTIME_DIR/pweq/<output>.conf`: a standalone PipeWire client that loads `libpipewire-module-filter-chain`. |
| `pweq-reload.path` | systemd path unit: reloads the daemon when `~/.config/pweq/presets/` or `assignments.json` change. |
| GUI / CLI | Edit presets and assignments, then ask the daemon to reload. They never touch audio directly. |

### The EQ sink

Each generated config holds one filter-chain with:

- a **capture** side: a virtual sink named `pweq.<output-slug>` with the
  description `<Output> (EQ: <preset>)` and `priority.session = 0`, so
  WirePlumber never picks it as a fallback on its own.
- a filter graph: `linear` (preamp, `Mult = 10^(dB/20)`) followed by one
  builtin biquad per enabled band. filter-chain duplicates the graph per channel.
- a **playback** side targeting the real output, with `node.dont-reconnect`
  (if the device goes away, so does the EQ) and `node.dont-move` (nothing may
  re-route it, which prevents feedback loops with tools like EasyEffects).

## Output switching

The daemon keeps a live copy of the graph from `pw-dump --monitor`. That
output is event driven, so the daemon sleeps between events. After every change it:

1. Works out the desired EQ processes: one for every *connected* output
   whose assignment has a valid preset. Assignments match by `node.name`,
   falling back to a unique `node.description`, because AirPlay node names
   contain the speaker's IP address.
2. Starts missing processes and stops ones that are no longer wanted or
   whose generated config changed (for example, a preset was edited). Only
   affected outputs restart. A crashed EQ process restarts after a 10 s backoff.
3. If the **default output is a real output that has a running EQ**, it sets
   the default to that EQ sink (`default.configured.audio.sink`). WirePlumber
   then moves streams that follow the default.

So the flows look like this:

- **Headphones plugged in.** The headphone node appears, its EQ starts,
  WirePlumber makes headphones the default, and pweq moves the default to
  *Headphones (EQ)*.
- **Headphones unplugged.** Their node disappears and pweq stops that EQ.
  WirePlumber falls back to the speakers, and pweq moves the default to
  *Speaker (EQ)*.
- **You pick an AirPlay speaker in the volume menu.** pweq moves the default
  to its EQ sink. If you pick an EQ sink directly, nothing needs to happen.
- **Output without a preset.** pweq does nothing, and audio goes straight to it.

On stop, the daemon moves the default back from an EQ sink to its real
output before shutting the EQ processes down, so audio never ends up on a
sink that's about to disappear.

## Design choices

**Why one process per output, not PipeWire's `filter-chain.service`?**
EQ sinks from a static config would exist even while their device doesn't.
WirePlumber could keep such a sink as the default and route audio into a
dead end, or re-link it to the wrong device. Tying each EQ's lifetime to
its device avoids that, and a preset edit restarts only one small process.

**Why not keep EasyEffects?** EasyEffects is a full effects rack with meters
and a UI in the audio path. For "just a parametric EQ" that's a lot of work
per buffer: about 20% of a core on the author's laptop, versus under 1%
for pweq plus PipeWire's biquads.

**Why APO text files?** It's the de facto interchange format (AutoEQ,
squig.link, REW, Equalizer APO), it diffs well, and any editor works.

## Performance

Measured on a ThinkPad X13 Gen 2 (i7-1185G7) during music playback:

| | CPU |
|---|---|
| pweq daemon | ~0.1% of one core |
| `pw-dump --monitor` | ~0.1% |
| EQ process (10 bands) | not measurable with `ps` (<0.1%) |
| EasyEffects (same 10-band EQ) | ~19% |
