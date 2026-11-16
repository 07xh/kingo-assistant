# KingoAssistant Lite · Windows UI alpha

This is a separate, experimental desktop shell. It does not replace the existing
packaged Tkinter Windows client or its tested capabilities.

## Scope

- Original React + Tauri 2 compact interface built with standard web components.
- Dashboard, local course plan table/editor, global settings overlay,
  operation log, collapsible sidebar and dark/light theme.
- Tauri commands delegate local course plan reads/writes to an isolated Python
  sidecar which reuses the existing validation and atomic save implementation.
- No login, online catalog, active monitoring, or automatic enrollment in this
  alpha UI. Unavailable controls and status are clearly labeled.
- LightC influenced the compact desktop layout. LightC application source,
  artwork, code and branding are NOT included. Its source-available license
  restricts redistribution of modified versions and binaries without permission.

## Windows development and packaging

Requires Python 3.12, Node.js 22, Rust, Visual Studio C++ Build Tools,
WebView2 and PyInstaller 6.

1. Install dependencies from clients/windows/source/requirements.txt.
2. Run from clients/windows/source:

   python -m PyInstaller --clean --onefile --noconfirm --name kingo-sidecar --distpath ../lite/packaging/sidecar --add-data "config.example.yaml;." lite_bridge.py

3. Run from clients/windows/lite:

   npm install
   npm --prefix frontend install
   npm run icon
   npm run build
   npm run tauri -- build

The NSIS bundle should appear below src-tauri/target/release/bundle/nsis.
Only the matching Windows CI artifact and packaged startup tests can establish
a verified build; no executable is provided by this document.

## Migration and safety

The Lite sidecar reads the existing local config (same app data directory as
the old Tk client). Only whitelisted public course fields are returned to
React. Saving uses the original local config validation, strict course count
and the original atomic save routine. It never exports credentials or sends
school requests. The existing Tk client remains the real monitoring and
advanced tooling UI until proper parity is implemented and tested.

Next milestones: native account/session readiness in the UI; authorized
catalog read; monitor worker status/stop and tray lifecycle; logs and
advanced tools; parity tests; actual Windows acceptance; finally promote
the new shell. Do not remove the original GUI before those gates pass.
