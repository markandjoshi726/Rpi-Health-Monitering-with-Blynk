#!/usr/bin/env python3
"""Run on Raspberry Pi 3B+; see README.md before connecting hardware."""
import argparse
from collections import deque
import json
import logging
import os
from pathlib import Path
import re
import threading
import time

from sensors import HoneywellHSC, MAX30100
from processing import ppg_metrics

LOG = logging.getLogger('health_monitor')


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.samples = deque(maxlen=1200)
        self.last_ppg = 0
        self.errors = {}
        self.events = {'sound': False, 'motion': False}

    def put(self, key, value):
        with self.lock:
            self.values[key] = (value, time.monotonic())

    def error(self, key, error):
        with self.lock:
            self.errors[key] = error

    def event(self, key):
        with self.lock:
            self.events[key] = True

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            values = {k: v if now - t < 1
                      else None for k, (v, t) in self.values.items()}
            samples = list(self.samples) if now - self.last_ppg < 1 else []
            events = dict(self.events)
            self.events = dict.fromkeys(self.events, False)
            return values, samples, events, dict(self.errors)


def ppg_worker(state, stop):
    while not stop.is_set():
        sensor = None
        try:
            sensor = MAX30100()
            last_poll = time.monotonic()
            while not stop.is_set():
                now = time.monotonic()
                # A 16-slot FIFO can wrap without a usable continuous waveform.
                if now - last_poll > 0.12:
                    sensor.clear_fifo()
                    with state.lock:
                        state.samples.clear()
                last_poll = now
                rows = sensor.read_samples()
                if rows:
                    with state.lock:
                        state.samples.extend(rows)
                        state.last_ppg = now
                    state.error('MAX30100', None)
                stop.wait(0.01)
        except (OSError, ValueError, RuntimeError) as exc:
            state.error('MAX30100', str(exc))
            with state.lock:
                state.samples.clear()
                state.last_ppg = 0
        finally:
            if sensor:
                try:
                    sensor.close()
                except OSError:
                    pass
        stop.wait(2)


def pressure_worker(state, stop, cfg):
    while not stop.is_set():
        sensor = None
        try:
            sensor = HoneywellHSC(cfg)
            while not stop.is_set():
                try:
                    state.put('cuff_mmhg', sensor.read_mmhg())
                    state.error('pressure', None)
                except ValueError as exc:
                    state.put('cuff_mmhg', None)
                    state.error('pressure', str(exc))
                stop.wait(0.02)
        except (OSError, RuntimeError) as exc:
            state.put('cuff_mmhg', None)
            state.error('pressure', str(exc))
        finally:
            if sensor:
                sensor.close()
        stop.wait(2)


class Blynk:
    def __init__(self):
        import requests
        self.requests = requests
        self.session = requests.Session()
        self.token = os.environ.get('BLYNK_AUTH_TOKEN', '')
        host = os.environ.get('BLYNK_SERVER', 'blynk.cloud')
        if not self.token:
            raise ValueError('Set BLYNK_AUTH_TOKEN, or run with --no-cloud')
        if not re.fullmatch(r'(?:[a-z0-9-]+\.)?blynk\.cloud', host):
            raise ValueError('BLYNK_SERVER must be blynk.cloud or your regional Blynk hostname')
        self.url = 'https://' + host + '/external/api/batch/update'

    def publish(self, values, metrics, events, status):
        params = {'token': self.token, 'v5': int(events['sound']),
                  'v6': int(events['motion']), 'v7': status[:250],
                  'v8': int(time.time()),
                  'v10': int(metrics['pulse_bpm'] is not None),
                  'v11': int(metrics['spo2_pct'] is not None),
                  'v12': int(values.get('cuff_mmhg') is not None)}
        for pin, value in [('v2', metrics['pulse_bpm']), ('v3', metrics['spo2_pct']),
                           ('v4', values.get('cuff_mmhg')), ('v9', metrics['ratio'])]:
            if value is not None:
                params[pin] = round(value, 4)
        # Do not log request URLs or exception strings: query contains token.
        try:
            r = self.session.get(self.url, params=params, timeout=(3, 5), allow_redirects=False)
            if r.status_code != 200:
                LOG.warning('Blynk HTTP %s: check token, regional server, datastreams or quota', r.status_code)
                return False
            return True
        except self.requests.RequestException:
            LOG.warning('Blynk unavailable; local sampling continues')
            return False

    def close(self):
        self.session.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--no-cloud', action='store_true', help='Read real hardware locally only')
    parser.add_argument('--seconds', type=float, default=0, help='0 runs until Ctrl+C')
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    if cfg['publish_seconds'] < 2:
        raise ValueError('publish_seconds must be >= 2')
    if cfg['pressure']['model'] != 'HSCDANN600MGSA3':
        raise ValueError('This configuration is written for HSCDANN600MGSA3')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cloud = None if args.no_cloud else Blynk()
    from gpiozero import DigitalInputDevice
    state, stop = State(), threading.Event()
    devices, threads = [], []
    pending = {'sound': False, 'motion': False}
    started = time.monotonic()
    try:
        for name in ('sound', 'motion'):
            # Comparator DO outputs must be 3.3V-compatible. Pull-up to Pi's 3.3V.
            device = DigitalInputDevice(cfg[name + '_gpio'], pull_up=None,
                                        active_state=cfg[name + '_active_high'])
            device.when_activated = lambda key=name: state.event(key)
            devices.append((name, device))
        for target, extra in [(ppg_worker, ()),
                              (pressure_worker, (cfg['pressure'],))]:
            t = threading.Thread(target=target, args=(state, stop, *extra), daemon=True)
            t.start()
            threads.append(t)
        while not args.seconds or time.monotonic() - started < args.seconds:
            values, samples, events, errors = state.snapshot()
            for name, device in devices:
                pending[name] |= events[name] or bool(device.is_active)
            metrics = ppg_metrics(samples, cfg['ppg'])
            status = '; '.join([metrics['ppg_status']] + [k + ': ' + v for k, v in errors.items() if v])
            LOG.info('%s', json.dumps(dict(values=values, metrics=metrics, events=pending, status=status)))
            if cloud is None or cloud.publish(values, metrics, pending, status):
                pending = dict.fromkeys(pending, False)
            stop.wait(cfg['publish_seconds'])
    except KeyboardInterrupt:
        LOG.info('Stopping')
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        for _, device in devices:
            device.close()
        if cloud:
            cloud.close()


if __name__ == '__main__':
    main()
