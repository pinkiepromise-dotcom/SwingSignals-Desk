' Starts SwingSignals Desk with no console window (used by the auto-start shortcut)
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run """" & dir & "\.venv\Scripts\pythonw.exe"" """ & dir & "\app\server.py""", 0, False
