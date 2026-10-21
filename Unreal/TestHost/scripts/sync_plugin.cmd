@echo off
rem Copy the DalLive plugin of this repository into the test project (never touches Binaries / Intermediate).
rem   sync_plugin.cmd [project dir]      default G:\DCC\DalLiveHost
set PROJECT=%~1
if "%PROJECT%"=="" set PROJECT=G:\DCC\DalLiveHost
set PLUGIN=%~dp0..\..\DalLive
robocopy "%PLUGIN%" "%PROJECT%\Plugins\DalLive" /MIR /XD Binaries Intermediate __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% LEQ 7 (exit /b 0) else (exit /b %ERRORLEVEL%)
