' Двойной клик — запускает AI Vision из живого кода с автоперезапуском при
' сохранении правок (dev_launcher.py), без окна консоли — даже мелькающего.
' Какой Python взять для самого окна (с библиотеками), выбирает dev_launcher.py.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
script = """" & root & "\dev_launcher.py"""

' 1. pyw — Python Launcher без консоли (ставится вместе с Python)
On Error Resume Next
sh.Run "pyw " & script, 0, False
If Err.Number = 0 Then WScript.Quit
Err.Clear
On Error Goto 0

' 2. нет pyw — pythonw.exe из стандартной папки установки, свежий первым
py = ""
base = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python"
If fso.FolderExists(base) Then
    For Each d In fso.GetFolder(base).SubFolders
        If fso.FileExists(d.Path & "\pythonw.exe") And d.Name > pyName Then
            py = d.Path & "\pythonw.exe"
            pyName = d.Name
        End If
    Next
End If
If py = "" Then py = "pythonw"

On Error Resume Next
sh.Run """" & py & """ " & script, 0, False
If Err.Number <> 0 Then
    MsgBox "Python not found. Install Python 3.13 and the AI Vision libraries:" & vbCrLf & _
           "py -3.13 -m pip install -r requirements.txt", vbCritical, "AI Vision"
End If
