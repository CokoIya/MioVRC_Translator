; Mio RealTime Translator — Inno Setup Script

#define AppName "Mio RealTime Translator"
#define AppVersion "1.0.0"
#define AppPublisher "酒寄 みお"
#define AppURL "https://78hejiu.top"
#define AppExeName "MioTranslator.exe"
#define SourceDir "dist\MioTranslator"
#define ModelDir "C:\Users\yueya\.cache\modelscope\hub\models\iic\SenseVoiceSmall"

[Setup]
AppId={{A3F2C1B4-9E7D-4F6A-8C3E-1D5B0A2F9C8E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL=https://github.com/CokoIya/MioVRC_Translator/releases
DefaultDirName={autopf}\MioTranslator
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=
OutputDir=dist
OutputBaseFilename=MioTranslator-Setup-v{#AppVersion}
SetupIconFile=
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
; 64ビットのみ
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; UAC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; ウィザード外観
WizardResizable=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[Files]
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; SenseVoice モデルをユーザーのキャッシュに展開
Source: "{#ModelDir}\*"; DestDir: "{%USERPROFILE}\.cache\modelscope\hub\models\iic\SenseVoiceSmall"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
