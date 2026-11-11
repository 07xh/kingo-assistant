# Android client (planned)

The Android client is planned as an independent application. It will not require the Windows EXE or a cloud server to perform its own supported tasks.

Planned principles:

- Kotlin-native implementation and Android lifecycle handling.
- Independent account/session handling using official, authorized access only.
- Shared course/status data contracts with the Windows client where practical.
- Separate Android CI, signing, and release pipeline.
- No CAPTCHA bypass, access-control bypass, rate-limit evasion, or hidden high-frequency requests.

**Status:** planning only. No Android source code or APK is currently included.
