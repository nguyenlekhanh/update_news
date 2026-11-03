@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Chua co moi truong Python. Dang tao .venv voi Python 3.12...
  py -3.12 -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
echo.
echo Kiem tra Ollama va ComfyUI...
curl -s -m 3 http://127.0.0.1:11434/api/tags >nul || echo  [!] Ollama chua chay - hay chay "ollama serve"
curl -s -m 3 http://127.0.0.1:8188/system_stats >nul || echo  [!] ComfyUI chua chay - hay chay run_nvidia_gpu.bat cua ComfyUI Portable
echo.
echo Dang mo AI Viral News and Facebook Content Studio...
".venv\Scripts\python.exe" app.py
goto :eof

:error
echo.
echo Cai dat that bai. Kiem tra Python 3.12 va ket noi mang.
pause
