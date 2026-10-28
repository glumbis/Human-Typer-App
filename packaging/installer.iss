; Inno Setup 6 script. Build with: ISCC.exe packaging\installer.iss   (run build step for dist\HumanTyper first)
; Version can be overridden: ISCC /DAppVersion=1.3.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "1.3.0"
#endif
#define AppName "Human Typer"
#define AppExe "HumanTyper.exe"

[Setup]
AppId={{7B3E5C1A-4D2F-4F6B-9A8E-2C1D5E7F9B34}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Human Typer
AppPublisherURL=https://github.com/glumbis/Human-Typer-App
AppSupportURL=https://github.com/glumbis/Human-Typer-App/issues
AppUpdatesURL=https://github.com/glumbis/Human-Typer-App/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Human Typer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=HumanTyper-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\HumanTyper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
