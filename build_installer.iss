#define MyAppName "Voxaho"
#define MyAppVersion "1.0.5"
#define MyAppPublisher "Serge AHOUANSINOU"
#define MyAppURL "https://voxaho.com"
#define MyAppExeName "Voxaho.exe"

[Setup]
AppId={{4D95E701-4E41-427A-884B-87F1F8B7F3D0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
SetupIconFile=assets\AppIcon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; Mode user-level par défaut, choix possible via dialogue (UAC seulement si admin choisi)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Options :"
Name: "startup"; Description: "Lancer Voxaho au démarrage de Windows"; GroupDescription: "Options :"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; {auto*} = {user*} en mode user-level, {common*} en mode admin — pas de 0x80070005
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
