# KingoAssistant Lite · Windows UI alpha

This is a separate, experimental desktop shell. It does not replace the existing
packaged Tkinter Windows client or its tested capabilities.

## Scope

- Original React + Tauri 2 compact interface built with standard web components.
- Dashboard, local course plan table/editor, global settings overlay,
  operation log, collapsible sidebar and dark/light theme.
- Tauri commands delegate local course plan reads/writes to an isolated Python
  sidecar which reuses the existing validation and atomic save implementation.
- One explicitly initiated NCWU signed login reuses the existing first-party
  adapter. The app-owned session and login cooldown remain inside one long-lived
  Python child. Neither passwords nor cookies are returned to React.
- Optional **Remember this PC** (off by default): store the school-issued
  session token and account identifier encrypted using Windows CurrentUser
  DPAPI, never the password. The encrypted file lives under
  `%LOCALAPPDATA%/KingoAssistantReadOnly/official-session.dpapi` and is
  locally eligible for at most 7 days; the school's own session expiration
  always takes precedence. It is tied to the current Windows account.
- **Passwordless continue** is a user-clicked action, not silent login:
  restores only if the existing official YearTerm route and identity-binding
  contract are verified and the school's allowed request budget is set.
  It compares the official userCode to the remembered account in memory.
  If unverified, restricted or offline, the application does not claim login
  success. If expired or identity-mismatched, the stored token is revoked.
  Explicit Logout or Forget deletes the local encrypted session file.
- A manually initiated S202 read-only course snapshot reuses the existing
  source adapter and cooldown. It displays only real source rows and labels
  unknown seat counts clearly; no fake results or auto refresh.
- Start, stop and aggregate status of the *existing read-only MonitorWorker*
  are now wired through the private session process. All original protocol,
  access-policy and rate-limit gates must pass before any monitoring starts.
  This is not an enrollment engine or an automatic submission capability.
- The current alpha has no tray persistence. Closing the window ends its
  private session and its monitor. Real-account and installer acceptance are
  still pending.
- LightC influenced the compact desktop layout. LightC application source,
  artwork, code and branding are NOT included. Its source-available license
  restricts redistribution of modified versions and binaries without permission.

## Windows development and packaging

Requires Python 3.12, Node.js 22, Rust, Visual Studio C++ Build Tools,
WebView2 and PyInstaller 6.

1. Install dependencies from clients/windows/source/requirements.txt.
2. Run from clients/windows/source:

   python -m PyInstaller --clean --onefile --noconfirm --name kingo-sidecar --distpath ../lite/packaging/sidecar --add-data "config.example.yaml;." --collect-submodules pystray lite_bridge.py

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
and the original atomic save routine. Local plan management and preflight do
not make network calls. **Only user-triggered login, catalog reads and
validated monitor operations may access the school**. The original Tk client
remains intact and available until full feature parity is tested.

Known limitations: the NCWU signed-login adapter and DPAPI restoration must
be checked on the user's Windows PC. A stored cookie is not an indefinite
login session, and cannot skip school reauthentication after expiration.
The official SSO/CAS and Xiqueer alternatives have no verified EXE-native
session handoff contract here, so the app does not advertise fake QR login.
Also: a positive login-time identity proof is not
a persistent guarantee; S202 rows do not establish remaining seats or actual
enrollment; no login challenge or captcha is automatically solved; no
enrollment submission adapter is enabled.

Next milestones: tray persistence with safe shutdown, authorized timetable
and selected-result reads, native runtime diagnostics, legacy tools,
parity tests, actual Windows acceptance and release. Do not remove the
original GUI before those gates pass.
