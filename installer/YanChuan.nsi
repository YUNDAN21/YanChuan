Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
SetDatablockOptimize on

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!ifndef PAYLOAD_INCLUDE
  !error "Use scripts/package_installer.py to generate the audited payload include."
!endif
!include "${PAYLOAD_INCLUDE}"

Name "言川"
OutFile "${SETUP_OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\YanChuan"
; Stable private registry identity lets the renamed product update an existing installation.
InstallDirRegKey HKCU "Software\LiveCaptions" "InstallDir"
BrandingText "言川 · YanChuan"
VIProductVersion "${APP_VERSION_QUAD}"
VIAddVersionKey /LANG=2052 "ProductName" "言川"
VIAddVersionKey /LANG=2052 "FileDescription" "言川安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "YanChuan contributors"

!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\YanChuan.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_RUN_TEXT "启动言川"
!define MUI_FINISHPAGE_TEXT "安装完成。模型可在应用的“模型管理”中下载。配置、会议和默认模型保存在用户目录，卸载程序会保留这些内容。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\LiveCaptions"

; Upgrade/uninstall must not replace a running executable or terminate recordings.
!macro CheckFileUnlocked PATH
  ${If} ${FileExists} "${PATH}"
    System::Call 'kernel32::CreateFileW(w "${PATH}", i 0x40000000, i 0, p 0, i 3, i 0, p 0) p.r0'
    ${If} $0 == -1
      MessageBox MB_OK|MB_ICONEXCLAMATION "请先退出当前安装目录中的言川，再继续安装或卸载。" /SD IDOK
      SetErrorLevel 2
      Abort
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
!macroend

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${IsNativeAMD64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 x64 Windows。" /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastBuild} 17763
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 Windows 10 1809 或更新版本。" /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Section "言川（必选）" MainSection
  SectionIn RO
  SetShellVarContext current
  SetRegView 64
  !insertmacro CheckFileUnlocked "$INSTDIR\YanChuan.exe"
  !insertmacro CheckFileUnlocked "$INSTDIR\LiveCaptions.exe"
  !insertmacro CheckFileUnlocked "$INSTDIR\LiveCaptions.dll"
  !insertmacro CheckFileUnlocked "$INSTDIR\backend\python\python.exe"
  !insertmacro CheckFileUnlocked "$INSTDIR\backend\native\crispasr.dll"
  !insertmacro InstallPayload
  ; Retire only known old application entry files after the new payload is installed.
  Delete "$INSTDIR\LiveCaptions.exe"
  Delete "$INSTDIR\LiveCaptions.dll"
  Delete "$INSTDIR\LiveCaptions.deps.json"
  Delete "$INSTDIR\LiveCaptions.runtimeconfig.json"
  Delete "$INSTDIR\LiveCaptions.pri"
  Delete "$INSTDIR\LiveCaptions.pdb"
  Delete "$INSTDIR\livecaptions.runtime.json"
  Delete "$SMPROGRAMS\LiveCaptions\LiveCaptions.lnk"
  Delete "$SMPROGRAMS\LiveCaptions\卸载 LiveCaptions.lnk"
  RMDir "$SMPROGRAMS\LiveCaptions"
  ${If} ${FileExists} "$DESKTOP\LiveCaptions.lnk"
    CreateShortcut "$DESKTOP\言川.lnk" "$INSTDIR\YanChuan.exe" "" "$INSTDIR\Assets\AppIcon.ico"
    Delete "$DESKTOP\LiveCaptions.lnk"
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\LiveCaptions" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "言川"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "YanChuan"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\YanChuan.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${PAYLOAD_KIB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  CreateDirectory "$SMPROGRAMS\言川"
  CreateShortcut "$SMPROGRAMS\言川\言川.lnk" "$INSTDIR\YanChuan.exe" "" "$INSTDIR\Assets\AppIcon.ico"
  CreateShortcut "$SMPROGRAMS\言川\卸载言川.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "创建桌面快捷方式" DesktopSection
  CreateShortcut "$DESKTOP\言川.lnk" "$INSTDIR\YanChuan.exe" "" "$INSTDIR\Assets\AppIcon.ico"
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${FileExists} "$INSTDIR\yanchuan.runtime.json"
    MessageBox MB_OK|MB_ICONSTOP "找不到应用安装标记，停止卸载。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro CheckFileUnlocked "$INSTDIR\YanChuan.exe"
  !insertmacro CheckFileUnlocked "$INSTDIR\backend\python\python.exe"
  !insertmacro CheckFileUnlocked "$INSTDIR\backend\native\crispasr.dll"
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ; Only remove this installation's registration and shortcuts.
  ReadRegStr $1 HKCU "Software\LiveCaptions" "InstallDir"
  ${If} $1 == $INSTDIR
    Delete "$SMPROGRAMS\言川\言川.lnk"
    Delete "$SMPROGRAMS\言川\卸载言川.lnk"
    RMDir "$SMPROGRAMS\言川"
    Delete "$DESKTOP\言川.lnk"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
    DeleteRegValue HKCU "Software\LiveCaptions" "InstallDir"
    DeleteRegKey /ifempty HKCU "Software\LiveCaptions"
  ${EndIf}
  SetOutPath "$TEMP"
  ; Generated exact file list; never recursively delete application or user data.
  !insertmacro UninstallPayload
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
