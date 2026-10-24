; Windows installer for AI Vision Edge (Inno Setup 6). Built by scripts\build-installer.ps1,
; which stages the files and passes SourceDir / AppVersion / OutputDir.
;
; The installer copies the app (with the pre-built dashboard, the detection model, MediaMTX, uv
; and WinSW), then runs scripts\install-service.ps1 as administrator, which downloads Python and
; the AI libraries into the install folder, creates the secrets and database, and registers the
; "AI Vision Edge" Windows service. Uninstalling removes the service; data is kept.

#ifndef SourceDir
  #error SourceDir is required (run scripts\build-installer.ps1)
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{FD53BA05-4DE3-4E0E-A8FC-D416E08388F0}
AppName=AI Vision Edge
AppVersion={#AppVersion}
AppPublisher=AI Vision Edge
DefaultDirName={sd}\AIVisionEdge
; A short path: Python libraries nest deeply and Windows limits paths to 260 characters.
DisableDirPage=no
DefaultGroupName=AI Vision Edge
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=AIVisionEdge-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\installer\icon.ico
UninstallDisplayName=AI Vision Edge
MinVersion=10.0

[Messages]
FinishedLabel=AI Vision Edge is installed and running as a Windows service.%n%nOpen the dashboard to create your admin account. If it doesn't load yet, wait a minute: the first start takes a little longer.

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\AI Vision Edge dashboard"; Filename: "http://127.0.0.1:8000"
Name: "{group}\Uninstall AI Vision Edge"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install-service.ps1"""; \
  StatusMsg: "Setting up: downloading Python and the AI libraries (about 1.5 GB) and starting the service. This takes several minutes..."; \
  Flags: runhidden waituntilterminated
Filename: "http://127.0.0.1:8000"; Description: "Open the dashboard"; Flags: shellexec postinstall skipifsilent nowait

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install-service.ps1"" -Remove"; \
  Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]
// The setup step is long; if it fails the user needs to know where to look.
procedure CurStepChanged(CurStep: TSetupStep);
var
  Log: String;
begin
  if CurStep = ssPostInstall then
  begin
    Log := ExpandConstant('{app}\storage\logs\install.log');
    if not FileExists(ExpandConstant('{app}\tools\winsw\AIVisionEdge.xml')) then
      MsgBox('The setup step did not finish. Details are in:' + #13#10 + Log, mbError, MB_OK);
  end;
end;
