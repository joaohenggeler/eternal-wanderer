Local $delay = 20

If $CmdLine[0] > 0 Then
	$delay = $CmdLine[1]
EndIf

Opt("WinWaitDelay", $delay)

While True
	Local $handle = WinWait("[REGEXPTITLE:Security Warning|セキュリティ警告; REGEXPCLASS:SunAwtDialog|#32770]")
	WinActivate($handle)
	If WinWaitActive($handle, "", 2) Then
		Send("+{TAB}{ENTER}")
		WinWaitClose($handle, "", 2)
	EndIf
WEnd