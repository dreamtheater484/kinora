; Kinora's Windows installer, for the current user only (no administrator rights). It installs the program in
; %LOCALAPPDATA%\Programs\Kinora with a Start-menu shortcut to start.bat. The tools and models (about 25 GB) are not
; in it: install.ps1 downloads them on the first start, into the data folder %LOCALAPPDATA%\Kinora (paths.py).
;
; Build (Inno Setup 6), from the repository:
;   ISCC /DAppVersion=1.0.0 packaging\windows\kinora.iss      -> dist\Kinora-1.0.0-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{A669C7B0-34D8-4A35-9509-57EB9CF433D0}
AppName=Kinora
AppVersion={#AppVersion}
AppVerName=Kinora {#AppVersion}
AppComments=Offline upscaler and editor for old home videos
AppPublisherURL=https://github.com/dreamtheater484/kinora
SourceDir=..\..
OutputDir=dist
OutputBaseFilename=Kinora-{#AppVersion}-setup
PrivilegesRequired=lowest
DefaultDirName={autopf}\Kinora
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; app.py holds this mutex while Kinora runs, so setup and the uninstaller ask to close it first
AppMutex=KinoraApp
WizardStyle=modern
SetupIconFile=packaging\windows\kinora.ico
UninstallDisplayIcon={app}\kinora.ico
UninstallDisplayName=Kinora
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "app.py"; DestDir: "{app}"
Source: "editor.py"; DestDir: "{app}"
Source: "merge.py"; DestDir: "{app}"
Source: "paths.py"; DestDir: "{app}"
Source: "shrink.py"; DestDir: "{app}"
Source: "upscale.py"; DestDir: "{app}"
Source: "download_models.py"; DestDir: "{app}"
Source: "*.bat"; DestDir: "{app}"
Source: "install.ps1"; DestDir: "{app}"
Source: "firewall_block.ps1"; DestDir: "{app}"
Source: "README.md"; DestDir: "{app}"
Source: "refs\README.md"; DestDir: "{app}\refs"
Source: "tools\*.py"; DestDir: "{app}\tools"
Source: "tools\data_root.bat"; DestDir: "{app}\tools"
Source: "tools\requirements\*.txt"; DestDir: "{app}\tools\requirements"
Source: "ui\*"; DestDir: "{app}\ui"; Flags: recursesubdirs
Source: "packaging\installed.txt"; DestDir: "{app}"
Source: "packaging\windows\kinora.ico"; DestDir: "{app}"

[Icons]
Name: "{autoprograms}\Kinora"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; IconFilename: "{app}\kinora.ico"; Comment: "Upscale and edit old home videos"
Name: "{autodesktop}\Kinora"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; IconFilename: "{app}\kinora.ico"; Comment: "Upscale and edit old home videos"; Tasks: desktopicon

[Run]
Filename: "{app}\start.bat"; WorkingDir: "{app}"; Description: "Start Kinora (the first start downloads about 25 GB)"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; made by Python while Kinora runs
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\tools\__pycache__"

[Code]
function DataFolder: String;
begin
  Result := ExpandConstant('{localappdata}\Kinora');
end;

function Missing(Programs: TArrayOfString): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Programs) - 1 do
    if FileSearch(Programs[I] + '.exe', GetEnv('PATH')) = '' then
    begin
      if Result <> '' then
        Result := Result + ', ';
      Result := Result + Programs[I];
    end;
end;

// install.ps1 (the first start) needs git, uv and ffmpeg: say so before installing when they are not there yet
procedure InitializeWizard;
var
  NotFound: String;
begin
  NotFound := Missing(['git', 'uv', 'ffmpeg']);
  if NotFound <> '' then
    CreateOutputMsgPage(wpWelcome, 'Before the first start', 'Kinora needs three free tools that are not installed yet: ' + NotFound + '.',
      'The first start of Kinora uses git, uv and ffmpeg to download its tools and models (about 25 GB, only once). ' +
      'Install them in a terminal (Start menu, Terminal) with:' + #13#10#13#10 +
      '    winget install Git.Git astral-sh.uv Gyan.FFmpeg' + #13#10#13#10 +
      'You can install Kinora now and the tools afterwards; open a new terminal or sign out and in again before you start Kinora, so it finds them. ' +
      'Kinora also needs an NVIDIA graphics card with a current driver.');
end;

// The data folder is not part of the installation. Besides the downloaded tools and models it holds the user's own
// videos, results, edits and reference photos, so it is only deleted when the user says so (never in a silent uninstall).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DirExists(DataFolder) and not UninstallSilent then
    if MsgBox('Also delete Kinora''s data folder?' + #13#10#13#10 + DataFolder + #13#10#13#10 +
      'It holds the tools and models Kinora downloaded (about 25 GB) and also your videos, results, edits and reference photos.' + #13#10#13#10 +
      'Choose No to keep it, for example to install Kinora again later without downloading everything again.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      if not DelTree(DataFolder, True, True, True) then
        MsgBox('Not everything in ' + DataFolder + ' could be deleted. You can delete the rest yourself.', mbError, MB_OK);
end;
