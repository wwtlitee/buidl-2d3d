' Source checkout entry, v0.9.1b / Unclecow. Keep this file ASCII.
Option Explicit
Dim fso, shell, root, powershell, script, command, prepareOnly, result
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
powershell = fso.BuildPath(shell.ExpandEnvironmentStrings("%SystemRoot%"), "System32\WindowsPowerShell\v1.0\powershell.exe")
script = fso.BuildPath(root, "scripts\start-source-factory.ps1")
prepareOnly = False
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments.Count <> 1 Then Fail "Unsupported launcher arguments."
    If LCase(WScript.Arguments(0)) <> "--prepare-only" Then Fail "Unsupported launcher argument."
    prepareOnly = True
End If
If Not fso.FileExists(powershell) Then Fail "Windows PowerShell was not found: " & powershell
If Not fso.FileExists(script) Then Fail "Required startup script is missing: " & script
command = Quote(powershell) & " -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " & Quote(script)
If prepareOnly Then command = command & " -PrepareOnly"
On Error Resume Next
result = shell.Run(command, 0, True)
If Err.Number <> 0 Then
    Dim failure
    failure = "Could not start Windows PowerShell: " & Err.Description
    On Error GoTo 0
    Fail failure
End If
On Error GoTo 0
WScript.Quit result

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Sub Fail(message)
    Dim logRoot, logFile, stream
    On Error Resume Next
    logRoot = fso.BuildPath(shell.ExpandEnvironmentStrings("%TEMP%"), "BuildSomething-source-launcher")
    If Not fso.FolderExists(logRoot) Then fso.CreateFolder logRoot
    logFile = fso.BuildPath(logRoot, "source-entry-errors.log")
    Set stream = fso.OpenTextFile(logFile, 8, True, -1)
    stream.WriteLine CStr(Now) & " | " & root & " | " & message
    stream.Close
    On Error GoTo 0
    If Not prepareOnly Then MsgBox message & vbCrLf & vbCrLf & "Details: " & logFile, vbOKOnly + vbCritical, "Build Something - Startup failed"
    WScript.Quit 1
End Sub
