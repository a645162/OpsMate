; OpsMate NSIS 安装包脚本
; 先执行：python packaging/build.py --all
; 再执行（Windows，已安装 NSIS）：makensis packaging\installer.nsi

Unicode true
!define APPNAME "OpsMate"
!define COMPANY "OpsMate"
!define VERSION "0.1.0"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\OpsMate"

Name "${APPNAME} ${VERSION}"
OutFile "..\dist\OpsMate-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
InstallDirRegKey HKLM "Software\OpsMate" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "OpsMate 图形界面（必装）" SEC_GUI
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "..\dist\OpsMate.dist\*.*"
  File "..\LICENSE"
  File "..\README.md"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\OpsMate.exe"
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\OpsMate.exe"
SectionEnd

Section "命令行工具 ops" SEC_CLI
  SetOutPath "$INSTDIR\cli"
  File /r "..\dist\ops.dist\*.*"

  ; 提供一个方便进入 CLI 目录的快捷方式，避免直接改系统 PATH
  FileOpen $0 "$INSTDIR\cli\ops.cmd" w
  FileWrite $0 "@echo off$\r$\n"
  FileWrite $0 "$\"%~dp0ops.exe$\" %*$\r$\n"
  FileClose $0
  CreateShortCut "$SMPROGRAMS\${APPNAME}\OpsMate CLI.lnk" "$INSTDIR\cli\ops.cmd"
SectionEnd

Section -Post
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\OpsMate" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\OpsMate CLI.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\OpsMate"
SectionEnd
