# Verify NSIS installer auto-append via Win32 messages (NSIS pages are plain Win32
# dialogs; UIA does not always expose the MUI directory edit as an Edit element).
$ErrorActionPreference = 'Stop'

if (-not ('C2pWin32' -as [type])) {
Add-Type -Namespace '' -Name 'C2pWin32' -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr FindWindowExW(IntPtr parent, IntPtr after, string cls, string title);
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetClassNameW(IntPtr h, System.Text.StringBuilder sb, int max);
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder sb, int max);
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr SendMessageW(IntPtr h, uint msg, IntPtr wp, string lp);
[DllImport("user32.dll")]
public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
[DllImport("user32.dll")]
public static extern int GetDlgCtrlID(IntPtr h);
[DllImport("user32.dll")]
public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")]
public static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr lp);
[DllImport("user32.dll")]
public static extern bool SetForegroundWindow(IntPtr h);
public delegate bool EnumProc(IntPtr h, IntPtr lp);
'@
}

$WM_SETTEXT = 0x000C
$WM_GETTEXT = 0x000D
$BM_CLICK   = 0x00F5
$WM_COMMAND = 0x0111

$ROOT   = 'C:\Users\Song\Desktop\1\main\08-exe'
$SETUP  = Join-Path $ROOT 'dist\curl2py-0.5.3-win-x64-setup.exe'
$parent = Join-Path $env:TEMP 'c2p-nsis-test'
$expect = Join-Path $parent 'curl2py'
$exe    = Join-Path $expect 'curl2py.exe'

if (Test-Path $parent) { Remove-Item -Recurse -Force $parent }
Write-Output "parent dir : $parent  (exists before: $(Test-Path $parent))"

$proc = Start-Process -FilePath $SETUP -PassThru

# wait for main window handle
$hwnd = [IntPtr]::Zero
for ($i = 0; $i -lt 80; $i++) {
    Start-Sleep -Milliseconds 300
    $proc.Refresh()
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) { $hwnd = $proc.MainWindowHandle; break }
}
if ($hwnd -eq [IntPtr]::Zero) { Write-Output 'FAIL: no main window'; exit 1 }
[void][C2pWin32]::SetForegroundWindow($hwnd)
Write-Output "main hwnd  : $hwnd"
Start-Sleep -Milliseconds 1200

function Get-Children([IntPtr]$parentH) {
    $list = New-Object System.Collections.ArrayList
    $cb = [C2pWin32+EnumProc]{
        param($h, $lp)
        [void]$list.Add($h); return $true
    }
    [void][C2pWin32]::EnumChildWindows($parentH, $cb, [IntPtr]::Zero)
    return $list
}

function Get-ClsText([IntPtr]$h) {
    $cb = New-Object System.Text.StringBuilder 256
    [void][C2pWin32]::GetClassNameW($h, $cb, 256)
    $cls = $cb.ToString()
    $tb = New-Object System.Text.StringBuilder 1024
    [void][C2pWin32]::GetWindowTextW($h, $tb, 1024)
    return @{ hwnd = $h; cls = $cls; text = $tb.ToString(); id = [C2pWin32]::GetDlgCtrlID($h) }
}

$kids = Get-Children $hwnd | ForEach-Object { Get-ClsText $_ }
Write-Output "child controls:"
foreach ($k in $kids) { Write-Output ("   cls={0,-12} id={1,-5} text='{2}'" -f $k.cls, $k.id, $k.text) }

$edit = $kids | Where-Object { $_.cls -eq 'Edit' } | Select-Object -First 1
if (-not $edit) {
    # advance one page (welcome page) then re-enumerate
    $nextBtn = $kids | Where-Object { $_.cls -eq 'Button' -and $_.id -eq 1 } | Select-Object -First 1
    if ($nextBtn) { [void][C2pWin32]::SendMessage($nextBtn.hwnd, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero) }
    Start-Sleep -Milliseconds 1500
    $kids = Get-Children $hwnd | ForEach-Object { Get-ClsText $_ }
    Write-Output "child controls (after next):"
    foreach ($k in $kids) { Write-Output ("   cls={0,-12} id={1,-5} text='{2}'" -f $k.cls, $k.id, $k.text) }
    $edit = $kids | Where-Object { $_.cls -eq 'Edit' } | Select-Object -First 1
}
if (-not $edit) { Write-Output 'FAIL: no Edit control'; $proc | Stop-Process -Force; exit 1 }

$before = $edit.text
Write-Output "default DIR: $before"

# set the parent path
[void][C2pWin32]::SendMessageW($edit.hwnd, $WM_SETTEXT, [IntPtr]::Zero, $parent)
Start-Sleep -Milliseconds 500
$tb = New-Object System.Text.StringBuilder 1024
[void][C2pWin32]::GetWindowTextW($edit.hwnd, $tb, 1024)
Write-Output "typed DIR  : $($tb.ToString())"

# click Next -> NSIS runs .onVerifyInstDir, then may prompt to create the folder
$next = $kids | Where-Object { $_.cls -eq 'Button' -and $_.id -eq 1 } | Select-Object -First 1
if (-not $next) { Write-Output 'FAIL: no Next button (id=1)'; $proc | Stop-Process -Force; exit 1 }
[void][C2pWin32]::SendMessage($next.hwnd, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
Start-Sleep -Milliseconds 1500

$tb2 = New-Object System.Text.StringBuilder 1024
[void][C2pWin32]::GetWindowTextW($edit.hwnd, $tb2, 1024)
Write-Output "after Next : '$($tb2.ToString())'"
Write-Output "parent created before install: $(Test-Path $parent)"

# install loop: keep clicking id=1 (install / next / finish) and answer folder prompts
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Path $exe) { break }
    Start-Sleep -Milliseconds 900
    $kids = Get-Children $hwnd | ForEach-Object { Get-ClsText $_ }
    $btn = $kids | Where-Object { $_.cls -eq 'Button' -and $_.id -eq 1 } | Select-Object -First 1
    if ($btn) {
        [void][C2pWin32]::SendMessage($btn.hwnd, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
    } else {
        # likely a modal message box appeared (folder create confirm) - answer Yes (6)
        $msgBox = [C2pWin32]::FindWindowExW([IntPtr]::Zero, [IntPtr]::Zero, '#32770', $null)
        if ($msgBox -ne [IntPtr]::Zero) {
            $yes = [C2pWin32]::FindWindowExW($msgBox, [IntPtr]::Zero, 'Button', $null)
            # id 6 = IDYES on a standard Yes/No box
            $mbKids = Get-Children $msgBox | ForEach-Object { Get-ClsText $_ }
            $y = $mbKids | Where-Object { $_.cls -eq 'Button' -and $_.id -eq 6 } | Select-Object -First 1
            if (-not $y) { $y = $mbKids | Where-Object { $_.cls -eq 'Button' } | Select-Object -First 1 }
            if ($y) {
                Write-Output "message box hit: '$($y.text)' -> click"
                [void][C2pWin32]::SendMessage($y.hwnd, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
            }
        }
    }
}

Start-Sleep -Seconds 2
Write-Output "---- result ----"
Write-Output "installed exe exists : $(Test-Path $exe)"
if (Test-Path $expect) {
    Write-Output ("install dir listing  : " + ((Get-ChildItem $expect -Name | Select-Object -First 10) -join ', '))
}
Write-Output "parent has app subdir only (no loose copies): $(Test-Path (Join-Path $parent 'resources'))"

# cleanup
$un = Join-Path $expect 'Uninstall curl2py.exe'
if (Test-Path $un) {
    Write-Output 'silent uninstall ...'
    Start-Process -FilePath $un -ArgumentList '/S' -Wait
    Start-Sleep -Seconds 4
}
$proc.Refresh()
if (-not $proc.HasExited) { try { $proc | Stop-Process -Force } catch {} }
if (Test-Path $parent) { try { Remove-Item -Recurse -Force $parent } catch { Write-Output "cleanup warn: $($_.Exception.Message)" } }
Write-Output "parent cleaned       : $(-not (Test-Path $parent))"
