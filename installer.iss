; Inno Setup script for FSE Processor
; Requires Inno Setup 6.x

#define MyAppName "FSE Processor"
#define MyAppVersion "2.3.8"
#define MyAppPublisher "FSE Processor"
#define MyAppExeName "FSE Processor.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=FSE_Processor_Setup_{#MyAppVersion}_QT6
SetupIconFile=assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=yes
CloseApplications=force
CloseApplicationsFilter=*.exe

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main application files from PyInstaller dist
Source: "dist\FSE Processor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Installed mode marker
Source: "assets\.installed"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Disinstalla {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Avvia {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  PYTHON_URL = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe';

var
  NeedsPythonInstall: Boolean;

{ ---------- Python detection ---------- }

function IsPythonInstalled: Boolean;
var
  InstallPath: String;
  Versions: array of String;
  I: Integer;
begin
  Result := False;
  SetArrayLength(Versions, 4);
  Versions[0] := '3.13';
  Versions[1] := '3.12';
  Versions[2] := '3.11';
  Versions[3] := '3.10';

  for I := 0 to GetArrayLength(Versions) - 1 do
  begin
    if RegQueryStringValue(HKLM, 'SOFTWARE\Python\PythonCore\' + Versions[I] + '\InstallPath', '', InstallPath) then
    begin
      Log('[Python] Trovato Python ' + Versions[I] + ' in HKLM: ' + InstallPath);
      Result := True;
      Exit;
    end;
    if RegQueryStringValue(HKCU, 'SOFTWARE\Python\PythonCore\' + Versions[I] + '\InstallPath', '', InstallPath) then
    begin
      Log('[Python] Trovato Python ' + Versions[I] + ' in HKCU: ' + InstallPath);
      Result := True;
      Exit;
    end;
  end;

  Log('[Python] Nessuna versione di Python (3.10-3.13) trovata nel registro');
end;

{ ---------- Python download ---------- }

function DownloadPythonInstaller(const DestPath: String): Boolean;
var
  ResultCode: Integer;
  CmdLine: String;
begin
  Result := False;

  { Attempt 1 - PowerShell }
  Log('[Python] Tentativo download con PowerShell...');
  CmdLine := '-NoProfile -ExecutionPolicy Bypass -Command "& { $ProgressPreference = ''SilentlyContinue''; Invoke-WebRequest -Uri ''' + PYTHON_URL + ''' -OutFile ''' + DestPath + ''' }"';
  if Exec('powershell.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    Log('[Python] Download completato con PowerShell');
    Result := True;
    Exit;
  end;
  Log('[Python] PowerShell fallito (codice: ' + IntToStr(ResultCode) + ')');

  { Attempt 2 - certutil }
  Log('[Python] Tentativo download con certutil...');
  CmdLine := '-urlcache -split -f "' + PYTHON_URL + '" "' + DestPath + '"';
  if Exec('certutil.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    Log('[Python] Download completato con certutil');
    Result := True;
    Exit;
  end;
  Log('[Python] Anche certutil fallito (codice: ' + IntToStr(ResultCode) + ')');
end;

{ ---------- Setup events ---------- }

function InitializeSetup: Boolean;
begin
  Result := True;
  NeedsPythonInstall := False;

  Log('[Setup] Verifica presenza di Python...');

  if IsPythonInstalled then
  begin
    Log('[Setup] Python presente, proseguo normalmente');
    Exit;
  end;

  { Python not found - ask user }
  case MsgBox(
    'Python non risulta installato nel sistema.' + #13#10 +
    'Python e'' necessario per il funzionamento di FSE Processor.' + #13#10#13#10 +
    'Vuoi installare Python automaticamente?' + #13#10 +
    '(Verra'' scaricato ed installato Python 3.12 da python.org)',
    mbConfirmation, MB_YESNO) of
    IDYES:
      begin
        Log('[Setup] Utente ha accettato l''installazione automatica di Python');
        NeedsPythonInstall := True;
      end;
    IDNO:
      begin
        Log('[Setup] Utente ha rifiutato l''installazione di Python');
        MsgBox(
          'Installazione di FSE Processor interrotta.' + #13#10#13#10 +
          'Per utilizzare FSE Processor e'' necessario Python 3.10 o superiore.' + #13#10#13#10 +
          'Puoi installarlo manualmente:' + #13#10 +
          '  1. Scarica Python da https://www.python.org/downloads/' + #13#10 +
          '  2. Seleziona "Add Python to PATH" durante l''installazione' + #13#10 +
          '  3. Riesegui l''installer di FSE Processor',
          mbInformation, MB_OK);
        Result := False;
      end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProgressPage: TOutputProgressWizardPage;
  TempFile: String;
  ResultCode: Integer;
begin
  Result := '';

  if not NeedsPythonInstall then
    Exit;

  ProgressPage := CreateOutputProgressPage(
    'Installazione Python',
    'Installazione dei prerequisiti necessari per FSE Processor...');

  try
    ProgressPage.Show;

    { Step 1 - Download }
    ProgressPage.SetText(
      'Download di Python 3.12 da python.org...',
      'URL: ' + PYTHON_URL);
    ProgressPage.SetProgress(1, 5);
    Log('[Python] Avvio download da: ' + PYTHON_URL);

    TempFile := ExpandConstant('{tmp}\python-3.12.8-amd64.exe');

    if not DownloadPythonInstaller(TempFile) then
    begin
      Result :=
        'Download di Python fallito.' + #13#10 +
        'Verifica la connessione a Internet e riprova,' + #13#10 +
        'oppure installa Python manualmente da https://www.python.org/downloads/';
      Log('[Python] Download fallito, installazione interrotta');
      Exit;
    end;

    { Step 2 - Silent install }
    ProgressPage.SetText(
      'Installazione silenziosa di Python in corso...',
      'Opzioni: PrependPath=1, Include_pip=1 — potrebbe richiedere qualche minuto');
    ProgressPage.SetProgress(2, 5);
    Log('[Python] Avvio installazione silenziosa...');

    if Exec(TempFile,
            '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1',
            '', SW_SHOW, ewWaitUntilTerminated, ResultCode)
       and (ResultCode = 0) then
    begin
      Log('[Python] Installazione silenziosa completata (codice: 0)');
    end
    else
    begin
      Log('[Python] Installazione silenziosa fallita (codice: ' + IntToStr(ResultCode) + '), avvio guidata...');

      { Step 2b - Guided fallback }
      ProgressPage.SetText(
        'Installazione silenziosa fallita — avvio installazione guidata...',
        'Seguire le istruzioni a schermo. Selezionare "Add Python to PATH".');
      ProgressPage.SetProgress(2, 5);

      if not Exec(TempFile,
                  'PrependPath=1 Include_launcher=1 Include_pip=1',
                  '', SW_SHOW, ewWaitUntilTerminated, ResultCode)
         or (ResultCode <> 0) then
      begin
        Result :=
          'Installazione di Python fallita.' + #13#10 +
          'Installa Python manualmente da https://www.python.org/downloads/' + #13#10 +
          'poi riesegui l''installer di FSE Processor.';
        Log('[Python] Anche installazione guidata fallita (codice: ' + IntToStr(ResultCode) + ')');
        Exit;
      end;
      Log('[Python] Installazione guidata completata');
    end;

    { Step 3 - Verify }
    ProgressPage.SetText('Verifica installazione Python...', '');
    ProgressPage.SetProgress(4, 5);
    Log('[Python] Verifica post-installazione...');

    if IsPythonInstalled then
      Log('[Python] Verifica OK — Python presente nel registro')
    else
      Log('[Python] ATTENZIONE: Python non trovato nel registro dopo installazione (potrebbe essere normale)');

    { Done }
    ProgressPage.SetText('Installazione Python completata.', '');
    ProgressPage.SetProgress(5, 5);
    Log('[Python] Prerequisito Python soddisfatto');

    DeleteFile(TempFile);

  finally
    ProgressPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Create AppData directory for user settings }
    AppDataDir := ExpandConstant('{userappdata}\FSE Processor');
    if not DirExists(AppDataDir) then
      ForceDirectories(AppDataDir);
    Log('[Setup] Cartella AppData creata: ' + AppDataDir);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    AppDataDir := ExpandConstant('{userappdata}\FSE Processor');
    if DirExists(AppDataDir) then
    begin
      if MsgBox('Vuoi eliminare anche le impostazioni e i dati in AppData?' + #13#10 +
                 '(' + AppDataDir + ')',
                 mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(AppDataDir, True, True, True);
      end;
    end;
  end;
end;
