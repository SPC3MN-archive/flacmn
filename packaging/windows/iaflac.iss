; Inno Setup script for the Windows installer. Build the PyInstaller folder first (dist\iaflac), then:
;   iscc /DAppVersion=0.1.0 packaging\windows\iaflac.iss
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\iaflac.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F4C2A0E-8E1B-4C7B-9C62-1A2F7D3E9B10}
AppName=iaflac
AppVersion={#AppVersion}
AppPublisher=iaflac
DefaultDirName={localappdata}\Programs\iaflac
DefaultGroupName=iaflac
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=iaflac-{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\iaflac.exe
#ifexist "..\icons\iaflac.ico"
SetupIconFile=..\icons\iaflac.ico
#endif

[Files]
Source: "..\..\dist\iaflac\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\iaflac"; Filename: "{app}\iaflac.exe"
Name: "{userdesktop}\iaflac"; Filename: "{app}\iaflac.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\iaflac.exe"; Description: "Start iaflac"; Flags: nowait postinstall skipifsilent

[Code]
// The window is drawn by Microsoft Edge WebView2. Windows 11 always has it; on Windows 10 it may be missing.
function WebView2Installed(): Boolean;
var v: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0');
  if not Result then
    Result := RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var code: Integer;
begin
  if (CurStep = ssPostInstall) and not WebView2Installed() then
    if MsgBox('iaflac needs the Microsoft Edge WebView2 Runtime, which is not installed. Open the download page now?',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, code);
end;
