"""Experimental PPG processing. Not a validated medical algorithm."""
import numpy as np
from scipy.signal import butter, sosfiltfilt, find_peaks


def ppg_metrics(samples, cfg):
    result = {'pulse_bpm': None, 'spo2_pct': None, 'ratio': None,
              'ppg_status': 'warming up'}
    fs = 100
    if len(samples) < fs * 8:
        return result
    data = np.asarray(samples, dtype=float)
    if data.ndim != 2 or data.shape[1] != 2 or not np.isfinite(data).all():
        result['ppg_status'] = 'invalid samples'
        return result
    dc = data.mean(axis=0)
    if np.any(dc < cfg['min_dc_counts']) or np.any(data >= 65000):
        result['ppg_status'] = 'no finger / weak signal / clipping'
        return result
    sos = butter(3, [0.5, 4.0], fs=fs, btype='bandpass', output='sos')
    ac = sosfiltfilt(sos, data, axis=0)[fs:-fs]  # remove edge transients
    rms = np.sqrt(np.mean(ac ** 2, axis=0))
    if np.any(rms / dc < cfg['min_perfusion_fraction']):
        result['ppg_status'] = 'insufficient pulsatile signal'
        return result
    if np.corrcoef(ac.T)[0, 1] < 0.8:
        result['ppg_status'] = 'red/IR mismatch or movement'
        return result
    peaks, _ = find_peaks(ac[:, 0], distance=int(fs * 60 / 220),
                          prominence=float(rms[0] * 0.6))
    intervals = np.diff(peaks) / fs
    if len(intervals) < 3 or np.std(intervals) / np.mean(intervals) > 0.20:
        result['ppg_status'] = 'irregular or insufficient peaks'
        return result
    bpm = 60 / np.median(intervals)
    if not 40 <= bpm <= 220:
        result['ppg_status'] = 'pulse outside algorithm range'
        return result
    ratio = (rms[1] / dc[1]) / (rms[0] / dc[0])
    result.update(pulse_bpm=round(float(bpm), 1), ratio=round(float(ratio), 4),
                  ppg_status='experimental pulse; SpO2 uncalibrated')
    # No invented default coefficients. Fit A and B to your optical assembly
    # against an appropriate reference before enabling this research estimate.
    cal = cfg.get('spo2_calibration')
    if cal is not None:
        if cal['ratio_min'] <= ratio <= cal['ratio_max']:
            spo2 = cal['a'] + cal['b'] * ratio
            if 0 <= spo2 <= 100:
                result['spo2_pct'] = round(float(spo2), 1)
                result['ppg_status'] = 'experimental pulse and SpO2 estimates'
            else:
                result['ppg_status'] = 'SpO2 outside calibration output range'
        else:
            result['ppg_status'] = 'ratio outside calibration domain'
    return result
