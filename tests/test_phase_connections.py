"""Phase-specific connection replacement: model timing and real Streamlit controls."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from procsim import phasing as PH
from procsim.flowsheet import add_unit, connect, new_model, normalize, replace_connection, solve


def fixture():
    m = new_model()
    for name in ("Phase 1", "Phase 2", "Phase 3"):
        PH.add_phase(m, name)
    a = add_unit(m, "feed", name="Old source", params={"composition": {"C1": 1.0}, "P_bar": 20.0})
    b = add_unit(m, "feed", name="New source", params={"composition": {"C1": 1.0}, "P_bar": 40.0})
    out = add_unit(m, "product", name="Export")
    alt = add_unit(m, "product", name="Other export")
    sid = connect(m, a, "out", out, "in")
    return m, {"a": a, "b": b, "out": out, "alt": alt, "old": sid}


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.m, self.ids = fixture()

    def replace(self, pid="p2", source=None, destination=None, sid=None):
        i = self.ids
        return replace_connection(self.m, sid or i["old"], pid, source or i["b"], "out",
                                  destination or i["out"], "in")

    def assert_rejected(self, message, **kw):
        before = copy.deepcopy(self.m)
        with self.assertRaisesRegex(ValueError, message):
            self.replace(**kw)
        self.assertEqual(self.m, before, "Rejected replacements must not mutate the model")

    def test_source_replaced_only_from_selected_phase_and_saved(self):
        new = self.replace()
        restored = normalize(json.loads(json.dumps(self.m)))
        for stage in range(4):
            streams = PH.stage_model(restored, stage)["streams"]
            self.assertEqual(set(streams), {self.ids["old"] if stage < 2 else new})
        self.assertEqual(restored["streams"][new]["src"], [self.ids["b"], "out"])
        self.assertEqual(restored["streams"][new]["name"], "New source")

    def test_solver_follows_replaced_route(self):
        new = self.replace()
        before = solve(dict(self.m, stage=1))
        after = solve(dict(self.m, stage=2))
        self.assertAlmostEqual(before.streams[self.ids["old"]].P, 20.0)
        self.assertAlmostEqual(after.streams[new].P, 40.0)
        self.assertNotIn(new, before.streams)
        self.assertNotIn(self.ids["old"], after.streams)

    def test_destination_can_be_replaced(self):
        new = self.replace(source=self.ids["a"], destination=self.ids["alt"])
        normalize(self.m)
        self.assertIn(new, self.m["streams"])
        self.assertEqual(self.m["streams"][new]["dst"], [self.ids["alt"], "in"])

    def test_later_replacement_of_an_added_route(self):
        first = self.replace(pid="p1")
        second = self.replace(pid="p3", source=self.ids["a"], sid=first)
        normalize(self.m)
        self.assertEqual(self.m["streams"][first]["until"], "p3")
        for stage, expected in enumerate((self.ids["old"], first, first, second)):
            self.assertEqual(set(PH.stage_model(self.m, stage)["streams"]), {expected})

    def test_inherits_a_later_planned_retirement(self):
        PH.set_phase(self.m, [self.ids["old"]], "p3", "remove")
        new = self.replace()
        self.assertEqual(self.m["streams"][new]["until"], "p3")
        self.assertFalse(PH.stage_model(self.m, 3)["streams"])

    def test_added_connection_keeps_its_later_retirement(self):
        old = self.m["streams"][self.ids["old"]]
        old.update(phase="p1", change="add", until="p3")
        new = self.replace()
        self.assertEqual(old["until"], "p2")
        self.assertEqual(self.m["streams"][new]["until"], "p3")
        self.assertFalse(PH.stage_model(self.m, 3)["streams"])

    def test_can_replace_a_route_already_retiring_in_the_selected_phase(self):
        PH.set_phase(self.m, [self.ids["old"]], "p2", "remove")
        new = self.replace()
        self.assertIn(new, PH.stage_model(self.m, 3)["streams"])

    def test_rejects_unknown_phase(self):
        self.assert_rejected("existing project phase", pid="missing")

    def test_rejects_same_route(self):
        self.assert_rejected("Change the source or destination", source=self.ids["a"])

    def test_rejects_route_not_yet_in_place(self):
        PH.set_phase(self.m, [self.ids["old"]], "p2", "add")
        self.assert_rejected("immediately before")

    def test_rejects_absent_new_endpoint(self):
        PH.set_phase(self.m, [self.ids["b"]], "p3", "add")
        self.assert_rejected("Both new endpoints")

    def test_rejects_conflicting_outlet_without_dropping_any_streams(self):
        connect(self.m, self.ids["b"], "out", self.ids["alt"], "in")
        self.assert_rejected("conflicts with connection")

    def test_rejects_conflicting_inlet(self):
        extra = add_unit(self.m, "feed", name="Occupied source")
        connect(self.m, extra, "out", self.ids["alt"], "in")
        self.assert_rejected("conflicts with connection", destination=self.ids["alt"])

    def test_accepts_non_overlapping_occupant(self):
        other = connect(self.m, self.ids["b"], "out", self.ids["alt"], "in")
        PH.set_phase(self.m, [other], "p2", "remove")
        new = self.replace()
        normalize(self.m)
        self.assertIn(other, self.m["streams"])
        self.assertIn(new, self.m["streams"])

    def test_rejects_a_conflict_in_a_later_stage(self):
        other = connect(self.m, self.ids["b"], "out", self.ids["alt"], "in")
        PH.set_phase(self.m, [other], "p3", "add")
        self.assert_rejected("conflicts with connection")

    def test_later_occupant_is_allowed_after_replacement_retires(self):
        PH.set_phase(self.m, [self.ids["old"]], "p3", "remove")
        other = connect(self.m, self.ids["b"], "out", self.ids["alt"], "in")
        PH.set_phase(self.m, [other], "p3", "add")
        new = self.replace()
        normalize(self.m)
        self.assertIn(new, self.m["streams"])
        self.assertEqual(set(PH.stage_model(self.m, 3)["streams"]), {other})

    def test_multi_connection_port_keeps_other_routes(self):
        tee = add_unit(self.m, "splitter", name="Tee")
        self.m["streams"][self.ids["old"]]["src"] = [tee, "out"]
        other = connect(self.m, tee, "out", self.ids["alt"], "in")
        new_out = add_unit(self.m, "product", name="New export")
        new = self.replace(source=tee, destination=new_out)
        normalize(self.m)
        self.assertEqual(set(PH.stage_model(self.m, 2)["streams"]), {other, new})

    def test_invalid_ports_and_missing_stream_are_explicit(self):
        for sid, port, message in ((self.ids["old"], "missing", "valid outlet"),
                                   ("missing", "out", "no longer exists")):
            before = copy.deepcopy(self.m)
            with self.assertRaisesRegex(ValueError, message):
                replace_connection(self.m, sid, "p2", self.ids["b"], port, self.ids["out"], "in")
            self.assertEqual(self.m, before)


try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


UI_SOURCE = """
import streamlit as st
from test_phase_connections import fixture
from ui.phasing import appearance_panel
from ui import state as S

if "model" not in st.session_state:
    st.session_state.model, st.session_state.ids = fixture()
    st.session_state.widget_ver = 0
    st.session_state.nonce = 1
    st.session_state.selected = [st.session_state.ids["old"]]
appearance_panel(st.session_state.selected[0])
if st.button("Undo test edit"):
    S.undo_edit()
    S.bump()
    st.session_state.selected = [st.session_state.ids["old"]]
    st.rerun()
"""


@unittest.skipIf(AppTest is None, "Real Streamlit not installed")
class ReplacementUITests(unittest.TestCase):
    def setUp(self):
        self.at = AppTest.from_string(UI_SOURCE).run()
        self.assertFalse(self.at.exception)

    def widget(self, kind, label):
        return next(w for w in getattr(self.at, kind) if w.label == label)

    def schedule(self):
        self.widget("selectbox", "Replace from phase").set_value("p2")
        self.widget("selectbox", "New source").set_value(self.at.session_state["ids"]["b"])
        self.at.run()
        self.widget("button", "Schedule replacement").click().run()

    def test_schedule_persist_rerun_and_undo(self):
        before = copy.deepcopy(self.at.session_state["model"])
        self.schedule()
        self.assertFalse(self.at.exception)
        self.assertFalse(self.at.error)
        m = self.at.session_state["model"]
        old = self.at.session_state["ids"]["old"]
        new = self.at.session_state["selected"][0]
        self.assertNotEqual(old, new)
        self.assertEqual(set(PH.stage_model(m, 1)["streams"]), {old})
        self.assertEqual(set(PH.stage_model(m, 2)["streams"]), {new})
        self.at.run()
        self.assertEqual(self.at.session_state["model"], m)
        self.widget("button", "Undo test edit").click().run()
        self.assertEqual(self.at.session_state["model"], before)
        self.assertFalse(self.at.exception)

    def test_unchanged_route_reports_error_without_editing(self):
        before = copy.deepcopy(self.at.session_state["model"])
        self.widget("button", "Schedule replacement").click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any("Change the source or destination" in e.value for e in self.at.error))
        self.assertEqual(self.at.session_state["model"], before)
        self.assertNotIn("edit_hist", self.at.session_state)

    def test_equipment_selection_exposes_its_connections_without_empty_parameter_picker(self):
        self.at.session_state["selected"] = [self.at.session_state["ids"]["out"]]
        self.at.run()
        self.assertFalse(self.at.exception)
        self.assertTrue(self.widget("selectbox", "Connection to replace").options)
        self.assertFalse(any(w.label == "Parameter" for w in self.at.selectbox))
        self.assertTrue(any("no phase-specific numeric" in c.value for c in self.at.caption))
        self.assertNotIn("stage_params",
                         self.at.session_state["model"]["units"][self.at.session_state["ids"]["out"]])

    def test_equipment_parameter_overrides_still_set_and_remove(self):
        source = self.at.session_state["ids"]["a"]
        self.at.session_state["selected"] = [source]
        self.at.run()
        self.widget("selectbox", "Parameter").set_value("P_bar").run()
        self.widget("number_input", "New value").set_value(25.0)
        self.widget("button", "Set from this phase").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state["model"]["units"][source]["stage_params"], {"p1": {"P_bar": 25.0}})
        self.widget("selectbox", "Parameter").set_value("P_bar").run()
        self.widget("button", "Remove this override").click().run()
        self.assertFalse(self.at.exception)
        self.assertNotIn("stage_params", self.at.session_state["model"]["units"][source])

    def test_conflicting_route_is_reported_and_preserved(self):
        m = self.at.session_state["model"]
        ids = self.at.session_state["ids"]
        connect(m, ids["b"], "out", ids["alt"], "in")
        self.at.session_state["model"] = m
        before = copy.deepcopy(m)
        self.at.run()
        self.schedule()
        self.assertFalse(self.at.exception)
        self.assertTrue(any("conflicts with connection" in e.value for e in self.at.error))
        self.assertEqual(self.at.session_state["model"], before)
        self.assertEqual(self.at.session_state["selected"], [ids["old"]])


if __name__ == "__main__":
    unittest.main()
