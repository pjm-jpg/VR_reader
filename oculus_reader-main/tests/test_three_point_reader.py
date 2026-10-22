import copy
import json
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oculus_reader.three_point_reader import ThreePointReader, parse_frame


def packet(sequence=1, session=123):
    point = dict(valid=True, tracked=True, position_m=[0.25, -0.1, 1.6],
                 orientation_valid=True, orientation_tracked=True,
                 orientation_xyzw=[0, 0, 0, 1], linear_velocity_m_s=[0, 0, 0])
    return dict(version=1, session_id=session, sequence=sequence, sample_time_ns=1000000,
                space_epoch=0, reference_space='stage', axes='flu', focused=True,
                points={name: copy.deepcopy(point) for name in ('head', 'left', 'right')})


class ProtocolTests(unittest.TestCase):
    def test_accept_native_contract_and_local_metadata(self):
        data = packet()
        self.assertEqual(parse_frame(json.dumps(data)), data)
        data['reference_space'] = 'local'
        self.assertEqual(parse_frame(json.dumps(data))['reference_space'], 'local')

    def test_tracking_loss_is_null_not_zero(self):
        data = packet()
        data['points']['left'].update(valid=False, tracked=False, position_m=None,
                                     linear_velocity_m_s=None)
        self.assertIsNone(parse_frame(json.dumps(data))['points']['left']['position_m'])
        data['points']['left']['position_m'] = [0, 0, 0]
        with self.assertRaises(ValueError):
            parse_frame(json.dumps(data))

    def test_corruption_unknown_frames_and_nonfinite_numbers_rejected(self):
        for modify in (
            lambda f: f.update(version=2),
            lambda f: f.update(axes='xr'),
            lambda f: f['points'].pop('head'),
            lambda f: f['points']['head'].update(position_m=[1, 2]),
            lambda f: f['points']['head'].update(position_m=[float('nan'), 0, 1]),
            lambda f: f['points']['head'].update(orientation_xyzw=[0, 0, 0, 0]),
            lambda f: f.update(focused=False),
        ):
            data = packet()
            modify(data)
            with self.assertRaises((ValueError, TypeError)):
                parse_frame(json.dumps(data))

    def test_stale_disconnected_and_out_of_order_frames(self):
        reader = ThreePointReader()
        reader._connected = True
        reader._accept(packet(sequence=3), received=10.0)
        self.assertFalse(reader._accept(packet(sequence=2), received=10.1))
        with patch('oculus_reader.three_point_reader.time.monotonic', return_value=10.2):
            frame = reader.get_latest()
            self.assertEqual(frame['sequence'], 3)
            frame['points']['head']['position_m'][0] = 99
            self.assertEqual(reader.get_latest()['points']['head']['position_m'][0], 0.25)
        with patch('oculus_reader.three_point_reader.time.monotonic', return_value=10.6):
            self.assertIsNone(reader.get_latest())
        # Frame counters can restart in a new OpenXR session.
        self.assertTrue(reader._accept(packet(sequence=0, session=456), received=11))
        reader._connected = False
        self.assertIsNone(reader.get_latest())

    def test_stop_unblocks_a_silent_stream(self):
        reader = ThreePointReader()
        reader._process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                           stdout=subprocess.PIPE, text=True)
        import threading
        reader._connected = True
        reader._thread = threading.Thread(target=reader._read, daemon=True)
        reader._thread.start()
        started = time.monotonic()
        reader.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(reader._thread.is_alive())
        self.assertIsNotNone(reader._process.poll())

    def test_diagnostics_report_stale_stream_without_claiming_network_reachability(self):
        reader = ThreePointReader()
        self.assertIsNone(reader.get_status()['last_frame_age_s'])
        self.assertFalse(reader.get_status()['logcat_running'])
        reader._connected = True
        reader._accept(packet(), received=10.0)
        with patch('oculus_reader.three_point_reader.time.monotonic', return_value=13.0):
            status = reader.get_status()
            self.assertEqual(status['frames_received'], 1)
            self.assertEqual(status['last_frame_age_s'], 3.0)
            self.assertTrue(status['stream_open'])
            self.assertIsNone(reader.get_latest())

    def test_subprocess_stream_records_each_frame_and_stops(self):
        first = packet()
        second = packet(sequence=2)
        second['points']['left'].update(valid=False, tracked=False, position_m=None,
                                       linear_velocity_m_s=None)
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'adb'
            fake.write_text(f'''#!{sys.executable}
import sys, time
args = sys.argv[1:]
if 'devices' in args:
    print('List of devices attached\\nUSBTEST\\tdevice')
elif 'dumpsys' in args:
    print('versionCode=5 minSdk=26')
elif 'logcat' in args:
    print({json.dumps(first)!r}, flush=True)
    print({json.dumps(second)!r}, flush=True)
    print('{{"version":', flush=True)
    time.sleep(60)
''')
            fake.chmod(0o755)
            record = Path(directory) / 'frames.jsonl'
            with ThreePointReader(serial='USBTEST', adb=fake, record=record) as reader:
                deadline = time.monotonic() + 3
                while reader.malformed_frames == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(reader.frames_received, 2)
                self.assertEqual(reader.malformed_frames, 1)
                self.assertIsNotNone(reader.get_status()['last_parse_error'])
                frame = reader.get_latest()
                self.assertEqual(frame['sequence'], 2)
                self.assertIsNone(frame['points']['left']['position_m'])
            recorded = [json.loads(line) for line in record.read_text().splitlines()]
            self.assertEqual([frame['sequence'] for frame in recorded], [1, 2])
            self.assertIn('host_receive_monotonic_ns', recorded[0])


if __name__ == '__main__':
    unittest.main()
