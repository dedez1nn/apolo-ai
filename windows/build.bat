@echo off
REM Gera windows\dist\Apolo.exe (ícone da bandeja que abre "apolo review").
REM Rodar numa máquina Windows de verdade -- PyInstaller não faz cross-compile
REM de outro SO pra Windows.

cd /d "%~dp0"

python -m venv .buildvenv
call .buildvenv\Scripts\activate.bat
pip install -r requirements.txt

pyinstaller --onefile --windowed --icon=apolo.ico --name Apolo apolo_tray.py

echo.
echo Pronto: windows\dist\Apolo.exe
echo O script procura apolo.ico do lado do .exe em tempo de execucao (nao fica
echo embutido no binario) -- copie os DOIS, Apolo.exe e apolo.ico, juntos para
echo a raiz do projeto (onde fica a pasta .venv) e execute com duplo clique.
