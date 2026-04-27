; Mio RealTime Translator の Inno Setup スクリプト

#define AppName "Mio RealTime Translator"
#define AppVersion "v1.2.7"
#define AppNumericVersion "1.2.7.0"
#define AppPublisher "みお_Mio"
#define AppURL "https://github.com/CokoIya/MioVRC_Translator"
#define AppExeName "MioTranslator.exe"
#define SourceDir "dist\MioTranslator"
#define ModelRuntimeSlug "iic--SenseVoiceSmall"
#define ModelBundledName "sensevoice-small"
#define ModelArchiveName "MioTranslator-Model-SenseVoiceSmall-" + AppVersion + ".zip"
#define ModelArchiveFallbackName "MioTranslator-Model-SenseVoiceSmall.zip"

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
#ifdef BundleModels
OutputBaseFilename=MioTranslator-Setup-{#AppVersion}-full
#else
OutputBaseFilename=MioTranslator-Setup-{#AppVersion}-lite
#endif
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

[Code]
function CompleteModelDir(Path: string): Boolean;
begin
  Result :=
    DirExists(Path) and
    FileExists(Path + '\model.pt') and
    (FileExists(Path + '\configuration.json') or FileExists(Path + '\config.yaml'));
end;

function RuntimeModelsRoot(): string;
begin
  Result := ExpandConstant('{localappdata}\{#AppName}\runtime_models');
end;

function RuntimeModelDir(Name: string): string;
begin
  Result := RuntimeModelsRoot() + '\' + Name;
end;

function BundledModelDir(Name: string): string;
begin
  Result := ExpandConstant('{app}\models\' + Name);
end;

function InternalModelDir(Name: string): string;
begin
  Result := ExpandConstant('{app}\_internal\models\' + Name);
end;

function InstalledModelExists(): Boolean;
begin
  Result :=
    CompleteModelDir(RuntimeModelDir('{#ModelRuntimeSlug}')) or
    CompleteModelDir(RuntimeModelDir('{#ModelBundledName}')) or
    CompleteModelDir(BundledModelDir('{#ModelRuntimeSlug}')) or
    CompleteModelDir(BundledModelDir('{#ModelBundledName}')) or
    CompleteModelDir(InternalModelDir('{#ModelRuntimeSlug}')) or
    CompleteModelDir(InternalModelDir('{#ModelBundledName}'));
end;

function FindSidecarModelArchive(): string;
var
  Candidate: string;
begin
  Candidate := ExpandConstant('{src}\{#ModelArchiveName}');
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;

  Candidate := ExpandConstant('{src}\{#ModelArchiveFallbackName}');
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;

  Candidate := ExpandConstant('{src}\sensevoice-small.zip');
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    Exit;
  end;

  Result := '';
end;

function ShouldDeleteSidecarArchive(ArchivePath: string): Boolean;
begin
  Result :=
    (CompareText(ExtractFileName(ArchivePath), '{#ModelArchiveName}') = 0) or
    (CompareText(ExtractFileName(ArchivePath), '{#ModelArchiveFallbackName}') = 0);
end;

procedure DeleteSidecarArchive(ArchivePath: string);
begin
  if (ArchivePath = '') or (not ShouldDeleteSidecarArchive(ArchivePath)) then
  begin
    Exit;
  end;

  DeleteFile(ArchivePath);
  DeleteFile(ArchivePath + '.sha256');
end;

function PowerShellQuote(Value: string): string;
var
  Escaped: string;
begin
  Escaped := Value;
  StringChangeEx(Escaped, '''', '''''', True);
  Result := '''' + Escaped + '''';
end;

procedure InstallSidecarModelIfNeeded();
var
  ArchivePath: string;
  Command: string;
  Params: string;
  ResultCode: Integer;
begin
  ArchivePath := FindSidecarModelArchive();

  if InstalledModelExists() then
  begin
    DeleteSidecarArchive(ArchivePath);
    Exit;
  end;

  if ArchivePath = '' then
  begin
    if not WizardSilent then
    begin
      MsgBox(
        'The SenseVoice speech model was not found on this PC. ' +
        'MioTranslator can download the model automatically when you start listening. ' +
        'For the simplest fresh install, download the full installer from https://78hejiu.top.',
        mbInformation,
        MB_OK
      );
    end;
    Exit;
  end;

  ForceDirectories(RuntimeModelsRoot());
  WizardForm.StatusLabel.Caption := 'Installing SenseVoice speech model...';

  Command :=
    '$ErrorActionPreference = ''Stop''; ' +
    '$archive = ' + PowerShellQuote(ArchivePath) + '; ' +
    '$dest = ' + PowerShellQuote(RuntimeModelsRoot()) + '; ' +
    '$model = Join-Path $dest ' + PowerShellQuote('{#ModelRuntimeSlug}') + '; ' +
    '$legacy = Join-Path $dest ' + PowerShellQuote('{#ModelBundledName}') + '; ' +
    '$hashFile = $archive + ''.sha256''; ' +
    'if (Test-Path -LiteralPath $hashFile) { ' +
    '$expected = ((Get-Content -LiteralPath $hashFile -Raw).Trim() -split ''\s+'')[0].ToLowerInvariant(); ' +
    '$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant(); ' +
    'if ($expected -and ($actual -ne $expected)) { throw ''SenseVoice model archive checksum mismatch'' } }; ' +
    'New-Item -ItemType Directory -Force -Path $dest | Out-Null; ' +
    'Expand-Archive -LiteralPath $archive -DestinationPath $dest -Force; ' +
    'if (!(Test-Path (Join-Path $model ''model.pt'')) -and (Test-Path (Join-Path $legacy ''model.pt''))) { ' +
    'if (Test-Path $model) { Remove-Item -LiteralPath $model -Recurse -Force }; ' +
    'Rename-Item -LiteralPath $legacy -NewName ' + PowerShellQuote('{#ModelRuntimeSlug}') + ' }; ' +
    'if (!(Test-Path (Join-Path $model ''model.pt''))) { throw ''SenseVoice model archive is missing model.pt'' }; ' +
    'if (!(Test-Path (Join-Path $model ''configuration.json'')) -and !(Test-Path (Join-Path $model ''config.yaml''))) { throw ''SenseVoice model archive is missing configuration.json or config.yaml'' }';

  Params := '-NoProfile -ExecutionPolicy Bypass -Command ' + AddQuotes(Command);
  if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
  begin
    MsgBox(
      'A SenseVoice model archive was found next to the installer, but it could not be installed. ' +
      'MioTranslator can still download the model automatically when it starts.',
      mbInformation,
      MB_OK
    );
    Exit;
  end;

  if InstalledModelExists() then
  begin
    DeleteSidecarArchive(ArchivePath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InstallSidecarModelIfNeeded();
  end;
end;
