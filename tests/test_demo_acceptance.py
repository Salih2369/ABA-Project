"""Cross-module contract tests; synthetic signals are NOT model evidence."""
import unittest
from aba_demo.engine import Engine, INDICATORS


def observation(t, key=None, identity='confirmed'):
    return {'time': t, 'identity': identity,
            'signals': {k: k == key for k in INDICATORS}, 'values': {}}


class DemoAcceptanceTests(unittest.TestCase):
    def test_identity_loss_interrupts_all_events_and_does_not_bridge_gap(self):
        engine = Engine({'persistence_seconds': 1, 'max_observed_delta': 2})
        engine.update(observation(0, 'body_motion'))
        active = engine.update(observation(1, 'body_motion'))
        self.assertIsNotNone(active['alert'])
        unknown = engine.update(observation(2, 'body_motion', 'uncertain'))
        self.assertIsNone(unknown['alert'])
        self.assertTrue(all(x['state'] == 'unobservable' for x in unknown['indicators'].values()))
        resumed = engine.update(observation(3, 'body_motion'))
        self.assertEqual(resumed['indicators']['body_motion']['duration'], 0)
        self.assertEqual(resumed['indicators']['body_motion']['state'], 'candidate')

    def test_break_removes_behavior_alerts_not_a_healthy_score(self):
        engine = Engine()
        engine.set_activity('break')
        state = engine.update(observation(0, 'body_motion'))
        self.assertIsNone(state['alert'])
        self.assertTrue(all(x['state'] == 'not_applicable' for x in state['indicators'].values()))
        self.assertNotIn('score', state)
        self.assertNotIn('recommendation', state)

    def test_a_stable_posture_transition_pulse_is_counted(self):
        engine = Engine()
        engine.update(observation(0))
        state = engine.update(observation(.25, 'posture_change'))
        self.assertEqual(state['indicators']['posture_change']['count'], 1)
        self.assertIsNone(state['alert'], 'Posture alerts are opt-in')

    def test_invalid_input_is_rejected_before_mutating_state(self):
        engine = Engine()
        engine.update(observation(0))
        bad = observation(1)
        bad['signals']['hand_motion'] = 'yes'
        with self.assertRaises((ValueError, TypeError)):
            engine.update(bad)
        state = engine.update(observation(1))
        self.assertEqual(state['time'], 1)


if __name__ == '__main__':
    unittest.main()
