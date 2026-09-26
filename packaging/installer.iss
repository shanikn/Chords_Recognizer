; Inno Setup script for ChordChart. Built by: uv run python packaging/build.py --installer
; Per-user install (no admin prompt): %LOCALAPPDATA%\Programs\ChordChart.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8E4B6C2A-6F2D-4C1E-9B7A-3C5D2E1F0A9B}
AppName=ChordChart
AppVersion={#AppVersion}
AppVerName=ChordChart {#AppVersion}
AppPublisher=ChordChart
DefaultDirName={autopf}\ChordChart
DefaultGroupName=ChordChart
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=out
OutputBaseFilename=ChordChart-Setup-{#AppVersion}
SetupIconFile=chordchart.ico
UninstallDisplayIcon={app}\ChordChart.exe
UninstallDisplayName=ChordChart
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a ChordChart icon on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\ChordChart\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The license notices, also at the top level so they're easy to find.
Source: "build\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ChordChart"; Filename: "{app}\ChordChart.exe"
Name: "{group}\Third-party licenses"; Filename: "{app}\licenses\THIRD-PARTY-NOTICES.txt"
Name: "{group}\Uninstall ChordChart"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ChordChart"; Filename: "{app}\ChordChart.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ChordChart.exe"; Description: "Open ChordChart now"; Flags: nowait postinstall skipifsilent

[Code]
// On uninstall, offer to delete downloaded songs, cached analyses, updates and logs
// (%LOCALAPPDATA%\ChordChart). A silent uninstall keeps them.
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
