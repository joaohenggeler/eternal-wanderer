Local $delay = 20

If $CmdLine[0] > 0 Then
	$delay = $CmdLine[1]
EndIf

Opt("WinWaitDelay", $delay)

While True
	Local $handle = WinWait("[TITLE:Shockwave; CLASS:#32770]")
	WinClose($handle)
WEnd