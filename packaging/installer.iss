; Установщик Рэм (Inno Setup 6). Сборка: ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E1F3C52-9B4A-4D7E-A0C1-2F5B8D9E4A71}
AppName=Рэм
AppVersion={#AppVersion}
AppPublisher=KennyS44
AppPublisherURL=https://kennys44.github.io/rem-assistant/
DefaultDirName={localappdata}\Programs\Rem
DefaultGroupName=Рэм
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=RemSetup
SetupIconFile=rem.ico
UninstallDisplayIcon={app}\Rem.exe
UninstallDisplayName=Рэм — голосовой помощник
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "autostart"; Description: "Запускать Рэм вместе с Windows"
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked
Name: "ollama"; Description: "Скачать и установить Ollama — нужна для понимания команд (~1 ГБ)"; Check: not OllamaInstalled

[Files]
Source: "..\dist\Rem\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Рэм"; Filename: "{app}\Rem.exe"
Name: "{userdesktop}\Рэм"; Filename: "{app}\Rem.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Rem"; ValueData: """{app}\Rem.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Rem.exe"; Description: "Запустить Рэм"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/f /im Rem.exe"; Flags: runhidden; RunOnceId: "StopRem"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
         or FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'));
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Скачивание Ollama', 'Это может занять несколько минут.', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WizardIsTaskSelected('ollama') then
  begin
    DownloadPage.Clear;
    DownloadPage.Add('https://ollama.com/download/OllamaSetup.exe', 'OllamaSetup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        MsgBox('Не удалось скачать Ollama: ' + GetExceptionMessage + #13#10 +
               'Рэм установится, а Ollama можно поставить позже с ollama.com.', mbInformation, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Setup: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('ollama') then
  begin
    Setup := ExpandConstant('{tmp}\OllamaSetup.exe');
    if FileExists(Setup) then
    begin
      WizardForm.StatusLabel.Caption := 'Устанавливаю Ollama…';
      Exec(Setup, '/SILENT /NORESTART', '', SW_SHOW, ewWaitUntilTerminated, Code);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and
     DirExists(ExpandConstant('{userappdata}\Rem')) and
     (MsgBox('Удалить настройки и скачанные модели речи Рэм (~300 МБ)?',
             mbConfirmation, MB_YESNO) = IDYES) then
    DelTree(ExpandConstant('{userappdata}\Rem'), True, True, True);
end;
