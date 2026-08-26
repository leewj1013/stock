Option Explicit

Dim shell, scriptPath, command, index, exitCode

If WScript.Arguments.Count < 1 Then
    WScript.Quit 2
End If

Set shell = CreateObject("WScript.Shell")
scriptPath = WScript.Arguments(0)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptPath & """"

For index = 1 To WScript.Arguments.Count - 1
    command = command & " " & WScript.Arguments(index)
Next

exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
