# Packaging and updates

Tel-Agent installs as a local web application: the installer starts the local service
and opens the dashboard in the browser at `http://localhost:38471`. It is not a separate desktop application.

## Supported delivery paths

| Platform | Delivery | State |
| --- | --- | --- |
| Windows x64 | Unsigned `.exe` installer | Available from a release (v0.1.1; v0.1.2 adds the dashboard service) |
| Debian / Ubuntu | `.deb` package | Available from a release |
| Fedora / RHEL | `.rpm` package | Available from a release |
| Docker | Published container images | Available from a release |
| macOS | Unsigned `.pkg` installer, Apple Silicon and Intel | Available from a release (v0.1.1) |
| Shared hosting control panels | — | Unsupported; the application needs long-running services and WebSockets |

## Windows upgrades

The Windows installer is self-contained: it carries the Python and Node runtimes, the
API, and the dashboard. It installs two Windows services - the API (`TelAgent`, port
38472) and the dashboard (`TelAgentWeb`, port 38471, dependent on the API), both
loopback only - and opens the browser at `http://localhost:38471` once the dashboard
answers. The wizard shows nothing but a progress bar: every setup screen lives in the
product at `/install`, so Windows, macOS, Linux and Docker users see the same screens
in the same five languages.

The installer creates the SQLite database under `ProgramData\\Tel-Agent\\data` and
creates the encryption key only on the first run. An upgrade stops both services, replaces
the application files, then starts them again. It does not replace `.env`, the database,
or the encryption key, so an update is not a new installation.

The update task checks GitHub Releases once daily. It accepts only a newer published
version-tagged installer, verifies the release asset SHA-256 when GitHub provides it,
and runs the installer silently. It never installs directly from `main`: a branch
commit is not a product release and has not passed the release gate.

## macOS

macOS follows the same local-service model as Windows and Linux: a `.pkg` installs
the bundled runtimes, a LaunchDaemon starts the API and dashboard, and the installer
opens the dashboard in the browser. The first package is unsigned and not notarized;
signing and notarization are a later release-hardening step, not a prerequisite for
the installer architecture.
