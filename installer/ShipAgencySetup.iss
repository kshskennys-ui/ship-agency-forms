#define MyAppName "船代业务系统"
#define MyAppVersion "1.0.1"

[Setup]
AppId={{A0F5C4C4-90B0-47E4-9B26-9E5B3DB2E2A7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Ship Agency
Password={#MyPassword}
Encryption=yes
DefaultDirName={localappdata}\ShipAgencyForms
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
CloseApplications=yes
CloseApplicationsFilter=ShipAgencyServer.exe
RestartApplications=no
OutputDir=release
OutputBaseFilename=ShipAgencySetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
Uninstallable=yes

[Files]
Source: "staging\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{autodesktop}\船代业务系统"; Filename: "{app}\ShipAgencyLauncher.exe"; WorkingDir: "{app}"
Name: "{group}\船代业务系统"; Filename: "{app}\ShipAgencyLauncher.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\ShipAgencyLauncher.exe"; Description: "启动船代业务系统"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\templates"
Type: filesandordirs; Name: "{app}\node_modules"
Type: files; Name: "{app}\ShipAgencyLauncher.exe"
Type: files; Name: "{app}\ShipAgencyServer.exe"
