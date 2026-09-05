# Packaging and updates

Tel-Agent installs as a local web application: the installer starts the local service
and opens the dashboard in the browser at `http://localhost:38471`. It is not a separate desktop application.

## Supported delivery paths

| Platform | Delivery | State |
| --- | --- | --- |
| Windows x64 | Branded unsigned `.exe` installer | In progress |
| Debian / Ubuntu | `.deb` package | Available from a release |
| Fedora / RHEL | `.rpm` package | Available from a release |
| Docker | Published container images | Available from a release |
| macOS | Signed-later `.pkg` installer | In progress |
| Shared hosting control panels | — | Unsupported; the application needs long-running services and WebSockets |

## Windows upgrades

The Windows installer is self-contained: it carries the Python and Node runtimes, the
API, and the dashboard. It installs the API as a Windows service and opens the browser
at `http://localhost:38471`.

The installer creates the SQLite database under `ProgramData\\Tel-Agent\\data` and
creates the encryption key only on the first run. An upgrade stops the service, replaces
the application files, then starts it again. It does not replace `.env`, the database,
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
