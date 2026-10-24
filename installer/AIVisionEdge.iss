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
; The setup step (scripts\install-service.ps1) runs from [Code] below so the wizard can show its progress.
Filename: "http://127.0.0.1:8000"; Description: "Open the dashboard"; Flags: shellexec postinstall skipifsilent nowait

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install-service.ps1"" -Remove"; \
  Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]
// The setup step downloads Python and the AI libraries, which takes minutes. It runs in the
// background while the wizard shows its real progress: install-service.ps1 writes
// storage\logs\install-progress.txt ("percent|counter|text") every 2 s and install-result.txt when done.
type
  TMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: Longint;
    lParam: Longint;
    time: DWORD;
    ptX: Longint;
    ptY: Longint;
  end;

function PeekMessage(var lpMsg: TMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: UINT): BOOL;
  external 'PeekMessageW@user32.dll stdcall';
function TranslateMessage(const lpMsg: TMsg): BOOL;
  external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const lpMsg: TMsg): Longint;
  external 'DispatchMessageW@user32.dll stdcall';
function GetTickCount: DWORD;
  external 'GetTickCount@kernel32.dll stdcall';

// Keeps the wizard responsive (repaints, the progress bar) while we wait.
procedure PumpMessages;
var
  Msg: TMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, 1) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

procedure RunSetupStep;
var
  Logs, ProgressFile, ResultFile, PowerShell, Line, LastLine, Text, Outcome: String;
  Raw: AnsiString;
  Bar, Code, Started, LastChange: Integer;
  i: Integer;
begin
  Logs := ExpandConstant('{app}\storage\logs');
  ProgressFile := Logs + '\install-progress.txt';
  ResultFile := Logs + '\install-result.txt';
  DeleteFile(ProgressFile);
  DeleteFile(ResultFile);
  // Setup itself is 32-bit: Sysnative reaches the 64-bit PowerShell.
  PowerShell := ExpandConstant('{win}\Sysnative\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(PowerShell) then
    PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');

  WizardForm.StatusLabel.Caption := 'Setting up AI Vision Edge...';
  WizardForm.FilenameLabel.Caption := 'Starting';
  WizardForm.ProgressGauge.Min := 0;
  WizardForm.ProgressGauge.Max := 100;
  WizardForm.ProgressGauge.Position := 0;
  if not ShellExec('', PowerShell,
      '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\install-service.ps1') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewNoWait, Code) then
  begin
    MsgBox('Could not start the setup step (error ' + IntToStr(Code) + ').', mbError, MB_OK);
    Exit;
  end;

  Started := GetTickCount;
  LastChange := Started;
  LastLine := '';
  while not FileExists(ResultFile) do
  begin
    if LoadStringFromFile(ProgressFile, Raw) then
    begin
      // "percent|counter|text"; the counter changes every 2 s while the step is alive.
      Line := String(Raw);
      if Line <> LastLine then
      begin
        LastLine := Line;
        LastChange := GetTickCount;
      end;
      i := Pos('|', Line);
      if i > 0 then
      begin
        Bar := StrToIntDef(Copy(Line, 1, i - 1), -1);
        Text := Copy(Line, i + 1, Length(Line));
        i := Pos('|', Text);
        Text := Trim(Copy(Text, i + 1, Length(Text)));
        if (Bar >= 0) and (Bar <= 100) then WizardForm.ProgressGauge.Position := Bar;
        WizardForm.FilenameLabel.Caption := Text;
      end;
    end;
    for i := 1 to 10 do
    begin
      PumpMessages;
      Sleep(50);
    end;
    // A slow connection can take long; after 3 hours something is wrong.
    if GetTickCount - Started > 3 * 60 * 60 * 1000 then break;
    // No progress update for 5 minutes: the setup step has stopped.
    if GetTickCount - LastChange > 5 * 60 * 1000 then break;
  end;

  Outcome := '';
  if LoadStringFromFile(ResultFile, Raw) then Outcome := Trim(String(Raw));
  if Outcome = 'ok' then
  begin
    WizardForm.ProgressGauge.Position := 100;
    WizardForm.FilenameLabel.Caption := 'Done';
  end
  else
  begin
    if Outcome = '' then Outcome := 'it did not finish.';
    MsgBox('The setup step failed: ' + Outcome + #13#10#13#10 +
      'Details are in:' + #13#10 + Logs + '\install.log' + #13#10#13#10 +
      'Running this installer again usually resumes where it stopped.', mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then RunSetupStep;
end;
