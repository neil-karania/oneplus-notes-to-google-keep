@echo off
setlocal
if "%~1"=="" (
  echo Usage: run_export_windows.bat path-to-oneplus-backup.zip
  exit /b 2
)
python "%~dp0oneplus_to_google_keep.py" "%~1" --export-dir "%~dp0verified_export"
endlocal
