#define AppName "Tel-Agent"
#define AppVersion GetEnv("TEL_AGENT_VERSION")
#define Stage GetEnv("TEL_AGENT_STAGE")

[Setup]
AppId={{3D495CDD-63FA-4E4F-9A9E-1DEFD2B8D8AF}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\Tel-Agent
DefaultGroupName=Tel-Agent
DisableProgramGroupPage=yes
PrivilegesRequired=admin
UninstallDisplayName=Tel-Agent
OutputDir=..\..\dist
OutputBaseFilename=Tel-Agent-{#AppVersion}-windows-x64-unsigned
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "{#Stage}\app\*"; DestDir: "{app}"; Excludes: ".env"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\app\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Run]
Filename: "{app}\TelAgentService.exe"; Parameters: "install"; Flags: runhidden waituntilterminated
Filename: "{app}\TelAgentService.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/Create /F /SC DAILY /ST 03:00 /RU SYSTEM /TN ""Tel-Agent Update"" /TR ""{app}\update.cmd"""; Flags: runhidden waituntilterminated
Filename: "http://localhost:38471"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
Filename: "{app}\TelAgentService.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated
Filename: "{app}\TelAgentService.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN ""Tel-Agent Update"""; Flags: runhidden waituntilterminated

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then begin
    Exec(ExpandConstant('{app}\TelAgentService.exe'), 'stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
  if CurStep = ssPostInstall then begin
    ForceDirectories(ExpandConstant('{commonappdata}\Tel-Agent\data'));
    MsgBox('Tel-Agent will generate its encryption key as the service starts. Back up {app}\\.env separately from the database; losing that key makes stored credentials unrecoverable.', mbInformation, MB_OK);
  end;
end;
