; Installateur Windows 11 — Lumino (WinUI 3 / .NET 10 self-contained)
#define MyAppName "Lumino"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "yoyo1306"
#define MyAppExeName "Lumino.exe"

[Setup]
AppId={{8F4C2A91-3D7E-4B65-A19C-52E7B38D4102}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Lumino
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Lumino-Setup-{#MyAppVersion}
SetupIconFile=..\lumino.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=yes
UsedUserAreasWarning=no
MinVersion=10.0.19041

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"
Name: "autostart"; Description: "Réappliquer les couleurs et lancer le fond Corsair au démarrage de Windows"; GroupDescription: "Démarrage :"
Name: "addtopath"; Description: "Ajouter Lumino au PATH (commandes --boot, --corsair-stop)"; GroupDescription: "Options avancées :"; Flags: unchecked

[Dirs]
Name: "{commonappdata}\Lumino"; Permissions: users-modify

[Files]
; Fichiers publiés (Lumino.exe + runtime .NET 10 & WinUI 3 embarqués)
Source: "..\publish\*"; DestDir: "{app}"; Excludes: "config.json,lumino.log,*.lock"; Flags: ignoreversion recursesubdirs createallsubdirs
; Préserve config.json éventuel en mise à jour
Source: "..\publish\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist

[InstallDelete]
; Nettoie un éventuel ancien sous-dossier Menu Démarrer ou raccourci legacy
Type: filesandordirs; Name: "{commonprograms}\Lumino"
Type: files; Name: "{userprograms}\Lumino.lnk"
Type: files; Name: "{userstartup}\Lumino.lnk"

[Icons]
; Icône placée directement à la racine du Menu Démarrer, sans sous-dossier et sans raccourci Désinstaller
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\lumino.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\lumino.ico"; Tasks: desktopicon

[Registry]
; Démarrage automatique optionnel dans HKCU
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Lumino"; ValueData: """{app}\{#MyAppExeName}"" --boot --delay 10"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Lancement direct de Lumino à la fin (en utilisateur standard non élevé)
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--corsair-stop"; Flags: runhidden skipifdoesntexist; RunOnceId: "StopLuminoKeepalive"
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillLumino"

[Code]
const
  EnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure AddAppToSystemPath(PathToAdd: String);
var
  CurrentPath: String;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvKey, 'Path', CurrentPath) then
    CurrentPath := '';

  if Pos(';' + UpperCase(PathToAdd) + ';', ';' + UpperCase(CurrentPath) + ';') = 0 then
  begin
    if (Length(CurrentPath) > 0) and (CurrentPath[Length(CurrentPath)] <> ';') then
      CurrentPath := CurrentPath + ';';
    CurrentPath := CurrentPath + PathToAdd;
    RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, EnvKey, 'Path', CurrentPath);
  end;
end;

procedure RemoveAppFromSystemPath(PathToRemove: String);
var
  CurrentPath, UpperCurrent, UpperTarget, NewPath: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvKey, 'Path', CurrentPath) then
    Exit;

  UpperCurrent := ';' + UpperCase(CurrentPath) + ';';
  UpperTarget := ';' + UpperCase(PathToRemove) + ';';
  P := Pos(UpperTarget, UpperCurrent);
  if P > 0 then
  begin
    NewPath := CurrentPath;
    if P = 1 then
      Delete(NewPath, 1, Length(PathToRemove) + 1)
    else
      Delete(NewPath, P - 1, Length(PathToRemove) + 1);
    RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, EnvKey, 'Path', NewPath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addtopath') then
      AddAppToSystemPath(ExpandConstant('{app}'))
    else
      RemoveAppFromSystemPath(ExpandConstant('{app}'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveAppFromSystemPath(ExpandConstant('{app}'));
end;
