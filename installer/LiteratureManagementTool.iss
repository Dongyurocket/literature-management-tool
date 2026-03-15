#define MyAppName "Literature management tool"
#define MyAppPublisher "Dongyurocket"
#define MyAppURL "https://github.com/Dongyurocket/literature-management-tool"
#define MyAppExeName "Literature management tool.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.5.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\\dist\\Literature management tool"
#endif
#ifndef OutputDir
  #define OutputDir "..\\dist"
#endif

[Setup]
AppId={{6B7D62A5-D9A8-4486-8E2F-8A157C3062C4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
WizardStyle=modern
Compression=lzma
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=Literature-management-tool-v{#MyAppVersion}-Setup
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupLogging=yes
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\README"; Filename: "{app}\README.md"
Name: "{group}\LICENSE"; Filename: "{app}\LICENSE"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
