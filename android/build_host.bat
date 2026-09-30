@echo off
rem Builds the C++ core and its PC tests with MSVC (x64) and the SDK's CMake + Ninja.
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set CMAKE=%LOCALAPPDATA%\Android\Sdk\cmake\3.31.6\bin
set PATH=%CMAKE%;%PATH%
cmake -S core -B build\host -G Ninja -DCMAKE_BUILD_TYPE=Release -DCHORDCHART_BUILD_TESTS=ON ^
  -DCHORDCHART_ORT_INCLUDE=%~dp0third_party\onnxruntime-win-x64\include ^
  -DCHORDCHART_ORT_LIBRARY=%~dp0third_party\onnxruntime-win-x64\lib\onnxruntime.lib || exit /b 1
cmake --build build\host || exit /b 1
copy /y third_party\onnxruntime-win-x64\lib\onnxruntime.dll build\host\ >nul
