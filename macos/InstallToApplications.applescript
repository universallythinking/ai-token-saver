-- Double-clickable installer (lives at project root as "Install to Applications.app").
-- Runs install-to-applications.command from the repo root.

on run
	set appPosix to POSIX path of (path to me)
	if appPosix ends with "/" then set appPosix to text 1 thru -2 of appPosix
	set root to do shell script "/usr/bin/dirname " & quoted form of appPosix
	set cmd to root & "/install-to-applications.command"
	try
		do shell script "/bin/bash " & quoted form of cmd
		display notification "Token Saver is in Applications. Open it from Launchpad or the Dock." with title "Install complete"
	on error errMsg number errNum
		display dialog "Install failed:" & return & return & errMsg buttons {"OK"} with icon stop
		error errMsg number errNum
	end try
end run
