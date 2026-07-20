@echo off
setlocal
if "%~1"=="" (
  echo Usage: run_import_windows.bat path-to-oneplus-backup.zip
  exit /b 2
)
python "%~dp0oneplus_to_google_keep.py" "%~1" --export-dir "%~dp0verified_export" --profile-dir "%~dp0.oneplus_keep_browser_profile" --state-file "%~dp0oneplus_keep_import_state.json" --import-to-keep
endlocal
