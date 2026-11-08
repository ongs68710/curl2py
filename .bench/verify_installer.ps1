# Verify NSIS installer: type a parent dir that does not exist, press Enter,
# and check the app actually lands at <parent>\curl2py (auto-append works).
# All output in ASCII to avoid PS 5.1 ANSI/UTF-8 decoding issues.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ROOT   = 'C:\Users\Song\Desktop\1\main\08-exe'
$SETUP  = Join-Path $ROOT 'dist\curl2py-0.5.3-win-x64-setup.exe'
$parent = Join-Path $env:TEMP 'c2p-nsis-test'
$expect = Join-Path $parent 'curl2py'

if (Test-Path $parent) { Remove-Item -Recurse -Force $parent }
Write-Output "setup      : $SETUP"
Write-Output "parent dir : $parent (must NOT exist yet: $(-not (Test-Path $parent)))"

$AE   = [System.Windows.Automation.AutomationElement]
$TS   = [System.Windows.Automation.TreeScope]
$proc = Start-Process -FilePath $SETUP -PassThru

# wait for the top-level window
$win = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $cond = New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $proc.Id)
    $win  = $AE::RootElement.FindFirst($TS::Children, $cond)
    if ($win) { break }
}
if (-not $win) { Write-Output 'FAIL: installer window not found'; exit 1 }
Write-Output "window     : '$($win.Current.Name)'"

# bring to front
try { $win.SetFocus() } catch {}
Start-Sleep -Milliseconds 800

$editCond = New-Object System.Windows.Automation.PropertyCondition(
    $AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
$edit = $win.FindFirst($TS::Descendants, $editCond)
if (-not $edit) {
    # maybe an extra page is in front; press Enter and retry a few times
    for ($i = 0; $i -lt 4; $i++) {
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        Start-Sleep -Milliseconds 900
        $edit = $win.FindFirst($TS::Descendants, $editCond)
        if ($edit) { break }
    }
}
if (-not $edit) { Write-Output 'FAIL: directory edit box not found'; $proc | Stop-Process -Force; exit 1 }

$vp = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
Write-Output "default DIR: $($vp.Current.Value)"

# screenshot of the directory page (before typing)
try {
    $bmp = New-Object System.Drawing.Bitmap(1000, 700)
    $b  = $win.Current.BoundingRectangle
    $g  = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen([int]$b.X, [int]$b.Y, 0, 0, (New-Object System.Drawing.Size(1000, 700)))
    $bmp.Save((Join-Path $ROOT '.bench\nsis-dirpage.png'), [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
} catch { Write-Output "screenshot skipped: $($_.Exception.Message)" }

# type the parent path (no trailing backslash, like a user picking a folder)
$edit.SetFocus()
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait('^a')
Start-Sleep -Milliseconds 200
[System.Windows.Forms.SendKeys]::SendWait($parent)
Start-Sleep -Milliseconds 800
$typed = $vp.Current.Value
Write-Output "typed DIR  : $typed"

# Enter = Next; NSIS calls .onVerifyInstDir here, then may ask to create the folder
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 1200

# read back what the edit box holds after the verify callback
$after = ''
if (-not $edit.Current.IsOffscreen) { $after = $vp.Current.Value }
Write-Output "after Enter: $after"
Write-Output "parent exists now: $(Test-Path $parent)"

# drive the rest of the wizard with Enter, tolerate the "create folder?" prompt
$exe = Join-Path $expect 'curl2py.exe'
$done = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Path $exe) { $done = $true; break }
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    Start-Sleep -Milliseconds 1500
}

# final "Finish" page
for ($i = 0; $i -lt 3; $i++) {
    try { [System.Windows.Forms.SendKeys]::SendWait('{ENTER}') } catch {}
    Start-Sleep -Milliseconds 600
}

Write-Output "---- result ----"
Write-Output "installed exe exists : $(Test-Path $exe)"
if (Test-Path $expect) { Write-Output ("files in install dir : " + ((Get-ChildItem $expect -Name | Select-Object -First 8) -join ', ')) }
Write-Output "unrelated sibling    : $(Test-Path (Join-Path $parent 'resources'))"

# cleanup: silent uninstall, then remove leftovers
$un = Join-Path $expect 'Uninstall curl2py.exe'
if (Test-Path $un) {
    Write-Output 'silent uninstall ...'
    Start-Process -FilePath $un -ArgumentList '/S' -Wait
    Start-Sleep -Seconds 3
}
$proc.Refresh()
if (-not $proc.HasExited) { try { $proc | Stop-Process -Force } catch {} }
if (Test-Path $parent) {
    try { Remove-Item -Recurse -Force $parent } catch { Write-Output "cleanup warn: $($_.Exception.Message)" }
}
Write-Output "parent cleaned       : $(-not (Test-Path $parent))"
