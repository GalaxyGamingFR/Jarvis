# Starts the Jarvis server and the "Hey Jarvis" wake-word listener together.
# Use this as the target of a Windows Task Scheduler entry ("At log on") for full wake-to-voice behavior.

$root = Split-Path -Parent $PSScriptRoot

Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "server.py" -WorkingDirectory $root
Start-Sleep -Seconds 2
Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "wake_word_trigger.py" -WorkingDirectory $root
