"""Raspberry Pi sensor drivers. GPIO numbers are BCM numbers."""
import time


def decode_hsc(data, pmin=0.0, pmax=600.0):
    """HSC transfer function A: 10..90% of 14-bit counts; output in mbar."""
    if len(data) != 2:
        raise ValueError('Expected two pressure bytes')
    status = data[0] >> 6
    if status:
        raise ValueError({1: 'command mode', 2: 'stale pressure',
                          3: 'pressure diagnostic fault'}[status])
    raw = ((data[0] & 0x3f) << 8) | data[1]
    if not 1638 <= raw <= 14745:
        raise ValueError('Pressure outside calibrated range')
    return (raw - 1638) * (pmax - pmin) / (14745 - 1638) + pmin


class HoneywellHSC:
    """HSCDANN600MGSA3: 3.3V, SPI, 0..600 mbar gauge, transfer A.

    Manual CS on BCM25 ensures Honeywell's minimum CS setup delay.
    Do not connect sensor CS to CE0 as well. MOSI is not connected.
    """
    def __init__(self, cfg):
        import spidev
        from gpiozero import DigitalOutputDevice
        self.cs = DigitalOutputDevice(cfg['cs_gpio'], initial_value=True)
        self.spi = spidev.SpiDev()
        try:
            self.spi.open(cfg['bus'], cfg['device'])
            self.spi.mode = 0
            self.spi.max_speed_hz = 100000
            self.spi.no_cs = True
        except Exception:
            self.spi.close()
            self.cs.close()
            raise
        self.pmin, self.pmax = cfg['min_mbar'], cfg['max_mbar']

    def read_mmhg(self):
        self.cs.off()
        try:
            time.sleep(0.000005)  # >= 2.5 us select-to-clock setup
            data = self.spi.xfer2([0, 0])
        finally:
            self.cs.on()
        return decode_hsc(data, self.pmin, self.pmax) * 0.750061683

    def close(self):
        self.cs.on()
        self.spi.close()
        self.cs.close()


class MAX30100:
    """New minimal driver, checked against the MAX30100 register map.

    Produces raw IR/red pairs, NOT bpm or SpO2 percentages.
    100 samples/s, 1600us pulse width, 16-bit high-resolution, 11mA LEDs.
    """
    def __init__(self, bus=1, address=0x57):
        from smbus2 import SMBus
        self.bus = SMBus(bus)
        self.address = address
        try:
            if self.read(0xff) != 0x11:
                raise RuntimeError('Expected MAX30100 part ID 0x11; check model/wiring')
            self.write(0x06, 0x40)
            deadline = time.monotonic() + 1
            while self.read(0x06) & 0x40:
                if time.monotonic() > deadline:
                    raise RuntimeError('MAX30100 reset timeout')
                time.sleep(0.01)
            self.write(0x07, 0x47)  # high res | sample-rate code 1 << 2 | PW code 3
            self.write(0x09, 0x33)  # red and IR current code 3
            self.clear_fifo()
            self.write(0x06, 0x03)  # red + IR mode
        except Exception:
            self.bus.close()
            raise

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def clear_fifo(self):
        for reg in (0x02, 0x03, 0x04):
            self.write(reg, 0)

    def read_samples(self):
        if self.read(0x03) & 0x0f:
            self.clear_fifo()
            raise ValueError('MAX30100 FIFO overflow; waveform window discarded')
        n = ((self.read(0x02) & 15) - (self.read(0x04) & 15)) & 15
        samples = []
        for _ in range(n):
            b = self.bus.read_i2c_block_data(self.address, 0x05, 4)
            samples.append(((b[0] << 8) | b[1], (b[2] << 8) | b[3]))
        return samples

    def close(self):
        try:
            self.write(0x06, 0x80)
        finally:
            self.bus.close()
