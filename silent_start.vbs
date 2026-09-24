Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory

' Check if venv pythonw exists, otherwise use system pythonw
Dim fso, pythonExe
Set fso = CreateObject("Scripting.FileSystemObject")

If fso.FileExists(strPath & "\venv\Scripts\pythonw.exe") Then
    pythonExe = """" & strPath & "\venv\Scripts\pythonw.exe"""
ElseIf fso.FileExists(strPath & "\venv\Scripts\python.exe") Then
    pythonExe = """" & strPath & "\venv\Scripts\python.exe"""
Else
    pythonExe = "pythonw"
End If

' Run tray_app.py with hidden window (0)
WshShell.Run pythonExe & " """ & strPath & "\tray_app.py""", 0, False
