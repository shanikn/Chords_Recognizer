; Inno Setup script for ChordChart. Built by:
;     uv run python packaging/build.py --variant lite|full --installer
; build.py passes the variant's name, AppId and file names (packaging/variants.py); the
; defaults below are the lite app. Per-user install (no admin prompt):
; %LOCALAPPDATA%\Programs\<AppName>. The variants have different AppIds, so both can be
; installed side by side; they share %LOCALAPPDATA%\ChordChart (downloads, caches).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppName
  #define AppName "ChordChart"
#endif
#ifndef AppId
  #define AppId "{{8E4B6C2A-6F2D-4C1E-9B7A-3C5D2E1F0A9B}"
#endif
#ifndef FileStem
  #define FileStem "ChordChart"
#endif
#ifndef Variant
  #define Variant "lite"
#endif
; The other variant: both share %LOCALAPPDATA%\ChordChart, so uninstalling one must not
; delete it while the other is installed. OtherAppKey is its uninstall registry key.
#ifndef OtherAppName
  #define OtherAppName "ChordChart Notes"
#endif
#ifndef OtherAppKey
  #define OtherAppKey "{3F7D1B9E-2C4A-4E8B-A6D5-9B1E7C3F2A48}_is1"
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ChordChart
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=out
OutputBaseFilename={#FileStem}-Setup-{#AppVersion}
SetupIconFile=chordchart.ico
UninstallDisplayIcon={app}\ChordChart.exe
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a {#AppName} icon on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The license notices, also at the top level so they're easy to find.
Source: "build\{#Variant}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ChordChart.exe"
Name: "{group}\Third-party licenses"; Filename: "{app}\licenses\THIRD-PARTY-NOTICES.txt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ChordChart.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ChordChart.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
// On uninstall, offer to delete downloaded songs, cached analyses, models, updates and
// logs (%LOCALAPPDATA%\ChordChart). A silent uninstall keeps them. If the other variant
// is still installed it uses the same folder, so it's kept and the user is told why.
function OtherVariantInstalled(): Boolean;
begin
  Result := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#OtherAppKey}')
    or RegKeyExists(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#OtherAppKey}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ChordChart');
    if not DirExists(DataDir) then
      Exit;
    if OtherVariantInstalled() then
    begin
      Log('Shared data kept: {#OtherAppName} is still installed and uses ' + DataDir);
      if not UninstallSilent then
        MsgBox('Your downloaded songs and saved results are kept, because {#OtherAppName} ' +
               'is still installed and uses them.' + #13#10 + '(' + DataDir + ')',
               mbInformation, MB_OK);
    end
    else if UninstallSilent then
      Log('Shared data kept (silent uninstall): ' + DataDir)
    else if MsgBox('Also delete the songs ChordChart downloaded and its saved results?' + #13#10 +
                   '(' + DataDir + ')', mbConfirmation, MB_YESNO) = IDYES then
    begin
      Log('Shared data deleted at the user''s request: ' + DataDir);
      DelTree(DataDir, True, True, True);
    end;
  end;
end;
