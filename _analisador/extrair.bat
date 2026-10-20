@echo off
setlocal
chcp 65001 >nul
title SisExtractDados_Dintel
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo  Python 3.8 ou superior nao foi encontrado.
    echo  Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

rem Duplo clique: abre a interface. Arrastar arquivos/pastas sobre este .bat: abre ja com eles carregados.
%PY% "%~dp0extrair.py" %*
if errorlevel 1 pause
endlocal
