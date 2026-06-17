; Mio RealTime Translator の Inno Setup スクリプト

#define AppName "Mio RealTime Translator"
#define AppVersion "v1.3.7.6"
#define AppNumericVersion "1.3.7.6"
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
DefaultDirName={code:GetDefaultDirName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=LICENSE
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
ShowLanguageDialog=yes
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
Name: "zhcn"; MessagesFile: "compiler:Default.isl,installer\i18n\ChineseSimplified.isl"
Name: "japanese"; MessagesFile: "compiler:Default.isl,installer\i18n\Japanese.isl"
Name: "russian"; MessagesFile: "compiler:Default.isl,installer\i18n\Russian.isl"
Name: "korean"; MessagesFile: "compiler:Default.isl,installer\i18n\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"

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
Source: "{#SourceDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceDir}\NOTICE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceDir}\BRANDING.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#SourceDir}\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
AdditionalTasks=Additional tasks:

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  ExistingInstallDir: String;

function IsPathRooted(Path: String): Boolean;
begin
  Result := (Length(Path) >= 3) and (Path[2] = ':') and (Path[3] = '\');
end;

function NormalizeDir(Path: String): String;
begin
  Result := Trim(Path);
  if (Result <> '') and (Result[Length(Result)] = '\') then
    Delete(Result, Length(Result), 1);
end;

function DirFromUninstallString(Value: String): String;
var
  ExePath: String;
  QuotePos: Integer;
begin
  Result := '';
  Value := Trim(Value);
  if Value = '' then
    Exit;

  if Value[1] = '"' then
  begin
    QuotePos := Pos('"', Copy(Value, 2, Length(Value) - 1));
    if QuotePos > 0 then
      ExePath := Copy(Value, 2, QuotePos - 1);
  end
  else
  begin
    QuotePos := Pos(' ', Value);
    if QuotePos > 0 then
      ExePath := Copy(Value, 1, QuotePos - 1)
    else
      ExePath := Value;
  end;

  if ExePath <> '' then
    Result := NormalizeDir(ExtractFileDir(ExePath));
end;

function TryReadExistingInstallDir(RootKey: Integer; SubKey: String; var InstallDir: String): Boolean;
var
  Value: String;
begin
  Result := False;
  InstallDir := '';

  if RegQueryStringValue(RootKey, SubKey, 'InstallLocation', Value) then
  begin
    Value := NormalizeDir(Value);
    if (Value <> '') and DirExists(Value) then
    begin
      InstallDir := Value;
      Result := True;
      Exit;
    end;
  end;

  if RegQueryStringValue(RootKey, SubKey, 'UninstallString', Value) then
  begin
    Value := DirFromUninstallString(Value);
    if (Value <> '') and DirExists(Value) then
    begin
      InstallDir := Value;
      Result := True;
      Exit;
    end;
  end;
end;

function FindExistingInstallDir(): String;
var
  SubKey: String;
begin
  Result := '';
  SubKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3F2C1B4-9E7D-4F6A-8C3E-1D5B0A2F9C8E}_is1';

  if TryReadExistingInstallDir(HKCU, SubKey, Result) then
    Exit;
  if TryReadExistingInstallDir(HKLM, SubKey, Result) then
    Exit;
  if TryReadExistingInstallDir(HKLM64, SubKey, Result) then
    Exit;
  if TryReadExistingInstallDir(HKLM32, SubKey, Result) then
    Exit;
end;

function FreshInstallDefaultDir(): String;
var
  DriveCode: Integer;
  DriveRoot: String;
begin
  for DriveCode := Ord('D') to Ord('Z') do
  begin
    DriveRoot := Chr(DriveCode) + ':\';
    if DirExists(DriveRoot) then
    begin
      Result := DriveRoot + '{#AppName}';
      Exit;
    end;
  end;
  Result := 'C:\{#AppName}';
end;

function GetDefaultDirName(Param: String): String;
begin
  if ExistingInstallDir <> '' then
    Result := ExistingInstallDir
  else
    Result := FreshInstallDefaultDir();
end;

function InitializeSetup(): Boolean;
begin
  ExistingInstallDir := NormalizeDir(FindExistingInstallDir());
  Result := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectDir) and (ExistingInstallDir <> '');
end;
