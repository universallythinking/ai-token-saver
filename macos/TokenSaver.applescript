-- Stay-open applet: Dock quit runs stop.sh and tears down proxy processes.
property resPath : ""

on resourcePath(name)
	try
		return POSIX path of (path to resource name)
	on error
		return ""
	end try
end resourcePath

on run
	set startSh to my resourcePath("start.sh")
	if startSh is "" then
		display dialog "Token Saver is missing start.sh. Re-run macos/install-to-applications.sh from the repo." buttons {"OK"} with icon stop
		return
	end if
	try
		do shell script "bash " & quoted form of startSh
	on error errMsg number errNum
		if errNum is not 0 then
			display dialog "Token Saver failed to start:" & return & return & errMsg buttons {"OK"} with icon stop
		end if
	end try
end run

on quit
	set stopSh to my resourcePath("stop.sh")
	if stopSh is not "" then
		try
			do shell script "bash " & quoted form of stopSh
		end try
	end if
	continue quit
end quit
