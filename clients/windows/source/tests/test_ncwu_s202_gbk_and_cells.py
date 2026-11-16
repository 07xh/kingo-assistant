"""Synthetic S202 DataTable regression fixtures, including GBK form encoding."""
import asyncio
from urllib.parse import parse_qs
from unittest import TestCase

import httpx

from core.ncwu_elective_table import EXPECTED_HEADINGS
from core.ncwu_live_elective import _parse_catalog, live_s202_snapshot


def table_html(*, named=False, order=None):
    order = tuple(order or EXPECTED_HEADINGS)
    headers = "".join(f"<th id='{k}'>{k}</th>" for k in order)
    field_values = {
        "head_kc": "高等数学", "head_kcdm": "TEST_COURSE",
        "head_skbh": "CLASS_NO", "head_rkjs": "测试教师",
        "head_xk_status": "未知", "head_xk_points": "0",
    }
    if named:
        data = "".join(
            f"<td id='tr7_{k[5:]}'>{field_values.get(k, '')}</td>"
            for k in order
        )
        tr = f"<tr id='tr7'>{data}</tr>"
    else:
        data = "".join(f"<td>{field_values.get(k, '')}</td>" for k in order)
        tr = f"<tr>{data}</tr>"
    return "<html><table><tr>" + headers + "</tr>" + tr + "</table></html>"


class S202TableParsers(TestCase):
    def test_reordered_header_ids_override_static_offsets(self):
        courses, count = _parse_catalog(
            table_html(order=tuple(reversed(EXPECTED_HEADINGS))))
        self.assertEqual(count, 17)
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].course_code, "TEST_COURSE")
        self.assertEqual(courses[0].class_number, "CLASS_NO")
        self.assertEqual(courses[0].course_name, "高等数学")
        self.assertFalse(hasattr(courses[0], "remain"))

    def test_row_id_fields_resolve_out_of_order_cells(self):
        order = tuple(reversed(EXPECTED_HEADINGS))
        courses, count = _parse_catalog(table_html(named=True, order=order))
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].teacher, "测试教师")
        self.assertEqual(courses[0].selection_status, "未知")
        self.assertEqual(courses[0].class_number, "CLASS_NO")

    def test_unlabelled_continuation_row_is_not_a_course(self):
        html = table_html().replace(
            "</table>", "<tr class='E'><td>空</td></tr></table>")
        courses, count = _parse_catalog(html)
        self.assertEqual(len(courses), 1)

    def test_missing_header_does_not_synthesize_data(self):
        html = table_html().replace("head_kcdm", "unknown_column")
        with self.assertRaises(ValueError):
            _parse_catalog(html)

    def test_live_catalog_posts_gbk_encoded_chinese_form_values(self):
        page = (
            "<html><meta charset='GBK'><form id='ActionForm' method='post'>"
            "<input name='xktype' value='2'>"
            "<input name='xn' value='2026'><input name='xq' value='0'>"
            "<input name='xh' value='LOCAL_TEST_USER'>"
            "<input name='lcid' value='LOCAL_TEST_ROUND'>"
            "<input name='hidkcmc' value='高等数学'>"
            "</form></html>"
        ).encode("gbk")
        requests = []
        def transport(req):
            requests.append(req)
            if req.url.path == "/hsjw/student/wsxk.zx.html":
                return httpx.Response(
                    200, content=page,
                    headers={"content-type": "text/html; charset=GBK"})
            if req.url.path == "/hsjw/jw/common/getWsxkTimeRange.action":
                return httpx.Response(200, json={
                    "status": "400",
                    "message": "当前时间不是有效的选课时间区段！",
                })
            if req.url.path == "/hsjw/taglib/DataTable.jsp":
                body = req.content.decode("ascii")
                self.assertNotIn("%E9%AB%98", body)
                parsed = parse_qs(body, encoding="gbk", errors="strict")
                self.assertEqual(parsed["hidkcmc"], ["高等数学"])
                self.assertIn("charset=GBK", req.headers["content-type"])
                return httpx.Response(
                    200, content=table_html().encode("gbk"),
                    headers={"content-type": "text/html; charset=GBK"})
            self.fail("Unexpected path " + req.url.path)
        result = asyncio.run(live_s202_snapshot(
            {"base_url": "https://jwmis.ncwu.edu.cn"},
            session_cookie="JSESSIONID=LOCAL_TEST_SESSION",
            http_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(transport),
                follow_redirects=False),
            enforce_manual_cooldown=False,
        ))
        self.assertEqual(result.code, "catalog_read")
        self.assertEqual(len(result.courses), 1)
        self.assertEqual([r.method for r in requests], ["GET", "POST", "POST"])


if __name__ == "__main__":
    import unittest
    unittest.main()
