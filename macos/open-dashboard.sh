#!/bin/bash
# CLI helper: focus existing dashboard tab, else activate browser, else open URL.
# Dock clicks use TokenSaver.applescript directly (better Automation permissions).
set -euo pipefail

DIRECT_URL="${TOKENSAVER_DASH_URL:-http://127.0.0.1:8081/}"
# Escape for embedding inside AppleScript double-quoted string.
AS_URL="${DIRECT_URL//\\/\\\\}"
AS_URL="${AS_URL//\"/\\\"}"

osascript - "$AS_URL" <<'APPLESCRIPT'
on run argv
	set fallbackUrl to item 1 of argv
	set dashHosts to {"127.0.0.1:8081", "localhost:8081", "tokensaver.local"}
	
	set browsers to {}
	try
		tell application "System Events"
			set procs to name of every process whose background only is false
		end tell
		set candidates to {"Google Chrome", "Chrome", "Chromium", "Safari", "Arc", "Brave Browser", "Microsoft Edge", "Dia", "Vivaldi", "Opera", "Firefox"}
		repeat with c in candidates
			set cName to c as text
			if procs contains cName then set end of browsers to cName
		end repeat
	end try
	
	repeat with appName in browsers
		set appName to appName as text
		if appName is "Safari" then
			try
				tell application "Safari"
					repeat with w in windows
						set tabCount to count of tabs of w
						repeat with i from 1 to tabCount
							set tabUrl to URL of tab i of w
							repeat with h in dashHosts
								if tabUrl contains (h as text) then
									set current tab of w to tab i of w
									set index of w to 1
									activate
									return
								end if
							end repeat
						end repeat
					end repeat
				end tell
			end try
		else if appName is not "Firefox" then
			try
				tell application appName
					repeat with w in windows
						set tabCount to count of tabs of w
						repeat with i from 1 to tabCount
							set tabUrl to URL of tab i of w
							repeat with h in dashHosts
								if tabUrl contains (h as text) then
									set active tab index of w to i
									set index of w to 1
									activate
									return
								end if
							end repeat
						end repeat
					end repeat
				end tell
			end try
		end if
	end repeat
	
	-- No matching tab → open a new one.
	do shell script "open " & quoted form of fallbackUrl
end run
APPLESCRIPT
