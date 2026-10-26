$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    try {
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] < (3, 15) and sys.maxsize > 2**32 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-Python {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        foreach ($launcherArgument in @('-3.14', '-3.13', '-3.12')) {
            if (Test-PythonCandidate -Executable $launcher.Source -PrefixArguments @($launcherArgument)) {
                return [pscustomobject]@{
                    Executable = $launcher.Source
                    PrefixArguments = @($launcherArgument)
                    DisplayName = "py $launcherArgument"
                }
            }
        }
    }

    $commands = @(
        Get-Command python, python3 -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -and $_.Source -notmatch '[\\/]WindowsApps[\\/]' }
    )
    foreach ($command in $commands) {
        if (Test-PythonCandidate -Executable $command.Source) {
            return [pscustomobject]@{
                Executable = $command.Source
                PrefixArguments = @()
                DisplayName = $command.Source
            }
        }
    }

    throw '未找到可用的 64 位 Python 3.12～3.14。已依次检查 py -3.14、py -3.13、py -3.12 以及 PATH 中非 WindowsApps 的 python/python3；32 位或版本超出范围的解释器不会用于构建。'
}

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)][string]$Step,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "[$Step]"
    $executable = $script:PythonCommand.Executable
    $prefixArguments = $script:PythonCommand.PrefixArguments
    & $executable @prefixArguments @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 失败，退出码：$LASTEXITCODE"
    }
}

$script:PythonCommand = Find-Python
Write-Host "使用 Python：$($script:PythonCommand.DisplayName)"

Invoke-PythonStep -Step '安装开发与打包依赖' -Arguments @('-m', 'pip', 'install', '-r', 'requirements-dev.txt')
Invoke-PythonStep -Step '运行完整测试' -Arguments @('-m', 'pytest', '-v')
Invoke-PythonStep -Step '构建文件夹便携版' -Arguments @('-m', 'PyInstaller', '--noconfirm', '--clean', 'seal_generator.spec')

$ExecutablePath = Join-Path $ProjectRoot 'dist\印章生成器\印章生成器.exe'
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "构建产物不存在：$ExecutablePath"
}

Write-Host '[验证打包程序与内置字体]'
$smokeProcess = Start-Process -FilePath $ExecutablePath -ArgumentList '--smoke-test' -WindowStyle Hidden -Wait -PassThru
Write-Host "打包程序冒烟测试退出码：$($smokeProcess.ExitCode)"
if ($smokeProcess.ExitCode -ne 0) {
    throw "打包程序冒烟测试失败，退出码：$($smokeProcess.ExitCode)"
}

Write-Host '[验证打包程序图形界面启动]'
$guiSmokeProcess = Start-Process -FilePath $ExecutablePath -ArgumentList '--gui-smoke-test' -WindowStyle Hidden -Wait -PassThru
Write-Host "打包程序图形界面冒烟测试退出码：$($guiSmokeProcess.ExitCode)"
if ($guiSmokeProcess.ExitCode -ne 0) {
    throw "打包程序图形界面冒烟测试失败，退出码：$($guiSmokeProcess.ExitCode)"
}

Write-Host '构建完成：dist\印章生成器\印章生成器.exe'
