import asyncio
import socket
import struct
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime


class TimeSync:
    def __init__(self):
        self.offset_ms = 0.0
        self.uncertainty_ms = float('inf')
        self.source = '未校时'
        self.anchor = time.time()
        self.tick = time.monotonic()

    @staticmethod
    def parse(value):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.timestamp()

    def now(self):
        return self.anchor + time.monotonic() - self.tick

    @staticmethod
    def _ntp(host):
        epoch = 2208988800
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.connect((host, 123))
            packet = bytearray(48)
            packet[0] = 0x23
            t1 = time.time()
            stamp = t1 + epoch
            struct.pack_into('!II', packet, 40, int(stamp), int((stamp % 1) * 2**32))
            sock.send(packet)
            data = sock.recv(512)
            t4 = time.time()
        if len(data) < 48 or data[0] >> 6 == 3 or data[0] & 7 != 4:
            raise ValueError('无效 NTP 响应')
        if not 1 <= data[1] <= 15 or data[24:32] != packet[40:48]:
            raise ValueError('NTP stratum/origin 校验失败')
        def timestamp(start):
            sec, fraction = struct.unpack_from('!II', data, start)
            return sec - epoch + fraction / 2**32
        t2, t3 = timestamp(32), timestamp(40)
        delay = (t4 - t1) - (t3 - t2)
        if delay < -0.01 or t3 < t2:
            raise ValueError('NTP 时间戳异常')
        dispersion = struct.unpack_from('!I', data, 8)[0] / 65536
        return ((t2-t1)+(t3-t4))/2, max(0, delay)/2 + dispersion

    async def sync(self, http, url, referer, *, budgeted_get=None):
        samples = []
        for _ in range(3):
            try:
                samples.append(await asyncio.to_thread(self._ntp, 'ntp.aliyun.com'))
            except (OSError, ValueError):
                continue
        if samples:
            offset, error = min(samples, key=lambda x: x[1])
            self.source = 'NTP（UTC，并非教务业务时钟）'
        else:
            before = time.time()
            if budgeted_get is None:
                raise ValueError('学校 HTTP Date 校时必须使用统一预算请求入口')
            response = await budgeted_get(url, referer)
            after = time.time()
            response.raise_for_status()
            if response.headers.get('Age', '0') != '0':
                raise ValueError('HTTP Date 来自缓存，拒绝校时')
            server = parsedate_to_datetime(response.headers['Date']).timestamp()
            offset = server + 0.5 - (before + after)/2
            error = 0.5 + (after-before)/2
            self.source = 'HTTP Date（秒级粗校时）'
        self.offset_ms, self.uncertainty_ms = offset*1000, error*1000
        self.anchor, self.tick = time.time() + offset, time.monotonic()

    async def wait_until(self, target_time_str, advance_ms=400):
        deadline = self.parse(target_time_str) - advance_ms/1000
        while True:
            remaining = deadline - self.now()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.5, remaining))
