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
; обновление из самого Рэм: он выходит сам, установщик запускает новую версию ([Run] ниже)
RestartApplications=no

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
; тихое обновление (/UPDATE=1 из update.py) — запустить Рэм снова
Filename: "{app}\Rem.exe"; Flags: nowait; Check: IsUpdate

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/f /im Rem.exe"; Flags: runhidden; RunOnceId: "StopRem"

[UninstallDelete]
; файлы, появившиеся после установки (кэш comtypes и т. п.), — папка программы целиком
Type: filesandordirs; Name: "{app}"

[Code]
const
  DefaultModel = 'qwen3:4b-instruct-2507-q4_K_M';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RemKey = 'Software\Rem';

var
  DownloadPage: TDownloadWizardPage;
  RemoveData, RemoveModel, RemoveOllama: Boolean;

function OllamaExe: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe');
end;

function OllamaInstalled: Boolean;
begin
  Result := FileExists(OllamaExe) or FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'));
end;

{ ——— установка ——— }

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
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
        if not WizardSilent then
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
      Exec(Setup, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_SHOW, ewWaitUntilTerminated, Code);
      { запоминаем: Ollama поставили мы — при удалении предложим убрать и её }
      if FileExists(OllamaExe) then
        RegWriteStringValue(HKCU, RemKey, 'InstalledOllama', '1');
    end;
  end;
end;

{ ——— удаление ——— }

function OllamaInstalledByRem: Boolean;
var
  S: String;
begin
  Result := RegQueryStringValue(HKCU, RemKey, 'InstalledOllama', S) and (S = '1');
end;

function AskWhatToRemove: Boolean;
var
  Form: TSetupForm;
  Title, Note: TNewStaticText;
  Data, Model, Oll: TNewCheckBox;
  OkButton, CancelButton: TNewButton;
  Y: Integer;
begin
  Form := CreateCustomForm(ScaleX(460), ScaleY(250), False, False);
  try
    Form.Caption := 'Удаление Рэм';
    Y := ScaleY(16);
    Title := TNewStaticText.Create(Form);
    Title.Parent := Form;
    Title.Left := ScaleX(16);
    Title.Top := Y;
    Title.Caption := 'Что удалить вместе с программой:';
    Title.Font.Style := [fsBold];
    Y := Y + ScaleY(28);

    Data := TNewCheckBox.Create(Form);
    Data.Parent := Form;
    Data.Left := ScaleX(16);
    Data.Top := Y;
    Data.Width := Form.ClientWidth - ScaleX(32);
    Data.Caption := 'Настройки, журнал, модели речи и нейроголос (до ~700 МБ)';
    Data.Checked := True;
    Y := Y + ScaleY(26);

    Model := TNewCheckBox.Create(Form);
    Model.Parent := Form;
    Model.Left := ScaleX(16);
    Model.Top := Y;
    Model.Width := Form.ClientWidth - ScaleX(32);
    Model.Caption := 'Языковую модель Рэм из Ollama (~2,5 ГБ)';
    Model.Checked := OllamaInstalled;
    Model.Enabled := OllamaInstalled;
    Y := Y + ScaleY(26);

    Oll := TNewCheckBox.Create(Form);
    Oll.Parent := Form;
    Oll.Left := ScaleX(16);
    Oll.Top := Y;
    Oll.Width := Form.ClientWidth - ScaleX(32);
    Oll.Caption := 'Программу Ollama и все её модели';
    Oll.Checked := OllamaInstalled and OllamaInstalledByRem;
    Oll.Enabled := OllamaInstalled;
    Y := Y + ScaleY(22);

    Note := TNewStaticText.Create(Form);
    Note.Parent := Form;
    Note.Left := ScaleX(36);
    Note.Top := Y;
    Note.Width := Form.ClientWidth - ScaleX(52);
    Note.WordWrap := True;
    Note.Caption := 'Сними галочку, если пользуешься Ollama ещё для чего-то.';
    Note.Font.Color := clGrayText;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := 'Отмена';
    CancelButton.ModalResult := mrCancel;
    CancelButton.Width := ScaleX(88);
    CancelButton.Height := ScaleY(26);
    CancelButton.Left := Form.ClientWidth - ScaleX(16) - CancelButton.Width;
    CancelButton.Top := Form.ClientHeight - ScaleY(16) - CancelButton.Height;

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := 'Удалить';
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;
    OkButton.Width := ScaleX(88);
    OkButton.Height := ScaleY(26);
    OkButton.Left := CancelButton.Left - ScaleX(8) - OkButton.Width;
    OkButton.Top := CancelButton.Top;

    Result := Form.ShowModal = mrOk;
    RemoveData := Data.Checked;
    RemoveModel := Model.Checked;
    RemoveOllama := Oll.Checked;
  finally
    Form.Free;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  { по умолчанию (и в тихом режиме): всё своё; Ollama — только если ставили её мы }
  RemoveData := True;
  RemoveModel := OllamaInstalled;
  RemoveOllama := OllamaInstalled and OllamaInstalledByRem;
  if ExpandConstant('{param:KEEPOLLAMA|0}') = '1' then RemoveOllama := False;
  if ExpandConstant('{param:REMOVEOLLAMA|0}') = '1' then RemoveOllama := OllamaInstalled;
  if not UninstallSilent then
    Result := AskWhatToRemove;
end;

function ConfiguredModel: String;
{ модель из %APPDATA%\Rem\config.json, если её меняли в настройках }
var
  S: AnsiString;
  T: String;
  P: Integer;
begin
  Result := DefaultModel;
  if not LoadStringFromFile(ExpandConstant('{userappdata}\Rem\config.json'), S) then
    Exit;
  T := String(S);
  P := Pos('"model"', T);
  if P = 0 then Exit;
  T := Copy(T, P + 7, Length(T));
  P := Pos('"', T);
  if P = 0 then Exit;
  T := Copy(T, P + 1, Length(T));
  P := Pos('"', T);
  if P > 1 then
    Result := Copy(T, 1, P - 1);
end;

procedure KillOllama;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im "ollama app.exe"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im ollama.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure RemoveOllamaModel(const Model: String);
var
  Code: Integer;
begin
  if not FileExists(OllamaExe) then Exit;
  if Exec(OllamaExe, 'rm ' + Model, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then Exit;
  { сервер Ollama не запущен — поднимаем на время удаления модели }
  Exec(OllamaExe, 'serve', '', SW_HIDE, ewNoWait, Code);
  Sleep(5000);
  Exec(OllamaExe, 'rm ' + Model, '', SW_HIDE, ewWaitUntilTerminated, Code);
  { раз сервер не работал, единственный ollama.exe — наш; останавливаем его }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im ollama.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure UninstallOllama;
var
  Code, I: Integer;
  Uninst: String;
begin
  KillOllama;
  Uninst := ExpandConstant('{localappdata}\Programs\Ollama\unins000.exe');
  if FileExists(Uninst) then
  begin
    Exec(Uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code);
    { деинсталлятор Inno перезапускает себя из временной папки — ждём, пока файлы уйдут }
    for I := 1 to 90 do
    begin
      if not FileExists(OllamaExe) then Break;
      Sleep(1000);
    end;
  end;
  DelTree(ExpandConstant('{%USERPROFILE}\.ollama'), True, True, True);   { модели Ollama }
  DelTree(ExpandConstant('{localappdata}\Ollama'), True, True, True);    { её журналы }
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Model: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Model := ConfiguredModel;            { читаем до удаления настроек }
    if RemoveOllama then
      UninstallOllama
    else if RemoveModel then
      RemoveOllamaModel(Model);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    { автозапуск мог быть включён из настроек Рэм, а не при установке }
    RegDeleteValue(HKCU, RunKey, 'Rem');
    RegDeleteKeyIncludingSubkeys(HKCU, RemKey);
    if RemoveData then
      DelTree(ExpandConstant('{userappdata}\Rem'), True, True, True);
    DelTree(ExpandConstant('{app}'), True, True, True);
    DelTree(AddBackslash(GetTempDir) + 'comtypes_cache\\Rem-311', True, True, True);   { только наш кэш }
  end;
end;
