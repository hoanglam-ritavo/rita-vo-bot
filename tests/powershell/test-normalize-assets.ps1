$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../scripts/normalize-assets.ps1'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('assets-rename-test-' + [guid]::NewGuid().ToString('N'))

function New-Fixture([string]$Name) {
    $path = Join-Path $testRoot $Name
    $null = New-Item -ItemType Directory -Path (Join-Path $path 'scripts') -Force
    $null = New-Item -ItemType Directory -Path (Join-Path $path 'assets')
    Copy-Item -LiteralPath $source -Destination (Join-Path $path 'scripts/normalize-assets.ps1')
    return $path
}
function Run-Tool([string]$Path, [string]$Mode = '') {
    $argsList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Path 'scripts/normalize-assets.ps1'))
    if ($Mode) { $argsList += $Mode }
    $output = & powershell @argsList 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
    return ($output | Out-String)
}
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Fails([scriptblock]$Action) {
    $failed = $false
    try { & $Action | Out-Null } catch { $failed = $true }
    Assert $failed 'Expected operation to be rejected.'
}

$fixture = New-Fixture 'roundtrip'
$unicodeName = '01. ' + [char]0x0110 + [char]0x1ec1 + ' & Test'
$nested = Join-Path $fixture ('assets/' + $unicodeName + '/EMPTY')
$null = New-Item -ItemType Directory -Path $nested -Force
$file = Join-Path (Split-Path $nested -Parent) ('Q' + [char]0x00d0 + '  (Video) .MP4')
[IO.File]::WriteAllText($file, 'unchanged payload')
[IO.File]::WriteAllText((Join-Path $fixture 'assets/UPPER.TXT'), 'case only')
$hiddenPath = Join-Path $fixture 'assets/Thumbs.db'
[IO.File]::WriteAllText($hiddenPath, 'hidden test payload')
[IO.File]::SetAttributes($hiddenPath, ([IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System))
$preview = Run-Tool $fixture
Assert (-not (Test-Path (Join-Path $fixture 'assets-rename-manifest.json'))) 'Preview wrote a manifest.'
Assert (Test-Path -LiteralPath $file) 'Preview renamed a file.'
$null = Run-Tool $fixture '-Apply'
Assert (Test-Path (Join-Path $fixture 'assets/01-de-test/qd-video.mp4')) 'Unicode normalization failed.'
Assert (Test-Path (Join-Path $fixture 'assets/01-de-test/empty')) 'Empty directory missing.'
Assert ((Run-Tool $fixture) -match 'names to change: 0') 'Normalization is not idempotent.'
[IO.File]::WriteAllText((Join-Path $fixture 'assets/extra.txt'), 'new file')
Assert-Fails { Run-Tool $fixture '-Rollback' }
# Move the deliberate conflict outside assets; preserve all test artifacts.
Move-Item -LiteralPath (Join-Path $fixture 'assets/extra.txt') -Destination (Join-Path $fixture 'extra.txt')
$null = Run-Tool $fixture '-Rollback'
Assert (Test-Path -LiteralPath $file) 'Original Unicode path was not restored.'
Assert ([IO.File]::ReadAllText($file) -eq 'unchanged payload') 'Content changed.'
Assert (Test-Path (Join-Path $fixture 'assets/UPPER.TXT')) 'Case-only rollback failed.'

$collision = New-Fixture 'collision'
[IO.File]::WriteAllText((Join-Path $collision 'assets/a b.txt'), 'one')
[IO.File]::WriteAllText((Join-Path $collision 'assets/a_b.txt'), 'two')
Assert-Fails { Run-Tool $collision '-Apply' }
Assert (Test-Path (Join-Path $collision 'assets/a b.txt')) 'Collision partially applied.'
Assert (-not (Test-Path (Join-Path $collision 'assets-rename-manifest.json'))) 'Collision wrote manifest.'

foreach ($name in @('!!!.txt', ('C' + [char]0x00d3 + 'N.txt'))) {
    $invalid = New-Fixture ([guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText((Join-Path $invalid ('assets/' + $name)), 'invalid target')
    Assert-Fails { Run-Tool $invalid '-Apply' }
}
$failureFixture = New-Fixture 'automatic-rollback'
[IO.File]::WriteAllText((Join-Path $failureFixture 'assets/A A.txt'), 'first')
$lockedPath = Join-Path $failureFixture 'assets/B B.txt'
[IO.File]::WriteAllText($lockedPath, 'locked')
$handle = [IO.File]::Open($lockedPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try { Assert-Fails { Run-Tool $failureFixture '-Apply' } } finally { $handle.Dispose() }
Assert (Test-Path (Join-Path $failureFixture 'assets/A A.txt')) 'Automatic rollback did not restore first file.'
Assert (-not (Test-Path (Join-Path $failureFixture 'assets/a-a.txt'))) 'Automatic rollback left renamed file.'
$failureJournal = Get-Content (Join-Path $failureFixture 'assets-rename-journal.json') -Raw | ConvertFrom-Json
Assert ($failureJournal.status -eq 'rolled-back') 'Automatic rollback was not recorded.'
Write-Output "PASS: preview, Unicode, punctuation, casing, nested/empty directories, idempotence, collision, invalid names, rollback conflict and roundtrip. Fixtures: $testRoot"
