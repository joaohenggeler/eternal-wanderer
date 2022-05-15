Local $delay = 20

If $CmdLine[0] > 0 Then
	$delay = $CmdLine[1]
EndIf

Opt("WinWaitDelay", $delay)

While True
	Local $handle = WinWaitActive("[TITLE:Security Warning; CLASS:SunAwtDialog]")
	Send("+{TAB}{ENTER}")
	WinWaitClose($handle)
WEnd