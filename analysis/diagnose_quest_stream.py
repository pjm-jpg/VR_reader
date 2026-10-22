#!/usr/bin/env python3
"""Measure Quest frame arrival continuity without enabling a robot controller."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import threading
import time


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


class StreamMetrics:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started
        self.lock = threading.Lock()
        self.first_received = self.last_received = None
        self.previous = None
        self.gaps = []
        self.frames = self.focused = 0
        self.skipped = self.session_changes = self.space_changes = 0
        self.valid = dict.fromkeys(('head', 'left', 'right'), 0)
        self.tracked = self.valid.copy()

    def accept(self, frame, received):
        with self.lock:
            if self.last_received is not None:
                self.gaps.append(received - self.last_received)
            else:
                self.first_received = received
            self.last_received = received
            self.frames += 1
            self.focused += int(frame['focused'])
            key = (frame['session_id'], frame['sequence'], frame['space_epoch'])
            if self.previous is not None:
                if key[0] != self.previous[0]:
                    self.session_changes += 1
                else:
                    self.skipped += max(0, key[1] - self.previous[1] - 1)
                    self.space_changes += int(key[2] != self.previous[2])
            self.previous = key
            for name, point in frame['points'].items():
                self.valid[name] += int(point['valid'])
                self.tracked[name] += int(point['tracked'])

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            gaps = self.gaps.copy()
            age = None if self.last_received is None else max(0, now - self.last_received)
            # Count a stream that stopped at the end of the run as a real gap.
            longest = max([*gaps, age if age is not None else max(0, now - self.started)])
            span = 0 if self.first_received is None else self.last_received - self.first_received
            return {
                'frames': self.frames,
                'received_fps': (self.frames - 1) / span if span > 0 else None,
                'last_frame_age_s': age,
                'longest_silence_s': longest,
                'arrival_gap_ms': {name: None if (v := percentile(gaps, q)) is None else v * 1000
                                   for name, q in (('p50', .5), ('p95', .95), ('p99', .99))},
                'sequence_skips': self.skipped,
                'session_changes': self.session_changes,
                'reference_space_changes': self.space_changes,
                'focused_fraction': self.focused / self.frames if self.frames else None,
                'valid_fraction': {k: v / self.frames if self.frames else None for k, v in self.valid.items()},
                'tracked_fraction': {k: v / self.frames if self.frames else None for k, v in self.tracked.items()},
            }


def load_reader():
    path = Path(__file__).resolve().parents[1] / 'oculus_reader-main/oculus_reader/three_point_reader.py'
    spec = importlib.util.spec_from_file_location('quest_diagnostic_receiver', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ThreePointReader


def probe_device_state(reader):
    """Bounded read-only ADB check after a stall; never runs in the control loop."""
    try:
        result = subprocess.run([reader.adb, '-s', reader.serial, 'shell', 'dumpsys', 'power'],
                                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(adb_responsive=False, wakefulness=None, error=str(error),
                    classification='adb_or_network_unreachable')
    if result.returncode:
        error = (result.stderr or result.stdout).strip()
        classification = 'authorization_required' if 'unauthorized' in error.lower() else 'adb_or_network_unreachable'
        return dict(adb_responsive=False, wakefulness=None, error=error, classification=classification)
    wakefulness = next((line.split('=', 1)[1].strip() for line in result.stdout.splitlines()
                       if line.strip().startswith('mWakefulness=')), None)
    return dict(adb_responsive=True, wakefulness=wakefulness, error=None,
                classification='headset_not_awake' if wakefulness in ('Asleep', 'Dozing', 'Dreaming')
                else 'source_or_logcat_paused')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument('--ip', help='Headset Wi-Fi address')
    transport.add_argument('--serial', help='USB ADB serial')
    parser.add_argument('--adb', help='ADB executable path')
    parser.add_argument('--duration', type=float, default=300, help='Observation duration in seconds')
    parser.add_argument('--max-gap', type=float, default=.5, help='Allowed frame arrival gap in seconds')
    parser.add_argument('--stop-after-stale', type=float, default=5, help='Stop early after this silent interval')
    parser.add_argument('--output', type=Path, help='Write a JSON summary')
    parser.add_argument('--record', help='Optional raw JSONL recording')
    args = parser.parse_args()
    for name in ('duration', 'max_gap', 'stop_after_stale'):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(name.replace('_', '-') + ' must be finite and positive')

    metrics = StreamMetrics()
    reader_class = load_reader()

    class MeasuredReader(reader_class):
        def _accept(self, frame, received):
            accepted = super()._accept(frame, received)
            if accepted:
                metrics.accept(frame, received)
            return accepted

    reader = MeasuredReader(ip=args.ip, serial=args.serial, adb=args.adb, record=args.record)
    error = None
    completed = False
    elapsed = 0
    try:
        reader.start()
        started = time.monotonic()
        metrics.started = started
        last_report = 0
        while True:
            elapsed = time.monotonic() - started
            status = reader.get_status()
            age = status['last_frame_age_s']
            if elapsed >= args.duration:
                completed = True
                break
            if elapsed - last_report >= 10:
                print(json.dumps(dict(elapsed_s=round(elapsed, 1), **status)), flush=True)
                last_report = elapsed
            if not reader.connected:
                error = status['last_error'] or 'logcat stream ended'
                break
            if (elapsed > args.stop_after_stale and age is None) or (age is not None and age > args.stop_after_stale):
                error = 'no new frames within the configured silent interval'
                break
            time.sleep(.05)
    except KeyboardInterrupt:
        error = 'interrupted'
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        error = str(exc)
    finally:
        summary = dict(transport='wifi' if args.ip else 'usb', device=reader.serial,
                       elapsed_s=elapsed, completed=completed, error=error,
                       **metrics.snapshot(), receiver=reader.get_status())
        if not completed:
            summary['device_probe'] = probe_device_state(reader)
        reader.close()
    summary['continuity_passed'] = bool(completed and not error and summary['frames'] >= 2
                                        and summary['longest_silence_s'] <= args.max_gap
                                        and summary['receiver']['malformed_frames'] == 0)
    summary['scope'] = 'Host arrival continuity only; not end-to-end latency or real-robot readiness.'
    output = json.dumps(summary, indent=2, allow_nan=False)
    print(output, flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    return 0 if summary['continuity_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
