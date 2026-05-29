; Mio RealTime Translator の Inno Setup スクリプト

#define AppName "Mio RealTime Translator"
#define AppVersion "v1.3.6.2"
#define AppNumericVersion "1.3.6.2"
#define AppPublisher "みお_Mio"
#define AppURL "https://github.com/CokoIya/MioVRC_Translator"
#define AppExeName "MioTranslator.exe"
#define SourceDir "dist\MioTranslator"

[Setup]
AppId={{A3F2C1B4-9E7D-4F6A-8C3E-1D5B0A2F9C8E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=
OutputDir=dist
OutputBaseFilename=MioTranslator-Setup-{#AppVersion}
SetupIconFile=assets\icons\app_icon_mio.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
; 64 ビット版のみを対象にする
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; UAC 設定
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
AppMutex=MioTranslatorRuntimeMutex
; ウィザードの外観設定
WizardResizable=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
ShowLanguageDialog=no
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=Copyright (C) 2026 {#AppPublisher}
VersionInfoDescription={#AppName} Installer
VersionInfoProductName={#AppName}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoVersion={#AppNumericVersion}
#ifdef SignPfx
#if SignPfx != ""
SignTool=signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f "{#SignPfx}" /p "{#SignPass}" $f
SignedUninstaller=yes
#endif
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[InstallDelete]
; Replace the packaged runtime completely. Overlay installs can otherwise keep
; stale binary extensions from older builds, such as scipy, NumPy 2.x, or cp314
; .pyd files, which can make NumPy fail with duplicate-load errors.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\models"

[Files]
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
