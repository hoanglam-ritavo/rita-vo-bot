[CmdletBinding(DefaultParameterSetName = 'Preview')]
param(
    [Parameter(ParameterSetName = 'Apply')][switch]$Apply,
    [Parameter(ParameterSetName = 'Rollback')][switch]$Rollback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$root = [IO.Path]::GetFullPath((Join-Path $project 'assets'))
$manifestPath = Join-Path $project 'assets-rename-manifest.json'
$journalPath = Join-Path $project 'assets-rename-journal.json'

function Save-Json($Value, [string]$Path) {
    $json = ConvertTo-Json -InputObject $Value -Depth 10
    [IO.File]::WriteAllText($Path, $json, (New-Object Text.UTF8Encoding($false)))
}

function Resolve-AssetPath([string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative)) { throw "Absolute path rejected: $Relative" }
    $full = [IO.Path]::GetFullPath((Join-Path $root $Relative))
    if (-not $full.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path outside assets: $Relative"
    }
    return $full
}

function Get-Inventory {
    $rootItem = Get-Item -LiteralPath $root
    if (-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'assets must be a regular directory.'
    }
    # Enumerate one level at a time: never follow a reparse point.
    $queue = New-Object 'Collections.Generic.Queue[string]'
    $queue.Enqueue($root)
    while ($queue.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $queue.Dequeue() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Link rejected: $($item.FullName)" }
            $relative = $item.FullName.Substring($root.Length + 1)
            $null = Resolve-AssetPath $relative
            $item
            if ($item.PSIsContainer) { $queue.Enqueue($item.FullName) }
        }
    }
}

function Convert-Name([string]$Name, [bool]$IsFile) {
    $extension = ''
    if ($IsFile) {
        $extension = [IO.Path]::GetExtension($Name).ToLowerInvariant()
        $Name = [IO.Path]::GetFileNameWithoutExtension($Name)
        if ($extension -and $extension -notmatch '^\.[a-z0-9]+$') { throw "Unsupported extension: $extension" }
    }
    $value = $Name.ToLowerInvariant().Replace([char]0x0111, 'd').Replace([char]0x00f0, 'd').Normalize([Text.NormalizationForm]::FormKD)
    $builder = New-Object Text.StringBuilder
    foreach ($ch in $value.ToCharArray()) {
        if ([Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch) -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$builder.Append($ch)
        }
    }
    $stem = [regex]::Replace($builder.ToString(), '[^a-z0-9]+', '-').Trim('-')
    if (-not $stem -or $stem -match '^(con|prn|aux|nul|com[0-9]|lpt[0-9])$') { throw "Invalid target name: $Name" }
    return $stem + $extension
}

function Assert-Snapshot($Entries, [string]$Side) {
    $items = @(Get-Inventory)
    if ($items.Count -ne @($Entries).Count) { throw 'Inventory changed; refusing to continue.' }
    $actual = @{}
    foreach ($item in $items) { $actual[$item.FullName.Substring($root.Length + 1)] = $item }
    foreach ($entry in $Entries) {
        $relative = [string]$entry.$Side
        $null = Resolve-AssetPath $relative
        if (-not $actual.ContainsKey($relative)) { throw "Missing path: $relative" }
        $item = $actual[$relative]
        if ($item.FullName.Substring($root.Length + 1) -cne $relative) { throw "Path casing changed: $relative" }
        if ($item.PSIsContainer -ne ($entry.kind -eq 'directory')) { throw "Type changed: $relative" }
        if (-not $item.PSIsContainer) {
            if ($item.Length -ne $entry.size -or (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash -ne $entry.sha256) {
                throw "Content changed: $relative"
            }
        }
    }
}

function Rename-Relative([string]$From, [string]$To) {
    $source = Resolve-AssetPath $From
    $target = Resolve-AssetPath $To
    if ((Split-Path $source -Parent) -cne (Split-Path $target -Parent)) { throw 'Only same-parent renames are allowed.' }
    if (Test-Path -LiteralPath $target) { throw "Destination already exists: $To" }
    for ($attempt = 0; ; $attempt++) {
        try {
            Rename-Item -LiteralPath $source -NewName (Split-Path $target -Leaf) -Force
            break
        } catch {
            if ($attempt -ge 4 -or (Test-Path -LiteralPath $target)) { throw }
            # Explorer or antivirus may briefly hold a directory after a child rename.
            Start-Sleep -Milliseconds 250
        }
    }
}

if ($Rollback) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $journal = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.version -ne 1 -or $journal.id -ne $manifest.id -or $journal.status -ne 'applied') {
        throw 'Rollback requires a matching, successfully applied manifest and journal.'
    }
    Assert-Snapshot $manifest.entries 'new'
    # Simulate all reverse operations first, including descendants of renamed directories.
    $paths = @($manifest.entries | ForEach-Object { [string]$_.new })
    $ops = @($journal.operations)
    [array]::Reverse($ops)
    foreach ($op in $ops) {
        $null = Resolve-AssetPath $op.from
        $null = Resolve-AssetPath $op.to
        if ($paths -notcontains $op.to -or $paths -contains $op.from) { throw 'Rollback path conflict.' }
        $paths = @($paths | ForEach-Object {
            if ($_ -ieq $op.to) { $op.from }
            elseif ($_.StartsWith($op.to + '\', [StringComparison]::OrdinalIgnoreCase)) { $op.from + $_.Substring($op.to.Length) }
            else { $_ }
        })
    }
    if (@(Compare-Object $paths @($manifest.entries | ForEach-Object { $_.old })).Count) { throw 'Invalid rollback journal.' }
    $completed = New-Object Collections.ArrayList
    try {
        foreach ($op in $ops) {
            Rename-Relative $op.to $op.from
            [void]$completed.Add($op)
            $journal.status = 'rolling-back'
            $journal | Add-Member -NotePropertyName rollbackCompleted -NotePropertyValue $completed.Count -Force
            Save-Json $journal $journalPath
        }
        Assert-Snapshot $manifest.entries 'old'
        $journal.status = 'rolled-back'
        Save-Json $journal $journalPath
        Write-Output "Rollback verified: $($manifest.entries.Count) entries."
    } catch {
        $failure = $_
        for ($i = $completed.Count - 1; $i -ge 0; $i--) { Rename-Relative $completed[$i].from $completed[$i].to }
        $journal.status = 'applied'
        Save-Json $journal $journalPath
        throw $failure
    }
    return
}

$items = @(Get-Inventory | Sort-Object FullName)
$entries = @($items | ForEach-Object {
    $relative = $_.FullName.Substring($root.Length + 1)
    $parts = $relative.Split('\')
    for ($i = 0; $i -lt $parts.Length; $i++) {
        $parts[$i] = Convert-Name $parts[$i] ($i -eq $parts.Length - 1 -and -not $_.PSIsContainer)
    }
    $new = $parts -join '\'
    $null = Resolve-AssetPath $new
    [pscustomobject]@{
        old = $relative; new = $new
        kind = $(if ($_.PSIsContainer) { 'directory' } else { 'file' })
        size = $(if ($_.PSIsContainer) { $null } else { $_.Length })
        sha256 = $(if ($_.PSIsContainer -or -not $Apply) { $null } else { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash })
    }
})
$seen = @{}
foreach ($entry in $entries) {
    if ($seen.ContainsKey($entry.new)) { throw "Target collision: $($entry.old) and $($seen[$entry.new])" }
    $seen[$entry.new] = $entry.old
}
$changes = @($entries | Where-Object { (Split-Path $_.old -Leaf) -cne (Split-Path $_.new -Leaf) })
Write-Output "Entries: $($entries.Count); names to change: $($changes.Count)"
if (-not $Apply) {
    $changes | Select-Object old, new
    return
}
if (-not $changes.Count) { return }
if ((Test-Path -LiteralPath $manifestPath) -or (Test-Path -LiteralPath $journalPath)) {
    throw 'Existing manifest/journal must be archived before another migration.'
}
$manifest = [pscustomobject]@{ version = 1; id = [guid]::NewGuid().ToString(); createdUtc = [DateTime]::UtcNow.ToString('o'); entries = $entries }
$journal = [pscustomobject]@{ id = $manifest.id; status = 'applying'; operations = @() }
Save-Json $manifest $manifestPath
Save-Json $journal $journalPath
Assert-Snapshot $entries 'old'
$completed = New-Object Collections.ArrayList
try {
    foreach ($entry in ($changes | Sort-Object @{Expression = { $_.old.Split('\').Length }; Descending = $true}, old)) {
        $parent = Split-Path $entry.old -Parent
        $leaf = Split-Path $entry.new -Leaf
        $target = $(if ($parent) { Join-Path $parent $leaf } else { $leaf })
        $source = $entry.old
        $steps = @()
        if ($source -ieq $target) {
            $tempLeaf = 'asset-rename-temp-' + [guid]::NewGuid().ToString('N')
            $temp = $(if ($parent) { Join-Path $parent $tempLeaf } else { $tempLeaf })
            $steps += [pscustomobject]@{ from = $source; to = $temp }
            $source = $temp
        }
        $steps += [pscustomobject]@{ from = $source; to = $target }
        foreach ($op in $steps) {
            Rename-Relative $op.from $op.to
            [void]$completed.Add($op)
            $journal.operations = @($completed.ToArray())
            Save-Json $journal $journalPath
        }
    }
    Assert-Snapshot $entries 'new'
    $journal.status = 'applied'
    Save-Json $journal $journalPath
    Write-Output "Applied and SHA-256 verified: $($changes.Count) names."
} catch {
    $failure = $_
    for ($i = $completed.Count - 1; $i -ge 0; $i--) {
        Rename-Relative $completed[$i].to $completed[$i].from
        $completed.RemoveAt($i)
        $journal.operations = @($completed.ToArray())
        Save-Json $journal $journalPath
    }
    Assert-Snapshot $entries 'old'
    $journal.status = 'rolled-back'
    Save-Json $journal $journalPath
    throw $failure
}
