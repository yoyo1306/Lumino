Set sh = CreateObject("Wscript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & base & "\start.bat""", 0, False
