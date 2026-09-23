# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pweq import apo, chain, config, daemon, easyeffects, pw

AUTOEQ = """\
Preamp: -6.8 dB
Filter 1: ON PK Fc 21 Hz Gain 6.7 dB Q 1.100
Filter 2: ON LSC Fc 105 Hz Gain 7.6 dB Q 0.70
Filter 3: OFF HSC Fc 10000 Hz Gain 4.7 dB Q 0.70
# comment
Filter: ON HP Fc 20 Hz
"""


class ApoTest(unittest.TestCase):
    def test_parse_autoeq(self):
        p = apo.parse(AUTOEQ)
        self.assertEqual(p.preamp, -6.8)
        self.assertEqual([b.type for b in p.bands], ["PK", "LSC", "HSC", "HP"])
        self.assertEqual(p.bands[1].label, "bq_lowshelf")
        self.assertFalse(p.bands[2].enabled)
        self.assertEqual(len(p.active_bands), 3)
        self.assertEqual(p.bands[3].q, apo.DEFAULT_Q)  # Q optional for pass filters

    def test_examples_parse(self):
        examples = sorted(Path(__file__).parent.parent.joinpath("examples").glob("*.txt"))
        self.assertTrue(examples)
        for path in examples:
            with self.subTest(path=path.name):
                self.assertTrue(apo.parse(path.read_text()).bands)

    def test_roundtrip(self):
        p = apo.parse(AUTOEQ)
        self.assertEqual(apo.parse(apo.dumps(p)), p)

    def test_errors_name_the_line(self):
        cases = {
            "Filter 1: ON XX Fc 100 Hz Gain 1 dB Q 1": "unsupported filter type",
            "Filter 1: ON PK Gain 1 dB Q 1": "missing 'Fc",
            "Filter 1: ON PK Fc 100 Hz Q 1": "missing 'Gain",
            "Filter 1: ON PK Fc 100 Hz Gain 1 dB Q 0": "Q must be positive",
            "Filter 1: MAYBE PK Fc 100 Hz": "ON or OFF",
            "Hello": "expected",
        }
        for text, reason in cases.items():
            with self.subTest(text=text), self.assertRaises(apo.ParseError) as cm:
                apo.parse("Preamp: 0 dB\n" + text)
            self.assertIn(reason, str(cm.exception))
            self.assertEqual(cm.exception.line_no, 2)


class ChainTest(unittest.TestCase):
    def test_render(self):
        conf = chain.render("spk", "alsa_output.x", 'Speaker "A"', "clean", apo.parse(AUTOEQ))
        self.assertIn('node.name = "pweq.spk"', conf)
        self.assertIn('target.object = "alsa_output.x"', conf)
        self.assertIn('node.description = "Speaker \\"A\\" (EQ: clean)"', conf)
        self.assertIn("label = bq_peaking", conf)
        self.assertNotIn("bq_highshelf", conf)  # disabled band skipped
        self.assertIn('{ output = "preamp:Out" input = "band1:In" }', conf)
        self.assertIn('{ output = "band2:Out" input = "band3:In" }', conf)
        self.assertNotIn("band4", conf)
        mult = 10 ** (-6.8 / 20)
        self.assertIn(f'"Mult" = {mult!r}', conf)

    def test_flat_preset_has_only_preamp(self):
        conf = chain.render("k", "t", "d", "p", apo.Preset())
        self.assertIn('"Mult" = 1.0', conf)
        self.assertNotIn("output =", conf)

    def test_slug(self):
        name = "raop_sink.Living Room.local.192.168.1.40.7000"
        self.assertEqual(chain.slug(name), "raop-sink-living-room-local-192-168-1-40-7000")
        self.assertEqual(chain.slug("???"), "output")


def node(oid, name, desc=None, cls="Audio/Sink", **props):
    p = {"node.name": name, "media.class": cls, "node.description": desc or name, **props}
    return {"id": oid, "type": pw.NODE_TYPE, "info": {"props": p}}


def default_meta(sink, oid=42):
    return {
        "id": oid,
        "type": pw.METADATA_TYPE,
        "props": {"metadata.name": "default"},
        "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON", "value": {"name": sink}}],
    }


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.g = pw.Graph()
        self.g.apply(
            [
                node(1, "alsa.speaker", "Speaker"),
                # Real RAOP sinks are virtual (boolean) and must still be listed.
                node(2, "raop_sink.livingroom.local.1.2.3.4.7000", "Living Room", **{"node.virtual": True}),
                node(3, "easyeffects_sink", **{"factory.name": "support.null-audio-sink"}),
                node(4, "pweq.alsa-speaker"),
                node(5, "spotify", cls="Stream/Output/Audio"),
                node(6, "effect_input.eq6", **{"node.virtual": True, "node.link-group": "filter-chain-1-2"}),
                default_meta("alsa.speaker"),
            ]
        )

    def test_outputs_keep_airplay_skip_processing_sinks(self):
        names = [o.name for o in self.g.outputs()]
        self.assertEqual(names, ["raop_sink.livingroom.local.1.2.3.4.7000", "alsa.speaker"])

    def test_default_and_updates(self):
        self.assertEqual(self.g.default_sink(), "alsa.speaker")
        self.g.apply([default_meta("pweq.alsa-speaker")])
        self.assertEqual(self.g.default_sink(), "pweq.alsa-speaker")
        self.g.apply([{"id": 1, "info": None}])
        self.assertNotIn("alsa.speaker", self.g.node_names())
        self.g.apply([{"id": 42, "info": None}])
        self.assertIsNone(self.g.default_sink())

    def test_metadata_key_removed(self):
        self.g.apply([{"id": 42, "metadata": [{"subject": 0, "key": "default.audio.sink", "value": None}]}])
        self.assertIsNone(self.g.default_sink())

    def test_short_label(self):
        card = "500 Series (HD Audio)"
        self.assertEqual(pw.Output(1, "n", f"{card} Speaker", card).label, "Speaker")
        self.assertEqual(pw.Output(2, "n", "Living Room").label, "Living Room")
        self.assertEqual(pw.short_label(f"{card} Headphones", ["other", card]), "Headphones")

    def test_batch_splitter(self):
        s = pw.BatchSplitter()
        text = json.dumps([node(9, "a")], indent=2) + "\n" + json.dumps([node(10, "b")], indent=2) + "\n"
        data = text.encode()
        batches = s.feed(data[:37]) + s.feed(data[37:120]) + s.feed(data[120:])
        self.assertEqual([b[0]["id"] for b in batches], [9, 10])


class PlanTest(unittest.TestCase):
    def test_plan_and_redirect(self):
        outputs = [
            pw.Output(1, "alsa.speaker", "Speaker"),
            pw.Output(2, "raop_sink.livingroom.local.9.9.9.9.7000", "Living Room"),
            pw.Output(3, "alsa.hdmi", "HDMI"),
        ]
        assignments = [
            config.Assignment("alsa.speaker", "Speaker", "clean"),
            config.Assignment("raop_sink.livingroom.local.1.2.3.4.7000", "Living Room", "room"),  # IP changed
            config.Assignment("alsa.hdmi", "HDMI", "broken"),
        ]
        presets = {"clean": apo.Preset(), "room": apo.parse(AUTOEQ)}

        def load(name):
            if name not in presets:
                raise apo.ParseError(1, "x", "bad")
            return presets[name]

        with mock.patch.object(daemon, "log"):
            plan = daemon.plan(outputs, assignments, load)
        self.assertEqual(set(plan), {"alsa-speaker", "raop-sink-livingroom-local-1-2-3-4-7000"})
        room = plan["raop-sink-livingroom-local-1-2-3-4-7000"]
        self.assertEqual(room.target, "raop_sink.livingroom.local.9.9.9.9.7000")

        self.assertEqual(daemon.redirect_target("alsa.speaker", plan, {"pweq.alsa-speaker"}), "pweq.alsa-speaker")
        self.assertIsNone(daemon.redirect_target("alsa.speaker", plan, set()), "EQ sink not up yet")
        self.assertIsNone(daemon.redirect_target("pweq.alsa-speaker", plan, {"pweq.alsa-speaker"}))
        self.assertIsNone(daemon.redirect_target("alsa.hdmi", plan, {"pweq.alsa-hdmi"}))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_assignments_roundtrip_and_match(self):
        items = config.set_assignment([], "a", "Speaker", "clean")
        items = config.set_assignment(items, "b", "Speaker", "x")
        items = config.set_assignment(items, "c", "Den", "room")
        config.save_assignments(items)
        loaded = config.load_assignments()
        self.assertEqual(loaded, items)
        self.assertEqual(config.match(loaded, "a", "whatever").preset, "clean")
        self.assertEqual(config.match(loaded, "c2", "Den").preset, "room")
        self.assertIsNone(config.match(loaded, "z", "Speaker"), "ambiguous description")
        self.assertEqual(config.set_assignment(loaded, "a", "Speaker", None), loaded[1:])

    def test_import_validates(self):
        good = Path(self.tmp.name, "My Phones.txt")
        good.write_text(AUTOEQ)
        self.assertEqual(config.import_preset(good), "My Phones")
        self.assertEqual(list(config.list_presets()), ["My Phones"])
        bad = Path(self.tmp.name, "bad.txt")
        bad.write_text("nonsense\n")
        with self.assertRaises(apo.ParseError):
            config.import_preset(bad)
        self.assertNotIn("bad", config.list_presets())


class CliAssignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"PWEQ_CONFIG_DIR": self.tmp.name})
        self.env.start()
        config.save_preset("warm", apo.parse(AUTOEQ))
        card = "Built-in Audio"
        g = pw.Graph()
        g.apply(
            [
                {"id": 50, "type": "PipeWire:Interface:Device", "info": {"props": {"device.description": card}}},
                node(1, "alsa.speaker", f"{card} Speaker", **{"device.id": 50}),
                node(2, "alsa.hdmi", f"{card} HDMI", **{"device.id": 50}),
            ]
        )
        # A disconnected output known only from a stale assignment.
        config.save_assignments([config.Assignment("alsa.hp", f"{card} Headphones", "warm")])
        patches = [
            mock.patch.object(pw, "dump", return_value=g),
            mock.patch("pweq.__main__.reload_daemon", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def presets(self):
        return {a.name: a.preset for a in config.load_assignments()}

    def test_assign_by_short_name_case_insensitive(self):
        from pweq.__main__ import main

        self.assertEqual(main(["assign", "speaker", "warm"]), 0)
        self.assertEqual(self.presets(), {"alsa.hp": "warm", "alsa.speaker": "warm"})
        self.assertEqual(main(["assign", "Headphones", "none"]), 0)
        self.assertEqual(self.presets(), {"alsa.speaker": "warm"})

    def test_assign_errors(self):
        from pweq.__main__ import main

        with mock.patch("sys.stderr"):
            self.assertEqual(main(["assign", "nope", "warm"]), 1)
            self.assertEqual(main(["assign", "Speaker", "missing-preset"]), 1)


def ee_preset(bypass=False, bands=None, plugins=("equalizer#0",)):
    bands = bands or [
        {"type": "Lo-shelf", "frequency": 105.0, "gain": 7.6, "q": 0.7, "mute": False, "slope": "x1"},
        {"type": "Bell", "frequency": 1000.0, "gain": -2.0, "q": 1.4, "mute": True, "slope": "x1"},
        {"type": "Resonance", "frequency": 50.0, "gain": 1.0, "q": 1.0, "mute": False, "slope": "x1"},
    ]
    eq = {
        "bypass": bypass,
        "input-gain": -6.69,
        "output-gain": 1.0,
        "num-bands": len(bands),
        "split-channels": False,
        "left": {f"band{i}": b for i, b in enumerate(bands)},
    }
    return {"output": {"plugins_order": list(plugins), "equalizer#0": eq}}


class EasyEffectsTest(unittest.TestCase):
    def test_convert(self):
        preset, warnings = easyeffects.convert(ee_preset())
        self.assertAlmostEqual(preset.preamp, -5.69)
        self.assertEqual([(b.type, b.freq) for b in preset.bands], [("LSC", 105.0)])
        self.assertTrue(any("Resonance" in w for w in warnings))

    def test_bypassed_is_flat(self):
        self.assertIsNone(easyeffects.convert(ee_preset(bypass=True))[0])
        preset, warnings = easyeffects.convert(ee_preset(plugins=("limiter#0",)))
        self.assertIsNone(preset)
        self.assertTrue(warnings)

    def test_import_all(self):
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as ee, mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": cfg}
        ):
            ee = Path(ee)
            (ee / "output").mkdir()
            (ee / "autoload/output").mkdir(parents=True)
            (ee / "output/porta pro.json").write_text(json.dumps(ee_preset()))
            (ee / "output/clean.json").write_text(json.dumps(ee_preset(bypass=True)))
            rules = [("alsa.hp", "Headphones", "porta pro"), ("raop.livingroom", "Living Room", "clean")]
            for dev, desc, name in rules:
                (ee / f"autoload/output/{dev}:x.json").write_text(
                    json.dumps({"device": dev, "device-description": desc, "preset-name": name})
                )
            easyeffects.import_all(ee)
            self.assertEqual(list(config.list_presets()), ["porta pro"])
            self.assertEqual(config.load_assignments(), [config.Assignment("alsa.hp", "Headphones", "porta pro")])


if __name__ == "__main__":
    unittest.main()
