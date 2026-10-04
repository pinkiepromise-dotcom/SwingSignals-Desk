@echo off
REM Adds SwingSignals Desk to Windows startup so it runs every time you log in.
cd /d "%~dp0"
if not exist .venv ( echo Run start_windows.bat once first. & pause & exit /b )
set LNK="%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SwingSignals Desk.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%LNK%);$s.TargetPath='wscript.exe';$s.Arguments='\"%~dp0start_windows_hidden.vbs\"';$s.WorkingDirectory='%~dp0';$s.Save()"
echo Auto-start installed. SwingSignals Desk will start when you log in.
echo Dashboard: http://127.0.0.1:8765
pause
