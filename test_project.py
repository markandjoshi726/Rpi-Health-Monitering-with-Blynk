import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sensors import decode_hsc, MAX30100
from processing import ppg_metrics
from main import Blynk, State

CFG = {'min_dc_counts': 1000, 'min_perfusion_fraction': 0.0005,
       'spo2_calibration': None}

class ProjectTests(unittest.TestCase):
    def test_pressure_endpoints(self):
        for raw, expected in [(1638, 0), (14745, 600)]:
            self.assertAlmostEqual(decode_hsc([raw >> 8, raw & 255]), expected)
        self.assertAlmostEqual(decode_hsc([0x39, 0x99]) * 0.750061683, 450.0370098)

    def test_pressure_faults(self):
        for first in [0x46, 0x86, 0xc6]:
            with self.assertRaises(ValueError):
                decode_hsc([first, 0x66])
        for data in ([0, 0], [0x3f, 255]):
            with self.assertRaises(ValueError):
                decode_hsc(data)

    def test_synthetic_pulse_and_no_fabricated_spo2(self):
        t = np.arange(1200) / 100
        rows = np.column_stack((20000 + 1000 * np.sin(2*np.pi*1.2*t),
                                15000 + 500 * np.sin(2*np.pi*1.2*t)))
        result = ppg_metrics(rows, CFG)
        self.assertAlmostEqual(result['pulse_bpm'], 72, delta=1)
        self.assertIsNone(result['spo2_pct'])
        self.assertAlmostEqual(result['ratio'], 2/3, places=3)

    def test_flat_no_finger_and_warmup(self):
        for rows in [[], np.ones((1200, 2))*20000, np.zeros((1200, 2))]:
            result = ppg_metrics(rows, CFG)
            self.assertIsNone(result['pulse_bpm'])
            self.assertIsNone(result['spo2_pct'])

    def test_max_registers_and_fifo(self):
        bus = Mock()
        regs = {0xff: 0x11, 0x06: 0, 0x03: 0, 0x02: 1, 0x04: 15}
        bus.read_byte_data.side_effect = lambda address, reg: regs.get(reg, 0)
        bus.read_i2c_block_data.return_value = [0x12, 0x34, 0x56, 0x78]
        with patch.dict(sys.modules, {'smbus2': Mock(SMBus=Mock(return_value=bus))}):
            sensor = MAX30100()
        bus.write_byte_data.assert_any_call(0x57, 0x07, 0x47)
        self.assertEqual(sensor.read_samples(), [(0x1234, 0x5678)] * 2)
        regs[0x03] = 1
        with self.assertRaises(ValueError):
            sensor.read_samples()
        sensor.close()

    def test_blynk_validity_and_timeout(self):
        with patch.dict(os.environ, {'BLYNK_AUTH_TOKEN': 'test-only', 'BLYNK_SERVER': 'blynk.cloud'}):
            cloud = Blynk()
        cloud.session = Mock()
        cloud.session.get.return_value.status_code = 200
        metrics = {'pulse_bpm': None, 'spo2_pct': None, 'ratio': None}
        self.assertTrue(cloud.publish({'cuff_mmhg': 120}, metrics,
                                    {'sound': True, 'motion': False}, 'warming'))
        params = cloud.session.get.call_args.kwargs['params']
        self.assertNotIn('v2', params)
        self.assertNotIn('v3', params)
        self.assertEqual(params['v11'], 0)
        self.assertEqual(params['v12'], 1)
        cloud.session.get.side_effect = cloud.requests.Timeout('must not log token')
        self.assertFalse(cloud.publish({}, metrics, {'sound': False, 'motion': False}, 'test'))

    def test_stale_data(self):
        state = State()
        state.values['cuff_mmhg'] = (120, 0)
        state.event('sound')
        values, samples, events, _ = state.snapshot()
        self.assertIsNone(values['cuff_mmhg'])
        self.assertTrue(events['sound'])
        self.assertFalse(state.snapshot()[2]['sound'])

if __name__ == '__main__':
    unittest.main()
