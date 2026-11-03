@echo off
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (start "" .venv\Scripts\pythonw.exe scripts\installer.py) else (start "" pythonw.exe scripts\installer.py)
