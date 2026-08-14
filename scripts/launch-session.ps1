# Starts the Jarvis server and the clap-wake listener together.
# Use this as the target of a Windows Task Scheduler entry ("At log on") for full clap-to-wake behavior.

$root = Split-Path -Parent $PSScriptRoot

Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "server.py" -WorkingDirectory $root
Start-Sleep -Seconds 2
Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "clap_trigger.py" -WorkingDirectory $root
