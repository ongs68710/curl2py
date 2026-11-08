; curl2py 桌面版安装器自定义脚本
; 目标：① 选定目录后自动补 app 子文件夹（无需手动新建）② 卸载可选删数据
; 注意：本文件在 StrContains.nsh 之前被 include，不能用 ${StrContains}，
; 后缀判断用原生 StrCpy/StrCmp 实现。

; 目录页顶部说明：告诉用户直接选上级目录即可
!ifndef MUI_DIRECTORYPAGE_TEXT_TOP
  !define MUI_DIRECTORYPAGE_TEXT_TOP "Setup 会自动在所选目录下创建 ${APP_FILENAME} 子文件夹，无需手动新建。$\r$\n单击 [浏览(B)...] 选择上级目录（例如 D:\bianchengruanjan），安装路径会自动补全为 D:\bianchengruanjan\${APP_FILENAME}。"
!endif

; 目录变化回调：浏览或手输目录后，若结尾还不是 app 文件夹名，自动补上。
; 补完后结尾即匹配，再次触发时条件不成立，不会重复追加。
Function .onVerifyInstDir
  StrCmp $INSTDIR "" done
  StrCpy $R1 "\${APP_FILENAME}"
  StrLen $R2 "$R1"
  StrLen $R0 "$INSTDIR"
  IntCmp $R0 $R2 append append check
  check:
    StrCpy $R3 "$INSTDIR" "" -$R2
    StrCmp $R3 $R1 done
  append:
    StrCpy $INSTDIR "$INSTDIR\${APP_FILENAME}"
  done:
FunctionEnd

!macro customHeader
  ; 安装器标题栏文案
  BrandingText "curl2py"
!macroend

; 安装阶段不做额外动作（源码与运行时随安装包释放，数据只在首次运行时创建）
!macro customInstall
!macroend

; 卸载时询问是否一并删除用户数据（收藏夹库、日志、运行时缓存）
!macro customUnInstall
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "是否同时删除 curl2py 的本地数据？$\r$\n$\r$\n包含：Cookie 收藏夹库（vault.db）、后端日志、收藏夹运行时目录。$\r$\n选择「否」将保留这些数据，重新安装后可继续使用。" \
    /SD IDNO IDNO skipDataCleanup

  SetShellVarContext current
  RMDir /r "$APPDATA\curl2py"
  RMDir /r "$LOCALAPPDATA\curl2py"

  skipDataCleanup:
!macroend
