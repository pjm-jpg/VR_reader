"""Receive the same fixed-space head/hand poses shown by the Quest VR HUD."""
import argparse
import copy
import json
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path

TAG = 'QuestThreePoint'
POINTS = ('head', 'left', 'right')


def parse_frame(payload):
    """Validate protocol v1. Invalid/missing poses are null, never origin points."""
    frame = json.loads(payload)
    if not isinstance(frame, dict) or type(frame.get('version')) is not int or frame['version'] != 1:
        raise ValueError('Unsupported three-point protocol')
    if frame.get('reference_space') not in ('stage', 'local') or frame.get('axes') != 'flu':
        raise ValueError('Unknown reference space or axes')
    for key in ('session_id', 'sequence', 'sample_time_ns', 'space_epoch'):
        if type(frame.get(key)) is not int or frame[key] < 0:
            raise ValueError('Invalid ' + key)
    if type(frame.get('focused')) is not bool:
        raise ValueError('Invalid focus flag')
    if not isinstance(frame.get('points'), dict) or set(frame['points']) != set(POINTS):
        raise ValueError('Expected head, left and right points')
    for point in frame['points'].values():
        if not isinstance(point, dict):
            raise ValueError('Invalid point')
        for flag in ('valid', 'tracked', 'orientation_valid', 'orientation_tracked'):
            if type(point.get(flag)) is not bool:
                raise ValueError('Invalid tracking flag')
        for valid, tracked in (('valid', 'tracked'), ('orientation_valid', 'orientation_tracked')):
            if point[tracked] and not point[valid]:
                raise ValueError('Tracked pose must be valid')
        if not frame['focused'] and (point['valid'] or point['orientation_valid']):
            raise ValueError('Unfocused frame cannot claim valid poses')
        for key, size in (('position_m', 3), ('orientation_xyzw', 4), ('linear_velocity_m_s', 3)):
            value = point.get(key)
            required = point['orientation_valid'] if key == 'orientation_xyzw' else point['valid']
            if key != 'linear_velocity_m_s' and required != (value is not None):
                raise ValueError('Validity and pose disagree')
            if key == 'linear_velocity_m_s' and not point['valid'] and value is not None:
                raise ValueError('Velocity without a valid position')
            if value is not None:
                if not isinstance(value, list) or len(value) != size:
                    raise ValueError('Wrong vector shape')
                if any(type(v) not in (float, int) or not math.isfinite(v) for v in value):
                    raise ValueError('Non-finite vector')
        q = point.get('orientation_xyzw')
        if q is not None and abs(sum(v * v for v in q) - 1.0) > 0.02:
            raise ValueError('Invalid orientation quaternion')
    return frame


def default_adb():
    bundled = Path(__file__).resolve().parents[2] / '.build-tools/android-sdk/platform-tools/adb'
    found = str(bundled) if bundled.is_file() else shutil.which('adb')
    if not found:
        raise RuntimeError('ADB not found')
    return found


class ThreePointReader:
    """ADB logcat receiver; stale or disconnected samples are unavailable."""

    def __init__(self, ip=None, port=5555, serial=None, adb=None, record=None):
        if ip and serial:
            raise ValueError('Choose either ip or serial')
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        self.adb = str(adb or default_adb())
        self.serial = f'{ip}:{port}' if ip else serial
        self._ip = ip
        self._process = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest = None
        self._received = None
        self._connected = False
        self._record_path = Path(record) if record else None
        self._record_file = None
        self.last_error = None
        self.malformed_frames = 0
        self.frames_received = 0

    def _adb(self, *args):
        result = subprocess.run([self.adb, *args], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return result.stdout.strip()

    def start(self):
        if self._process is not None:
            raise RuntimeError('Reader already started')
        self._adb('start-server')
        if self._ip:
            self._adb('connect', self.serial)
        states = {}
        for line in self._adb('devices').splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2:
                states[fields[0]] = fields[1]
        if self.serial is None:
            devices = [key for key, state in states.items() if ':' not in key and state == 'device']
            if len(devices) != 1:
                raise RuntimeError('Specify --ip or --serial; expected exactly one authorized USB device')
            self.serial = devices[0]
        if states.get(self.serial) != 'device':
            raise RuntimeError(f'{self.serial}: {states.get(self.serial, "not connected")}; '
                               'check the network and accept the headset debugging authorization')
        package = self._adb('-s', self.serial, 'shell', 'dumpsys', 'package', 'com.rail.oculus.teleop')
        import re
        version = re.search(r'versionCode=(\d+)', package)
        if not version or int(version[1]) < 5:
            raise RuntimeError('Install APK 1.4-three-points or newer before using this receiver')
        if self._record_path:
            self._record_path.parent.mkdir(parents=True, exist_ok=True)
            self._record_file = self._record_path.open('a', encoding='utf-8')
        # Subscribe before launching the application. Only fresh log entries are read.
        self._process = subprocess.Popen(
            [self.adb, '-s', self.serial, 'logcat', '-T', '0', '-v', 'raw',
             f'{TAG}:I', '*:S'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1)
        self._thread = threading.Thread(target=self._read, daemon=True)
        with self._lock:
            self._connected = True
        self._thread.start()
        try:
            output = self._adb('-s', self.serial, 'shell', 'am', 'start', '-n',
                               'com.rail.oculus.teleop/com.rail.oculus.teleop.MainActivity')
            if 'Error:' in output:
                raise RuntimeError(output)
        except Exception:
            self.close()
            raise
        return self

    def _accept(self, frame, received):
        with self._lock:
            old = self._latest
            if old and old['session_id'] == frame['session_id'] and frame['sequence'] <= old['sequence']:
                return False
            self._latest = frame
            self._received = received
            self.frames_received += 1
        if self._record_file:
            event = dict(frame, host_receive_monotonic_ns=int(received * 1e9),
                         host_receive_unix_ns=time.time_ns())
            self._record_file.write(json.dumps(event, separators=(',', ':'), allow_nan=False) + '\n')
            self._record_file.flush()
        return True

    def _read(self):
        try:
            for line in self._process.stdout:
                if self._stop.is_set():
                    break
                if not line.lstrip().startswith('{'):
                    if 'error:' in line.lower() or 'offline' in line.lower():
                        self.last_error = line.strip()
                    continue
                try:
                    frame = parse_frame(line)
                except (ValueError, TypeError, KeyError):
                    self.malformed_frames += 1
                    continue
                self._accept(frame, time.monotonic())
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                self.last_error = str(error)
        finally:
            with self._lock:
                self._connected = False

    def get_latest(self, max_age=0.5):
        """Return a copy of a fresh frame or None; age is host receipt age, not latency."""
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError('max_age must be finite and positive')
        with self._lock:
            if not self._connected or self._received is None or time.monotonic() - self._received > max_age:
                return None
            return copy.deepcopy(self._latest)

    @property
    def connected(self):
        with self._lock:
            return self._connected

    def get_status(self):
        """Local receiver diagnostics; a running logcat is not proof of network reachability."""
        with self._lock:
            age = None if self._received is None else max(0.0, time.monotonic() - self._received)
            return {
                'logcat_running': self._process is not None and self._process.poll() is None,
                'stream_open': self._connected,
                'frames_received': self.frames_received,
                'malformed_frames': self.malformed_frames,
                'last_frame_age_s': age,
                'last_error': self.last_error,
            }

    def close(self):
        self._stop.set()
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if self._thread:
                self._thread.join(timeout=2)
            if process.stdout:
                process.stdout.close()
        if self._record_file:
            self._record_file.close()
            self._record_file = None
        with self._lock:
            self._connected = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument('--ip', help='Quest Wi-Fi IPv4 address (without port)')
    transport.add_argument('--serial', help='USB serial, or an already connected ADB address')
    parser.add_argument('--port', type=int, default=5555)
    parser.add_argument('--adb', help='ADB executable; defaults to the workspace bundled version')
    parser.add_argument('--record', help='Append every received frame to a JSONL file')
    parser.add_argument('--interval', type=float, default=0.3, help='Terminal refresh interval, seconds')
    parser.add_argument('--duration', type=float, help='Stop after this many seconds')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0 or (
        args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0)
    ):
        parser.error('interval and duration must be finite and positive')
    reader = ThreePointReader(ip=args.ip, port=args.port, serial=args.serial,
                              adb=args.adb, record=args.record)
    try:
        with reader:
            print(f'Connected: {reader.serial}\nAxes: fixed-space X forward, Y left, Z up; metres', flush=True)
            started = time.monotonic()
            previous_sequence = None
            while args.duration is None or time.monotonic() - started < args.duration:
                if not reader.connected:
                    raise RuntimeError(reader.last_error or 'ADB stream disconnected; reconnect and restart the receiver')
                frame = reader.get_latest()
                if frame is None:
                    print('WAITING / STALE: no fresh three-point frame; keep the headset awake and the app in front', flush=True)
                elif previous_sequence != (frame['session_id'], frame['sequence']):
                    previous_sequence = frame['session_id'], frame['sequence']
                    space = 'FLOOR / STAGE' if frame['reference_space'] == 'stage' else 'LOCAL / NOT FLOOR CALIBRATED'
                    print(f"\n{space}  frame={frame['sequence']}  epoch={frame['space_epoch']}  focused={frame['focused']}")
                    for name in POINTS:
                        point = frame['points'][name]
                        if point['valid']:
                            x, y, z = point['position_m']
                            status = 'TRACKED' if point['tracked'] else 'INFERRED'
                            print(f'{name.upper():5} {status:8} X={x:+.3f} Y={y:+.3f} Z={z:+.3f}')
                        else:
                            print(f'{name.upper():5} NO TRACKING  X=--- Y=--- Z=---')
                    print('', end='', flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Error: {error}\n')
    finally:
        reader.close()


if __name__ == '__main__':
    main()
