import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch


path = Path(__file__).resolve().parents[2] / 'analysis/diagnose_quest_stream.py'
spec = importlib.util.spec_from_file_location('quest_diagnostic_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def frame(sequence, session=1, epoch=0, focused=True, tracked=True):
    return dict(sequence=sequence, session_id=session, space_epoch=epoch, focused=focused,
                points={k: dict(valid=focused, tracked=focused and tracked) for k in ('head', 'left', 'right')})


class StreamMetricsTest(unittest.TestCase):
    def test_empty_stream_and_trailing_silence_are_counted(self):
        metrics = module.StreamMetrics(started=10)
        self.assertEqual(metrics.snapshot(now=13)['longest_silence_s'], 3)
        metrics.accept(frame(1), 13)
        metrics.accept(frame(2), 13.02)
        self.assertAlmostEqual(metrics.snapshot(now=16)['longest_silence_s'], 2.98)

    def test_reception_rate_and_gap_percentiles(self):
        metrics = module.StreamMetrics(started=0)
        for i in range(5):
            metrics.accept(frame(i), i * .02)
        result = metrics.snapshot(now=.08)
        self.assertAlmostEqual(result['received_fps'], 50)
        self.assertAlmostEqual(result['arrival_gap_ms']['p99'], 20)

    def test_long_gap_is_preserved_after_recovery(self):
        metrics = module.StreamMetrics(started=0)
        for i, received in enumerate((0, .02, 3, 3.02)):
            metrics.accept(frame(i), received)
        self.assertAlmostEqual(metrics.snapshot(now=3.03)['longest_silence_s'], 2.98)

    def test_tracking_and_reference_events_are_separate_from_transport(self):
        metrics = module.StreamMetrics(started=0)
        metrics.accept(frame(1), 0)
        metrics.accept(frame(4, epoch=1, tracked=False), .02)
        metrics.accept(frame(0, session=2, focused=False), .04)
        result = metrics.snapshot(now=.04)
        self.assertEqual(result['sequence_skips'], 2)
        self.assertEqual(result['session_changes'], 1)
        self.assertEqual(result['reference_space_changes'], 1)
        self.assertAlmostEqual(result['tracked_fraction']['head'], 1/3)
        self.assertAlmostEqual(result['valid_fraction']['head'], 2/3)

    def test_sleep_is_not_misclassified_as_network_failure(self):
        reader = SimpleNamespace(adb='adb', serial='HEADSET')
        response = SimpleNamespace(returncode=0, stdout='  mWakefulness=Asleep\n', stderr='')
        with patch.object(module.subprocess, 'run', return_value=response):
            result = module.probe_device_state(reader)
        self.assertTrue(result['adb_responsive'])
        self.assertEqual(result['classification'], 'headset_not_awake')

    def test_timeout_and_authorization_have_separate_diagnostics(self):
        reader = SimpleNamespace(adb='adb', serial='HEADSET')
        with patch.object(module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('adb', 3)):
            self.assertEqual(module.probe_device_state(reader)['classification'], 'adb_or_network_unreachable')
        response = SimpleNamespace(returncode=1, stdout='', stderr='error: device unauthorized')
        with patch.object(module.subprocess, 'run', return_value=response):
            self.assertEqual(module.probe_device_state(reader)['classification'], 'authorization_required')


if __name__ == '__main__':
    unittest.main()
