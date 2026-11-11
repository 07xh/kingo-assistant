# Official-site appointment fallback (Windows V5.2)

This is a reliable **manual selection fallback**, not a verified NCWU automatic enrollment adapter.

### What works before the selection window

1. Open the existing Windows client.
2. In **编辑课程和设置**, enter your actual course code or course name, teaching-class ID if known, and order your alternatives.
3. Enter the *exact start time* from the school notice for your year, campus, and category, in **北京时间 YYYY-MM-DD HH:MM:SS**.
4. Press **预约按时打开官网（辅助）** and explicitly confirm that you checked the official notice.
5. Keep Windows awake and keep the client open. The GUI displays a countdown and attempts to open only the fixed official website `https://jwmis.ncwu.edu.cn` once at the specified moment.
6. Complete login, teaching-class selection, submission, and final result confirmation on the official page, using the existing course-ID copy buttons.

### Limits

- Does not load signed Xiqueer API requests, scrape enrollment endpoints, supply authentication material, bypass CAPTCHA, or submit any enrollment.
- Does not treat a school-wide date as the exact opening hour for a student.
- Only works while the desktop application remains open and the computer is awake. If it was asleep, the next GUI tick may open the site late, not earlier.
- Checks only the local clock; the institution's actual selection window and availability remain authoritative.
- The template's example date/time is not confirmed for the student's next round. **Edit and re-confirm it.**
- Can be canceled in one click and triggers at most one browser-opening attempt.
- The independent scheduled enrollment engine remains unavailable for real submissions until a separately verified, authorized NCWU adapter exists.

### Test acceptance

Synthetic offline tests validate China-local time conversion, date formats, time horizon, required course preferences, opt-in confirmation, cancellation, one-shot launch, no launch during close, and invalid-template rejection. Native packaged GUI smoke tests verify the EXE starts.
