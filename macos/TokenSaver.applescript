-- Stay-open applet:
--   first launch → start proxy + open dashboard
--   Dock click while running → focus existing dashboard tab; else open a new tab
--   Dock Quit → stop.sh

property dashHosts : {"127.0.0.1:8081", "localhost:8081", "tokensaver.local"}

on resourcePath(name)
	try
		return POSIX path of (path to resource name)
	on error
		return ""
	end try
end resourcePath

on dashboardUp()
	try
		do shell script "/usr/bin/curl -fsS -m 1 http://127.0.0.1:8081/api/stats >/dev/null"
		return true
	on error
		return false
	end try
end dashboardUp

on urlIsDashboard(tabUrl)
	if tabUrl is missing value or tabUrl is "" then return false
	repeat with h in dashHosts
		if tabUrl contains (h as text) then return true
	end repeat
	return false
end urlIsDashboard

on runningBrowserNames()
	set names to {}
	try
		tell application "System Events"
			set procs to name of every process whose background only is false
		end tell
		set candidates to {"Google Chrome", "Chrome", "Chromium", "Safari", "Arc", "Brave Browser", "Microsoft Edge", "Dia", "Vivaldi", "Opera"}
		repeat with c in candidates
			set cName to c as text
			if procs contains cName then set end of names to cName
		end repeat
	end try
	return names
end runningBrowserNames

on focusChromeFamily(appName)
	-- "using terms from" needed so osacompile knows Chrome tab vocabulary
	-- when the application name is a variable.
	using terms from application "Google Chrome"
		try
			tell application appName
				repeat with w in windows
					try
						set tabCount to count of tabs of w
						repeat with i from 1 to tabCount
							try
								set tabUrl to URL of tab i of w
								if my urlIsDashboard(tabUrl) then
									set active tab index of w to i
									set index of w to 1
									activate
									return "tab"
								end if
							end try
						end repeat
					end try
				end repeat
			end tell
		end try
	end using terms from
	return "none"
end focusChromeFamily

on focusSafari()
	try
		tell application "Safari"
			repeat with w in windows
				try
					set tabCount to count of tabs of w
					repeat with i from 1 to tabCount
						try
							set tabUrl to URL of tab i of w
							if my urlIsDashboard(tabUrl) then
								set current tab of w to tab i of w
								set index of w to 1
								activate
								return "tab"
							end if
						end try
					end repeat
				end try
			end repeat
		end tell
	end try
	return "none"
end focusSafari

on focusDashboard()
	-- 1) Focus existing dashboard tab (keep its path).
	-- 2) No match → open a new tab.
	set browsers to my runningBrowserNames()
	repeat with b in browsers
		set appName to b as text
		if appName is "Safari" then
			if my focusSafari() is "tab" then return
		else
			if my focusChromeFamily(appName) is "tab" then return
		end if
	end repeat
	try
		do shell script "open " & quoted form of "http://127.0.0.1:8081/"
	end try
end focusDashboard

on startProxy()
	set startSh to my resourcePath("start.sh")
	if startSh is "" then
		display dialog "Token Saver is missing start.sh. Double-click Install to Applications in the project folder." buttons {"OK"} with icon stop
		return
	end if
	try
		do shell script "bash " & quoted form of startSh
	on error errMsg number errNum
		if errNum is not 0 then
			display dialog "Token Saver failed to start:" & return & return & errMsg buttons {"OK"} with icon stop
		end if
	end try
end startProxy

on run
	my startProxy()
end run

on reopen
	if my dashboardUp() then
		my focusDashboard()
	else
		my startProxy()
	end if
end reopen

on quit
	set stopSh to my resourcePath("stop.sh")
	if stopSh is not "" then
		try
			do shell script "bash " & quoted form of stopSh
		end try
	end if
	continue quit
end quit
