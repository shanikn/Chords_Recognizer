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
// On uninstall, offer to delete downloaded songs, cached analyses, updates and logs
// (%LOCALAPPDATA%\ChordChart). A silent uninstall keeps them. The other variant, if
// installed, uses the same folder, so it would have to download again.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ChordChart');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete the songs ChordChart downloaded and its saved results?' + #13#10 +
                '(' + DataDir + ')', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
