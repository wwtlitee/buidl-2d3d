"""Desktop prerequisite regression checks; isolated PS fixtures, no real installs. v0.5.0a."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/bootstrap-runtime.ps1"


class DesktopBootstrapTests(unittest.TestCase):
    def fixture(self, *, action="ensure", versions=(None, "150.0.0.0"), status="Valid",
                publisher="Microsoft Corporation", exit_code=0, download_fails=False, elevated=False):
        shell = shutil.which("pwsh")
        self.assertIsNotNone(shell)
        with tempfile.TemporaryDirectory(prefix="build-something-desktop-bootstrap-") as directory:
            root = Path(directory)
            windowed = root / "runtime/desktop/.venv/Scripts/pythonw.exe"
            windowed.parent.mkdir(parents=True)
            windowed.write_bytes(b"isolated readiness fixture")
            script = root / "fixture.ps1"
            source = r"""$ErrorActionPreference='Stop'
$Root='__ROOT__'
$source='__SOURCE__'
$ast=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
foreach($function in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)){Invoke-Expression $function.Extent.Text}
$script:versions=ConvertFrom-Json -NoEnumerate '__VERSIONS__'
$script:probeCalls=0
$script:downloadCalls=0
$script:signatureCalls=0
$script:installCalls=@()
$script:launches=@()
$script:signatureStatus='__STATUS__'
$script:publisher='__PUBLISHER__'
$script:exitCode=__EXITCODE__
$script:downloadFails=__DOWNLOADFAILS__
$script:elevated=__ELEVATED__
function Get-DesktopRuntimeVersion {
    $index=[Math]::Min($script:probeCalls,$script:versions.Count-1)
    $script:probeCalls++
    return $script:versions[$index]
}
function Test-ElevatedProcess {return $script:elevated}
function Invoke-WebRequest {
    param([string]$Uri,[string]$OutFile,[switch]$UseBasicParsing,[int]$TimeoutSec)
    $script:downloadCalls++
    if($script:downloadFails){throw 'fixture network unavailable'}
    [IO.File]::WriteAllBytes($OutFile,[byte[]]@(77,90,0,0))
}
function Get-AuthenticodeSignature {
    param([string]$LiteralPath,[string]$FilePath)
    $script:signatureCalls++
    $certificate=[pscustomobject]@{Name=$script:publisher}
    $certificate|Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value {param($type,$issuer) return $this.Name}
    return [pscustomobject]@{Status=$script:signatureStatus;SignerCertificate=$certificate}
}
function Start-Process {
    param([string]$FilePath,[object[]]$ArgumentList,[string]$WorkingDirectory,[string]$WindowStyle,[switch]$Wait,[switch]$PassThru,[string]$Verb)
    $script:installCalls+=@([pscustomobject]@{file=$FilePath;arguments=@($ArgumentList);hidden=($WindowStyle -eq 'Hidden');wait=[bool]$Wait;verb=$Verb})
    return [pscustomobject]@{ExitCode=$script:exitCode}
}
function Test-RuntimePython {return $true}
function Test-RuntimeCommand {return $true}
function Capture-Shell {
    param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments)
    $script:launches+=@([string]($Arguments -join ' '))
    $global:LASTEXITCODE=0
}
$desktopConsole=Join-Path $Root 'runtime/desktop/.venv/Scripts/python.exe'
Set-Item -Path ('Function:\'+$desktopConsole) -Value {$global:LASTEXITCODE=1}
$corePython=Join-Path $Root 'runtime/core/.venv/Scripts/python.exe'
$desktopPython=Join-Path $Root 'runtime/desktop/.venv/Scripts/pythonw.exe'
$portableShell='Capture-Shell'
$globalShell=$null
$Mode='desktop'
$Port=7860
$PrepareOnly=('__ACTION__' -eq 'prepare')
$CheckOnly=('__ACTION__' -eq 'check')
$spec=[pscustomobject]@{webview2=[pscustomobject]@{url='https://go.microsoft.com/fwlink/p/?LinkId=2124703'}}
$saved=@{}
$bootstrapMutex=[Threading.Mutex]::new($false)
$locked=$false
$errorMessage=$null
$check=$null
try {
    if('__ACTION__' -eq 'ensure'){
        Ensure-DesktopRuntime $desktopConsole $spec.webview2
    }elseif('__ACTION__' -eq 'check'){
        $branch=$ast.Find({param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if($CheckOnly)')},$true)
        $check=Invoke-Expression $branch.Extent.Text|ConvertFrom-Json
    }else{
        $main=$ast.EndBlock.Statements|Where-Object {$_ -is [System.Management.Automation.Language.TryStatementAst]}|Select-Object -Last 1
        Invoke-Expression $main.Extent.Text
    }
}catch {$errorMessage=$_.Exception.Message}
$result=@{error=$errorMessage;probeCalls=$script:probeCalls;downloadCalls=$script:downloadCalls;signatureCalls=$script:signatureCalls;installCalls=@($script:installCalls);launches=@($script:launches);check=$check}
Write-Output ('RESULT:'+($result|ConvertTo-Json -Depth 8 -Compress))
"""
            replacements = {
                "__ROOT__": str(root).replace("'", "''"),
                "__SOURCE__": str(BOOTSTRAP).replace("'", "''"),
                "__VERSIONS__": json.dumps(list(versions)),
                "__STATUS__": status,
                "__PUBLISHER__": publisher,
                "__EXITCODE__": str(exit_code),
                "__DOWNLOADFAILS__": "$true" if download_fails else "$false",
                "__ELEVATED__": "$true" if elevated else "$false",
                "__ACTION__": action,
            }
            for needle, value in replacements.items():
                source = source.replace(needle, value)
            script.write_text(source, encoding="utf-8")
            completed = subprocess.run([shell, "-NoProfile", "-File", str(script)], capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            lines = [line[7:] for line in completed.stdout.splitlines() if line.startswith("RESULT:")]
            self.assertEqual(len(lines), 1, completed.stdout + completed.stderr)
            return json.loads(lines[0])

    def test_existing_runtime_does_not_download_or_install(self):
        result = self.fixture(versions=("150.0.0.0",))
        self.assertIsNone(result["error"], "Desktop runtime helper must accept the detected runtime")
        self.assertEqual(result["probeCalls"], 1)
        self.assertEqual(result["downloadCalls"], 0)
        self.assertEqual(result["installCalls"], [])

    def test_missing_runtime_installs_only_verified_microsoft_bootstrapper_and_reprobes(self):
        result = self.fixture()
        self.assertIsNone(result["error"])
        self.assertEqual((result["probeCalls"], result["downloadCalls"], result["signatureCalls"]), (2, 1, 1))
        install = result["installCalls"]
        self.assertEqual(len(install), 1)
        self.assertEqual(install[0]["arguments"], ["/silent", "/install"])
        self.assertTrue(install[0]["hidden"])
        self.assertTrue(install[0]["wait"])
        self.assertFalse(install[0]["verb"], "Desktop prerequisites must never request elevation")

    def test_invalid_signature_cannot_run_installer(self):
        for status in ("NotSigned", "HashMismatch"):
            with self.subTest(status=status):
                result = self.fixture(status=status)
                self.assertIn("signature", result["error"].lower())
                self.assertEqual(result["installCalls"], [])

    def test_valid_signature_from_other_publisher_cannot_run_installer(self):
        result = self.fixture(publisher="Other Publisher")
        self.assertIn("Microsoft", result["error"])
        self.assertEqual(result["installCalls"], [])

    def test_download_failure_cannot_run_installer(self):
        result = self.fixture(download_fails=True)
        self.assertIn("download", result["error"].lower())
        self.assertEqual(result["installCalls"], [])

    def test_installer_failure_does_not_launch_app(self):
        result = self.fixture(exit_code=9)
        self.assertIn("9", result["error"])
        self.assertEqual(result["launches"], [])

    def test_zero_installer_exit_without_runtime_fails_postinstall_check(self):
        result = self.fixture(versions=(None, None))
        self.assertIn("not detected", result["error"].lower())
        self.assertEqual(result["probeCalls"], 2)

    def test_elevated_missing_runtime_never_runs_machine_install(self):
        result = self.fixture(elevated=True)
        self.assertIn("administrator", result["error"].lower())
        self.assertEqual(result["downloadCalls"], 0)
        self.assertEqual(result["installCalls"], [])

    def test_check_only_requires_webview2_for_desktop_ready(self):
        result = self.fixture(action="check", versions=(None,))
        self.assertIsNone(result["error"])
        self.assertFalse(result["check"]["desktopReady"], "Python alone cannot make the desktop ready")
        self.assertFalse(result["check"]["webview2Ready"])
        self.assertEqual(result["downloadCalls"], 0)

    def test_prepare_only_prepares_webview2_without_launching_application(self):
        result = self.fixture(action="prepare")
        self.assertIsNone(result["error"])
        self.assertEqual((result["probeCalls"], len(result["installCalls"])), (2, 1))
        self.assertEqual(result["launches"], [])

    def test_missing_runtime_and_failed_install_never_dispatch_browser(self):
        result = self.fixture(action="start", exit_code=9)
        self.assertIsNotNone(result["error"], "Desktop preparation failure must stop startup")
        self.assertEqual(result["launches"], [], "Desktop mode must not open the user's browser")

    def test_installed_runtime_dispatches_desktop_entry_only(self):
        result = self.fixture(action="start", versions=("150.0.0.0",))
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["launches"]), 1)
        self.assertIn("start-desktop-factory.ps1", result["launches"][0])
        self.assertNotIn("start-visual-factory.ps1", result["launches"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
