@echo off
rem Launcher for the F1 -> Solidpixels Tetra bridge.
rem   .\f1              menus, then live
rem   .\f1 --devices    list your devices
rem   .\f1 --demo       demo data
rem Uses the Windows "py" launcher when present, otherwise python on PATH.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 bridge.py %*
) else (
    python bridge.py %*
)
