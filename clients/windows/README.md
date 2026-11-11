# Windows client

The current baseline is KingoAssistant Windows V4.6.

The first run of the repository's **Windows standalone EXE** workflow expands `kingo-windows-v4.6.tar.xz` into `source/`, commits the expanded source tree to the same branch, runs offline tests, builds the EXE on a Windows runner, and verifies the packaged EXE with offline-only flags.

For a local Windows build after extracting the snapshot:

1. Install Python 3.10 or newer.
2. Extract the contents of `kingo-windows-v4.6.tar.xz` into a folder.
3. Run `build-windows.bat`.
4. Inspect `dist/build-verification.json` and `dist/SHA256.txt`.

Current scope is authorized read-only course monitoring and reminders. Live NCWU API integration and enrollment submission are not verified or implemented.
