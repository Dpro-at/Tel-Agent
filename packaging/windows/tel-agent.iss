; The unsigned Windows installer. Deliberately near-silent: it unpacks the bundled
; runtimes, registers the two services, and opens the dashboard - every screen a user
; actually interacts with lives in the product, at /install, so Windows, macOS, Linux
; and Docker users see the same setup in the same five languages. No wizard pages
; beyond the progress bar; no message boxes (the encryption-key notice belongs to the
; first-run screen, where it can be read in the user's language).

#define AppName "Tel-Agent"
#define AppVersion GetEnv("TEL_AGENT_VERSION")
#define Stage GetEnv("TEL_AGENT_STAGE")

[Setup]
AppId={{3D495CDD-63FA-4E4F-9A9E-1DEFD2B8D8AF}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Dpro GmbH
AppPublisherURL=https://tel-agent.com
DefaultDirName={autopf}\Tel-Agent
DefaultGroupName=Tel-Agent
PrivilegesRequired=admin
UninstallDisplayName=Tel-Agent
OutputDir=..\..\dist
OutputBaseFilename=Tel-Agent-{#AppVersion}-windows-x64-unsigned
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
ShowLanguageDialog=no
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes

[Files]
Source: "{#Stage}\app\*"; DestDir: "{app}"; Excludes: ".env"; Flags: recursesubdirs ignoreversion
; The operator's configuration survives upgrades: written once, never overwritten.
Source: "{#Stage}\app\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Run]
; API first, dashboard second - the dashboard service declares a dependency on the
; API, so Windows enforces the same order on every boot.
Filename: "{app}\TelAgentService.exe"; Parameters: "install"; Flags: runhidden waituntilterminated
Filename: "{app}\TelAgentWebService.exe"; Parameters: "install"; Flags: runhidden waituntilterminated
Filename: "{app}\TelAgentService.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{app}\TelAgentWebService.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/Create /F /SC DAILY /ST 03:00 /RU SYSTEM /TN ""Tel-Agent Update"" /TR ""{app}\update.cmd"""; Flags: runhidden waituntilterminated
; Opens the browser once the dashboard answers (not from a Finished page - there is
; none). Skipped for silent runs, which is how the daily updater re-installs.
Filename: "{app}\open-dashboard.cmd"; Flags: runhidden nowait skipifsilent

[UninstallRun]
Filename: "{app}\TelAgentWebService.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopWeb"
Filename: "{app}\TelAgentService.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopApi"
Filename: "{app}\TelAgentWebService.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveWeb"
Filename: "{app}\TelAgentService.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveApi"
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN ""Tel-Agent Update"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[Code]
procedure StopIfPresent(const Wrapper: string);
var
  ResultCode: Integer;
begin
  { An upgrade finds the previous version's services running; stop them before the
    files underneath are replaced. A first install has nothing to stop and this is a
    no-op - the wrapper is not there yet. }
  if FileExists(Wrapper) then
    Exec(Wrapper, 'stop', ExtractFileDir(Wrapper), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then begin
    StopIfPresent(ExpandConstant('{app}\TelAgentWebService.exe'));
    StopIfPresent(ExpandConstant('{app}\TelAgentService.exe'));
  end;
  if CurStep = ssPostInstall then begin
    // Conversations and the SQLite database live under ProgramData, outside the
    // application directory, so an uninstall or an upgrade never touches them.
    // (A brace comment cannot hold a {constant}: the first close brace ends it.)
    ForceDirectories(ExpandConstant('{commonappdata}\Tel-Agent\data'));
  end;
end;
