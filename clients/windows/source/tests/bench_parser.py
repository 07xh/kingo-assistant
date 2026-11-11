"""Synthetic local benchmark. NOT a campus-server load test."""
import statistics
import time
from core.client import parse_courses

html = ('<table>' + ''.join(
    f'<tr><td>TEST{i:05d}</td><td>测试课程{i}</td><td>教师</td><td>{i % 3}</td></tr>'
    for i in range(200)) + '</table>').encode('gbk')
config = {'columns': {'course_id': 0, 'course_name': 1, 'teacher': 2, 'remain': 3}}
for _ in range(20):
    parse_courses(html, config, ['TEST00123'])
times = []
for _ in range(500):
    start = time.perf_counter_ns()
    rows = parse_courses(html, config, ['TEST00123'])
    times.append((time.perf_counter_ns() - start) / 1e6)
assert len(rows) == 1 and rows[0].course_id == 'TEST00123'
print(f'Offline synthetic rows=200; target-only parser median={statistics.median(times):.3f}ms '
      f'p95={sorted(times)[int(len(times)*.95)]:.3f}ms')
