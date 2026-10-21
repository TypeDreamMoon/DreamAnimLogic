@echo off
rem Build the DalLiveHost editor target with the installed UE 5.8.   build.cmd [log file]
set LOG=%~1
if "%LOG%"=="" set LOG=G:\DCC\scratch\w4_logs\build.log
if not exist "%~dp1" mkdir "%~dp1"
"I:\UnrealEngine\UE_5.8\Engine\Build\BatchFiles\Build.bat" DalLiveHostEditor Win64 Development -Project="G:\DCC\DalLiveHost\DalLiveHost.uproject" -WaitMutex -NoHotReloadFromIDE > "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"
