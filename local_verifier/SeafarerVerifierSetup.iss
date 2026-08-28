#define MyAppName "船代海员证本地核验工具"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{B1A5D4E1-37E3-4F5A-9EE7-73CCBFE4B9D1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Ship Agency
Password={#MyPassword}
Encryption=yes
DefaultDirName={localappdata}\ShipAgencySeafarerAgent
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
CloseApplications=yes
CloseApplicationsFilter=ShipAgencySeafarerAgent.exe
OutputDir=release
OutputBaseFilename=ShipAgencySeafarerAgentSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes

[Files]
Source: "staging\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\海员证本地核验工具"; Filename: "{app}\ShipAgencySeafarerAgent.exe"; WorkingDir: "{app}"
Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\Startup\海员证本地核验工具"; Filename: "{app}\ShipAgencySeafarerAgent.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\ShipAgencySeafarerAgent.exe"; Description: "启动海员证本地核验工具"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: files; Name: "{app}\ShipAgencySeafarerAgent.exe"
