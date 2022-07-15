Local $delay = 20

If $CmdLine[0] > 0 Then
	$delay = $CmdLine[1]
EndIf

Opt("WinWaitDelay", $delay)

While True
	Local $handle = WinWaitActive("[REGEXPTITLE:Security Warning|セキュリティ警告; REGEXPCLASS:SunAwtDialog|#32770]")
	Send("+{TAB}{ENTER}")
	WinWaitClose($handle)
WEnd